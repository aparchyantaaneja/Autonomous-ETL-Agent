"""FastAPI application — main entry point for the ETL Agent web service."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader

from src.agents.code_generator import CodeGeneratorAgent
from src.agents.pr_agent import PRAgent
from src.agents.task_breakdown import TaskBreakdownAgent, UserStory
from src.agents.test_generator import TestGeneratorAgent
from src.api.models import (
    DeployRequest,
    HealthResponse,
    RunDetail,
    RunSummary,
    StoryRequest,
    SubmitResponse,
)
from src.config import settings
from src.gcp.dataproc_client import DataprocClient
from src.gcp.storage_client import GCSClient
from src.llm.claude_client import build_claude_client
from src.orchestrator.graph import AgentState, ETLAgentGraph, RunStatus

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# In-memory run store (replace with Firestore/Postgres in production)
# ---------------------------------------------------------------------------

_runs: dict[str, AgentState] = {}
_run_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Dependency injection — shared clients
# ---------------------------------------------------------------------------


def _build_graph(
    deploy_to_dataproc: bool = False,
    generate_airflow_dag: bool = False,
) -> ETLAgentGraph:
    llm = build_claude_client(settings)
    gcs = GCSClient(
        project_id=settings.gcp_project_id,
        artifacts_bucket=settings.gcs_artifacts_bucket,
    )
    dataproc = DataprocClient(
        project_id=settings.gcp_project_id,
        region=settings.dataproc_region,
        cluster_name=settings.dataproc_cluster_name,
        staging_bucket=settings.dataproc_staging_bucket,
    ) if deploy_to_dataproc else None

    return ETLAgentGraph(
        task_agent=TaskBreakdownAgent(llm),
        code_agent=CodeGeneratorAgent(llm),
        test_agent=TestGeneratorAgent(llm),
        pr_agent=PRAgent(
            github_token=settings.github_token,
            repo_owner=settings.github_repo_owner,
            repo_name=settings.github_repo_name,
            base_branch=settings.github_base_branch,
            llm=llm,
        ),
        gcs=gcs,
        dataproc=dataproc,
        deploy_to_dataproc=deploy_to_dataproc,
        generate_airflow_dag=generate_airflow_dag,
    )


# ---------------------------------------------------------------------------
# API key authentication
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)


def require_api_key(api_key: str = Security(_api_key_header)) -> str:
    if api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return api_key


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    import logging
    import structlog

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
    )
    logger.info("etl_agent_started", host=settings.api_host, port=settings.api_port)
    yield
    logger.info("etl_agent_shutting_down")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Autonomous ETL Agent",
    description=(
        "Multi-agent system that transforms DevOps user stories into "
        "tested, PR-ready PySpark pipelines deployed on GCP."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check() -> HealthResponse:
    """Health check — no authentication required."""
    return HealthResponse()


@app.post(
    "/stories",
    response_model=SubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Stories"],
    dependencies=[Depends(require_api_key)],
)
async def submit_story(
    request: StoryRequest,
    background_tasks: BackgroundTasks,
) -> SubmitResponse:
    """Submit a user story for autonomous ETL pipeline generation.

    The request is accepted immediately and processed asynchronously.
    Poll `/runs/{run_id}` to track progress.
    """
    run_id = str(uuid.uuid4())
    story = UserStory(
        id=request.id,
        title=request.title,
        description=request.description,
        acceptance_criteria=request.acceptance_criteria,
        labels=request.labels,
    )
    initial_state = AgentState(run_id=run_id, story=story)

    async with _run_lock:
        _runs[run_id] = initial_state

    background_tasks.add_task(
        _run_pipeline,
        run_id=run_id,
        story=story,
        deploy_to_dataproc=request.deploy_to_dataproc,
        generate_airflow_dag=request.generate_airflow_dag,
    )

    logger.info("story_submitted", run_id=run_id, story_id=request.id)
    return SubmitResponse(run_id=run_id)


@app.get(
    "/runs",
    response_model=list[RunSummary],
    tags=["Runs"],
    dependencies=[Depends(require_api_key)],
)
async def list_runs(limit: int = 50) -> list[RunSummary]:
    """List all pipeline runs, newest first."""
    runs = sorted(_runs.values(), key=lambda r: r.started_at, reverse=True)
    return [_to_summary(r) for r in runs[:limit]]


@app.get(
    "/runs/{run_id}",
    response_model=RunDetail,
    tags=["Runs"],
    dependencies=[Depends(require_api_key)],
)
async def get_run(run_id: str) -> RunDetail:
    """Get full details for a specific pipeline run."""
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )
    return _to_detail(run)


@app.post(
    "/deploy/{run_id}",
    response_model=RunSummary,
    tags=["Deploy"],
    dependencies=[Depends(require_api_key)],
)
async def deploy_run(
    run_id: str,
    request: DeployRequest,
    background_tasks: BackgroundTasks,
) -> RunSummary:
    """Trigger Dataproc deployment for a completed run that wasn't deployed initially."""
    run = _runs.get(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    if run.status != RunStatus.DONE:
        raise HTTPException(
            status_code=409,
            detail=f"Run is in state {run.status}; must be DONE to deploy",
        )
    if not run.artifact_uris.get("script"):
        raise HTTPException(status_code=409, detail="No script artifact found for this run")

    background_tasks.add_task(_deploy_to_dataproc, run_id=run_id)
    return _to_summary(run)


# ---------------------------------------------------------------------------
# Background task implementations
# ---------------------------------------------------------------------------


async def _run_pipeline(
    run_id: str,
    story: UserStory,
    deploy_to_dataproc: bool,
    generate_airflow_dag: bool,
) -> None:
    loop = asyncio.get_event_loop()
    graph = _build_graph(deploy_to_dataproc, generate_airflow_dag)
    try:
        result: AgentState = await loop.run_in_executor(
            None, graph.run, story
        )
        async with _run_lock:
            _runs[run_id] = result
    except Exception as exc:
        logger.error("pipeline_background_error", run_id=run_id, error=str(exc))
        async with _run_lock:
            run = _runs.get(run_id)
            if run:
                run.status = RunStatus.FAILED
                run.error_message = str(exc)


async def _deploy_to_dataproc(run_id: str) -> None:
    run = _runs.get(run_id)
    if not run or not run.artifact_uris.get("script"):
        return
    loop = asyncio.get_event_loop()
    dataproc = DataprocClient(
        project_id=settings.gcp_project_id,
        region=settings.dataproc_region,
        cluster_name=settings.dataproc_cluster_name,
        staging_bucket=settings.dataproc_staging_bucket,
    )
    try:
        job_id = f"etl-{run_id[:8]}"
        dataproc_job_id = await loop.run_in_executor(
            None,
            dataproc.submit_pyspark_job,
            run.artifact_uris["script"],
            job_id,
        )
        result = await loop.run_in_executor(None, dataproc.wait_for_job, dataproc_job_id)
        async with _run_lock:
            run.dataproc_job_id = dataproc_job_id
            run.dataproc_job_state = result.state.value
    except Exception as exc:
        logger.error("deploy_background_error", run_id=run_id, error=str(exc))


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _to_summary(run: AgentState) -> RunSummary:
    story = run.story
    return RunSummary(
        run_id=run.run_id,
        story_id=story.id if story else "",
        story_title=story.title if story else "",
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        current_step=run.current_step,
        started_at=run.started_at,
        completed_at=run.completed_at,
        pr_url=run.pr_result["pr_url"] if isinstance(run.pr_result, dict) else (run.pr_result.pr_url if run.pr_result else None),
        pr_number=run.pr_result["pr_number"] if isinstance(run.pr_result, dict) else (run.pr_result.pr_number if run.pr_result else None),
        dataproc_job_id=run.dataproc_job_id,
        dataproc_job_state=run.dataproc_job_state,
        airflow_dag_id=run.airflow_dag_id,
        error_message=run.error_message,
    )


def _to_detail(run: AgentState) -> RunDetail:
    summary = _to_summary(run)
    return RunDetail(
        **summary.model_dump(),
        etl_spec=run.etl_spec.model_dump() if run.etl_spec else None,
        artifact_uris=run.artifact_uris,
    )
