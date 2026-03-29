"""LangGraph-based multi-agent orchestrator.

State machine:
  story_intake
      │
      ▼
  task_breakdown  ──(error)──► handle_error
      │
      ▼
  code_generation ──(error)──► handle_error
      │
      ▼
  test_generation ──(error)──► handle_error
      │
      ▼
  upload_artifacts
      │
      ▼
  create_pr       ──(error)──► handle_error
      │
      ▼
  deploy_dataproc (optional)
      │
      ▼
  generate_dag    (optional)
      │
      ▼
  DONE
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import structlog
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from src.agents.code_generator import CodeGeneratorAgent, GeneratedCode
from src.agents.pr_agent import PRAgent, PRResult
from src.agents.task_breakdown import ETLSpec, TaskBreakdownAgent, UserStory
from src.agents.test_generator import GeneratedTests, TestGeneratorAgent
from src.gcp.dataproc_client import DataprocClient, JobState
from src.gcp.storage_client import GCSClient

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Agent State
# ---------------------------------------------------------------------------


class RunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class AgentState(BaseModel):
    """Shared mutable state passed between all graph nodes."""

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    status: RunStatus = RunStatus.PENDING
    current_step: str = "story_intake"

    # Inputs
    story: Optional[UserStory] = None

    # Intermediate outputs
    etl_spec: Optional[ETLSpec] = None
    generated_code: Optional[GeneratedCode] = None
    generated_tests: Optional[GeneratedTests] = None
    artifact_uris: dict[str, str] = Field(default_factory=dict)

    # Final outputs
    pr_result: Optional[PRResult] = None
    dataproc_job_id: Optional[str] = None
    dataproc_job_state: Optional[str] = None
    airflow_dag_id: Optional[str] = None

    # Error tracking
    error_step: Optional[str] = None
    error_message: Optional[str] = None
    completed_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


class ETLAgentGraph:
    """Constructs and runs the LangGraph state machine for the ETL agent pipeline.

    Usage:
        graph = ETLAgentGraph(task_agent, code_agent, test_agent, pr_agent, gcs, dataproc)
        result = graph.run(story)
    """

    def __init__(
        self,
        task_agent: TaskBreakdownAgent,
        code_agent: CodeGeneratorAgent,
        test_agent: TestGeneratorAgent,
        pr_agent: PRAgent,
        gcs: GCSClient,
        dataproc: DataprocClient | None = None,
        deploy_to_dataproc: bool = False,
        generate_airflow_dag: bool = False,
    ) -> None:
        self._task_agent = task_agent
        self._code_agent = code_agent
        self._test_agent = test_agent
        self._pr_agent = pr_agent
        self._gcs = gcs
        self._dataproc = dataproc
        self._deploy = deploy_to_dataproc
        self._gen_dag = generate_airflow_dag
        self._graph = self._build_graph()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self, story: UserStory) -> AgentState:
        """Execute the full ETL pipeline for a user story."""
        initial_state = AgentState(story=story, status=RunStatus.RUNNING)
        logger.info("run_start", run_id=initial_state.run_id, story_id=story.id)

        final_state_dict = self._graph.invoke(initial_state.model_dump())
        final_state = AgentState.model_validate(final_state_dict)

        logger.info(
            "run_complete",
            run_id=final_state.run_id,
            status=final_state.status,
            pr_url=final_state.pr_result.pr_url if final_state.pr_result else None,
        )
        return final_state

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------

    def _build_graph(self) -> Any:
        builder = StateGraph(dict)

        # Register nodes
        builder.add_node("task_breakdown", self._node_task_breakdown)
        builder.add_node("code_generation", self._node_code_generation)
        builder.add_node("test_generation", self._node_test_generation)
        builder.add_node("upload_artifacts", self._node_upload_artifacts)
        builder.add_node("create_pr", self._node_create_pr)
        builder.add_node("handle_error", self._node_handle_error)

        if self._deploy:
            builder.add_node("deploy_dataproc", self._node_deploy_dataproc)
        if self._gen_dag:
            builder.add_node("generate_dag", self._node_generate_dag)

        # Entry point
        builder.set_entry_point("task_breakdown")

        # Edges with error routing
        for node, next_node in [
            ("task_breakdown", "code_generation"),
            ("code_generation", "test_generation"),
            ("test_generation", "upload_artifacts"),
            ("upload_artifacts", "create_pr"),
        ]:
            builder.add_conditional_edges(
                node,
                self._route_or_error,
                {next_node: next_node, "handle_error": "handle_error"},
            )

        if self._deploy and self._gen_dag:
            builder.add_conditional_edges(
                "create_pr",
                self._route_or_error,
                {"deploy_dataproc": "deploy_dataproc", "handle_error": "handle_error"},
            )
            builder.add_conditional_edges(
                "deploy_dataproc",
                self._route_or_error,
                {"generate_dag": "generate_dag", "handle_error": "handle_error"},
            )
            builder.add_edge("generate_dag", END)
        elif self._deploy:
            builder.add_conditional_edges(
                "create_pr",
                self._route_or_error,
                {"deploy_dataproc": "deploy_dataproc", "handle_error": "handle_error"},
            )
            builder.add_edge("deploy_dataproc", END)
        elif self._gen_dag:
            builder.add_conditional_edges(
                "create_pr",
                self._route_or_error,
                {"generate_dag": "generate_dag", "handle_error": "handle_error"},
            )
            builder.add_edge("generate_dag", END)
        else:
            builder.add_conditional_edges(
                "create_pr",
                self._route_or_error,
                {END: END, "handle_error": "handle_error"},
            )

        builder.add_edge("handle_error", END)

        return builder.compile()

    # ------------------------------------------------------------------
    # Routing helper
    # ------------------------------------------------------------------

    @staticmethod
    def _route_or_error(state: dict) -> str:
        if state.get("error_message"):
            return "handle_error"
        # Return the next node name based on current step
        routing = {
            "task_breakdown": "code_generation",
            "code_generation": "test_generation",
            "test_generation": "upload_artifacts",
            "upload_artifacts": "create_pr",
            "create_pr": "deploy_dataproc",
            "deploy_dataproc": "generate_dag",
            "generate_dag": END,
        }
        current = state.get("current_step", "")
        return routing.get(current, END)

    # ------------------------------------------------------------------
    # Graph nodes
    # ------------------------------------------------------------------

    def _node_task_breakdown(self, state: dict) -> dict:
        logger.info("node_task_breakdown", run_id=state["run_id"])
        try:
            story = UserStory.model_validate(state["story"])
            spec = self._task_agent.parse_story(story)
            state["etl_spec"] = spec.model_dump()
            state["current_step"] = "task_breakdown"
        except Exception as exc:
            state = self._set_error(state, "task_breakdown", str(exc))
        return state

    def _node_code_generation(self, state: dict) -> dict:
        logger.info("node_code_generation", run_id=state["run_id"])
        try:
            spec = ETLSpec.model_validate(state["etl_spec"])
            code = self._code_agent.generate(spec)
            state["generated_code"] = {
                "script": code.script,
                "notebook": code.notebook,
                "filename": code.filename,
                "notebook_filename": code.notebook_filename,
            }
            state["current_step"] = "code_generation"
        except Exception as exc:
            state = self._set_error(state, "code_generation", str(exc))
        return state

    def _node_test_generation(self, state: dict) -> dict:
        logger.info("node_test_generation", run_id=state["run_id"])
        try:
            spec = ETLSpec.model_validate(state["etl_spec"])
            code_data = state["generated_code"]
            tests = self._test_agent.generate(code_data["script"], spec)
            state["generated_tests"] = {
                "conftest": tests.conftest,
                "test_file": tests.test_file,
                "fixtures": tests.fixtures,
                "conftest_filename": tests.conftest_filename,
                "test_filename": tests.test_filename,
                "fixtures_filename": tests.fixtures_filename,
            }
            state["current_step"] = "test_generation"
        except Exception as exc:
            state = self._set_error(state, "test_generation", str(exc))
        return state

    def _node_upload_artifacts(self, state: dict) -> dict:
        logger.info("node_upload_artifacts", run_id=state["run_id"])
        try:
            run_id = state["run_id"]
            code_data = state["generated_code"]
            tests_data = state["generated_tests"]
            import json

            uris: dict[str, str] = {}
            uris["script"] = self._gcs.upload_artifact(
                code_data["script"], run_id, code_data["filename"], "text/x-python"
            )
            uris["notebook"] = self._gcs.upload_artifact(
                code_data["notebook"], run_id, code_data["notebook_filename"], "application/json"
            )
            uris["test_file"] = self._gcs.upload_artifact(
                tests_data["test_file"], run_id, tests_data["test_filename"], "text/x-python"
            )
            uris["conftest"] = self._gcs.upload_artifact(
                tests_data["conftest"], run_id, "conftest.py", "text/x-python"
            )
            uris["fixtures"] = self._gcs.upload_artifact(
                json.dumps(tests_data["fixtures"], indent=2),
                run_id,
                tests_data["fixtures_filename"],
                "application/json",
            )
            state["artifact_uris"] = uris
            state["current_step"] = "upload_artifacts"
        except Exception as exc:
            state = self._set_error(state, "upload_artifacts", str(exc))
        return state

    def _node_create_pr(self, state: dict) -> dict:
        logger.info("node_create_pr", run_id=state["run_id"])
        try:
            from src.agents.code_generator import GeneratedCode
            from src.agents.test_generator import GeneratedTests

            spec = ETLSpec.model_validate(state["etl_spec"])
            cd = state["generated_code"]
            td = state["generated_tests"]

            code = GeneratedCode(
                script=cd["script"],
                notebook=cd["notebook"],
                filename=cd["filename"],
                notebook_filename=cd["notebook_filename"],
            )
            tests = GeneratedTests(
                conftest=td["conftest"],
                test_file=td["test_file"],
                fixtures=td["fixtures"],
                conftest_filename=td["conftest_filename"],
                test_filename=td["test_filename"],
                fixtures_filename=td["fixtures_filename"],
            )

            pr = self._pr_agent.create_pr(spec, code, tests)
            state["pr_result"] = {
                "pr_url": pr.pr_url,
                "pr_number": pr.pr_number,
                "branch_name": pr.branch_name,
                "commit_sha": pr.commit_sha,
            }
            state["current_step"] = "create_pr"
            state["status"] = RunStatus.DONE.value
            state["completed_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as exc:
            state = self._set_error(state, "create_pr", str(exc))
        return state

    def _node_deploy_dataproc(self, state: dict) -> dict:
        logger.info("node_deploy_dataproc", run_id=state["run_id"])
        if not self._dataproc:
            logger.warning("dataproc_client_not_configured")
            return state
        try:
            script_uri = state["artifact_uris"]["script"]
            run_id = state["run_id"]
            job_id = f"etl-{run_id[:8]}"
            dataproc_job_id = self._dataproc.submit_pyspark_job(
                script_gcs_uri=script_uri,
                job_id=job_id,
            )
            result = self._dataproc.wait_for_job(dataproc_job_id)
            state["dataproc_job_id"] = dataproc_job_id
            state["dataproc_job_state"] = result.state.value
            state["current_step"] = "deploy_dataproc"
        except Exception as exc:
            state = self._set_error(state, "deploy_dataproc", str(exc))
        return state

    def _node_generate_dag(self, state: dict) -> dict:
        logger.info("node_generate_dag", run_id=state["run_id"])
        try:
            from orchestration.airflow_dag_template import generate_dag
            spec = ETLSpec.model_validate(state["etl_spec"])
            dag_id = f"etl_{spec.story_id.lower().replace('-', '_')}"
            dag_code = generate_dag(
                dag_id=dag_id,
                script_gcs_uri=state["artifact_uris"].get("script", ""),
                story_id=spec.story_id,
            )
            self._gcs.upload_artifact(dag_code, state["run_id"], f"{dag_id}.py", "text/x-python")
            state["airflow_dag_id"] = dag_id
            state["current_step"] = "generate_dag"
        except Exception as exc:
            state = self._set_error(state, "generate_dag", str(exc))
        return state

    def _node_handle_error(self, state: dict) -> dict:
        logger.error(
            "pipeline_error",
            run_id=state.get("run_id"),
            step=state.get("error_step"),
            message=state.get("error_message"),
        )
        state["status"] = RunStatus.FAILED.value
        state["completed_at"] = datetime.now(timezone.utc).isoformat()
        return state

    @staticmethod
    def _set_error(state: dict, step: str, message: str) -> dict:
        state["error_step"] = step
        state["error_message"] = message
        return state
