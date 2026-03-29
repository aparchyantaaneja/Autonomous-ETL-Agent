"""FastAPI request/response models for the ETL Agent API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class StoryRequest(BaseModel):
    """Payload to submit a new user story for processing."""

    id: str = Field(..., examples=["STORY-42"])
    title: str = Field(..., examples=["Aggregate monthly revenue by region"])
    description: str = Field(
        ...,
        examples=[
            "Join orders with customers, clean nulls, aggregate monthly revenue by geo "
            "region, and upsert results to the Delta Lake campaign table."
        ],
    )
    acceptance_criteria: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    deploy_to_dataproc: bool = False
    generate_airflow_dag: bool = False


class DeployRequest(BaseModel):
    """Request to trigger Dataproc deployment for a completed run."""

    notify_on_complete: bool = False


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class RunSummary(BaseModel):
    run_id: str
    story_id: str
    story_title: str
    status: str
    current_step: str
    started_at: str
    completed_at: Optional[str] = None
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    dataproc_job_id: Optional[str] = None
    dataproc_job_state: Optional[str] = None
    airflow_dag_id: Optional[str] = None
    error_message: Optional[str] = None


class RunDetail(RunSummary):
    etl_spec: Optional[dict[str, Any]] = None
    artifact_uris: dict[str, str] = Field(default_factory=dict)


class SubmitResponse(BaseModel):
    run_id: str
    message: str = "Story submitted for processing"
    status: str = "PENDING"


class HealthResponse(BaseModel):
    status: str = "ok"
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    version: str = "1.0.0"
