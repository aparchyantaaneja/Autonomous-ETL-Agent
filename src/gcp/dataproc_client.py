"""GCP Dataproc client — submit, monitor, and retrieve PySpark jobs."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

import structlog
from google.cloud import dataproc_v1
from google.cloud.dataproc_v1.types import Job, JobStatus

logger = structlog.get_logger(__name__)


class JobState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


@dataclass
class DataprocJobResult:
    job_id: str
    state: JobState
    output_uri: str | None
    error_message: str | None
    duration_s: float


_TERMINAL_STATES = {
    JobStatus.State.DONE,
    JobStatus.State.ERROR,
    JobStatus.State.CANCELLED,
}


class DataprocClient:
    """Submit and monitor PySpark jobs on GCP Dataproc."""

    def __init__(
        self,
        project_id: str,
        region: str,
        cluster_name: str,
        staging_bucket: str,
    ) -> None:
        self.project_id = project_id
        self.region = region
        self.cluster_name = cluster_name
        self.staging_bucket = staging_bucket
        self._jobs_client = dataproc_v1.JobControllerClient(
            client_options={"api_endpoint": f"{region}-dataproc.googleapis.com:443"}
        )
        self._cluster_client = dataproc_v1.ClusterControllerClient(
            client_options={"api_endpoint": f"{region}-dataproc.googleapis.com:443"}
        )

    # ------------------------------------------------------------------
    # Job submission
    # ------------------------------------------------------------------

    def submit_pyspark_job(
        self,
        script_gcs_uri: str,
        job_id: str,
        args: list[str] | None = None,
        python_files: list[str] | None = None,
        jar_files: list[str] | None = None,
        properties: dict[str, str] | None = None,
    ) -> str:
        """Submit a PySpark job to Dataproc and return the Dataproc job ID."""
        pyspark_job: dict = {
            "main_python_file_uri": script_gcs_uri,
        }
        if args:
            pyspark_job["args"] = args
        if python_files:
            pyspark_job["python_file_uris"] = python_files
        if jar_files:
            pyspark_job["jar_file_uris"] = jar_files
        if properties:
            pyspark_job["properties"] = properties

        job = {
            "placement": {"cluster_name": self.cluster_name},
            "pyspark_job": pyspark_job,
            "reference": {"job_id": job_id},
        }

        submitted = self._jobs_client.submit_job(
            request={
                "project_id": self.project_id,
                "region": self.region,
                "job": job,
            }
        )
        dataproc_job_id = submitted.reference.job_id
        logger.info(
            "dataproc_job_submitted",
            job_id=dataproc_job_id,
            script=script_gcs_uri,
        )
        return dataproc_job_id

    # ------------------------------------------------------------------
    # Job monitoring
    # ------------------------------------------------------------------

    def wait_for_job(
        self,
        job_id: str,
        poll_interval_s: float = 15.0,
        timeout_s: float = 3600.0,
    ) -> DataprocJobResult:
        """Poll until job reaches a terminal state and return the result."""
        start = time.monotonic()
        logger.info("dataproc_waiting_for_job", job_id=job_id)

        while True:
            elapsed = time.monotonic() - start
            if elapsed > timeout_s:
                raise TimeoutError(
                    f"Dataproc job {job_id} did not complete within {timeout_s}s"
                )

            job = self._get_job(job_id)
            state = job.status.state

            logger.debug("dataproc_job_poll", job_id=job_id, state=state.name)

            if state in _TERMINAL_STATES:
                return self._build_result(job, elapsed)

            time.sleep(poll_interval_s)

    def get_job_state(self, job_id: str) -> JobState:
        job = self._get_job(job_id)
        return self._map_state(job.status.state)

    # ------------------------------------------------------------------
    # Cluster management (ephemeral pattern)
    # ------------------------------------------------------------------

    def create_ephemeral_cluster(
        self,
        cluster_name: str,
        num_workers: int = 2,
        machine_type: str = "n1-standard-4",
        image_version: str = "2.2-debian12",
    ) -> None:
        """Create a Dataproc cluster. Intended for ephemeral per-job clusters."""
        cluster = {
            "project_id": self.project_id,
            "cluster_name": cluster_name,
            "config": {
                "master_config": {
                    "num_instances": 1,
                    "machine_type_uri": machine_type,
                },
                "worker_config": {
                    "num_instances": num_workers,
                    "machine_type_uri": machine_type,
                },
                "software_config": {
                    "image_version": image_version,
                    "optional_components": ["DELTA"],
                },
                "gce_cluster_config": {
                    "zone_uri": f"{self.region}-a",
                },
                "temp_bucket": self.staging_bucket,
            },
        }
        operation = self._cluster_client.create_cluster(
            request={
                "project_id": self.project_id,
                "region": self.region,
                "cluster": cluster,
            }
        )
        operation.result()  # Wait for cluster to be ready
        logger.info("dataproc_cluster_created", cluster_name=cluster_name)

    def delete_cluster(self, cluster_name: str) -> None:
        operation = self._cluster_client.delete_cluster(
            request={
                "project_id": self.project_id,
                "region": self.region,
                "cluster_name": cluster_name,
            }
        )
        operation.result()
        logger.info("dataproc_cluster_deleted", cluster_name=cluster_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_job(self, job_id: str) -> Job:
        return self._jobs_client.get_job(
            request={
                "project_id": self.project_id,
                "region": self.region,
                "job_id": job_id,
            }
        )

    def _build_result(self, job: Job, duration_s: float) -> DataprocJobResult:
        state = self._map_state(job.status.state)
        error_msg = job.status.details if state == JobState.ERROR else None
        output_uri = (
            job.driver_output_resource_uri
            if hasattr(job, "driver_output_resource_uri")
            else None
        )
        logger.info(
            "dataproc_job_finished",
            job_id=job.reference.job_id,
            state=state,
            duration_s=round(duration_s, 1),
        )
        return DataprocJobResult(
            job_id=job.reference.job_id,
            state=state,
            output_uri=output_uri,
            error_message=error_msg,
            duration_s=round(duration_s, 1),
        )

    @staticmethod
    def _map_state(raw: JobStatus.State) -> JobState:
        mapping = {
            JobStatus.State.PENDING: JobState.PENDING,
            JobStatus.State.SETUP_DONE: JobState.RUNNING,
            JobStatus.State.RUNNING: JobState.RUNNING,
            JobStatus.State.DONE: JobState.DONE,
            JobStatus.State.ERROR: JobState.ERROR,
            JobStatus.State.CANCELLED: JobState.CANCELLED,
        }
        return mapping.get(raw, JobState.RUNNING)
