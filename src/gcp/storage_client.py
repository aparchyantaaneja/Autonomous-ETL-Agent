"""Google Cloud Storage client with retry logic and structured operations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import BinaryIO

import structlog
from google.api_core import retry as google_retry
from google.cloud import storage

logger = structlog.get_logger(__name__)


class GCSClient:
    """Wrapper around the GCS Python SDK for common ETL agent operations."""

    def __init__(self, project_id: str, artifacts_bucket: str) -> None:
        self._client = storage.Client(project=project_id)
        self.artifacts_bucket = artifacts_bucket
        self.project_id = project_id

    # ------------------------------------------------------------------
    # Upload / Download
    # ------------------------------------------------------------------

    def upload_string(
        self,
        content: str,
        gcs_path: str,
        content_type: str = "text/plain",
    ) -> str:
        """Upload a string to GCS and return the full gs:// URI."""
        bucket_name, blob_name = self._parse_path(gcs_path)
        bucket = self._client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(content, content_type=content_type)
        uri = f"gs://{bucket_name}/{blob_name}"
        logger.info("gcs_upload", uri=uri, size_bytes=len(content.encode()))
        return uri

    def upload_file(
        self,
        local_path: str | Path,
        gcs_path: str,
    ) -> str:
        """Upload a local file to GCS and return the full gs:// URI."""
        local_path = Path(local_path)
        bucket_name, blob_name = self._parse_path(gcs_path)
        bucket = self._client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(str(local_path))
        uri = f"gs://{bucket_name}/{blob_name}"
        logger.info("gcs_upload_file", uri=uri, local_path=str(local_path))
        return uri

    def upload_fileobj(self, fileobj: BinaryIO, gcs_path: str) -> str:
        bucket_name, blob_name = self._parse_path(gcs_path)
        bucket = self._client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_file(fileobj)
        uri = f"gs://{bucket_name}/{blob_name}"
        logger.info("gcs_upload_fileobj", uri=uri)
        return uri

    @google_retry.Retry()
    def download_string(self, gcs_path: str) -> str:
        """Download a GCS object and return its contents as a string."""
        bucket_name, blob_name = self._parse_path(gcs_path)
        bucket = self._client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        content = blob.download_as_text()
        logger.info(
            "gcs_download",
            gcs_path=gcs_path,
            size_bytes=len(content.encode()),
        )
        return content

    def download_json(self, gcs_path: str) -> dict:
        return json.loads(self.download_string(gcs_path))

    # ------------------------------------------------------------------
    # Artifact helpers
    # ------------------------------------------------------------------

    def upload_artifact(
        self,
        content: str,
        run_id: str,
        filename: str,
        content_type: str = "text/plain",
    ) -> str:
        """Upload a generated artifact into the standard artifacts bucket path."""
        gcs_path = f"gs://{self.artifacts_bucket}/runs/{run_id}/{filename}"
        return self.upload_string(content, gcs_path, content_type)

    def list_artifacts(self, run_id: str) -> list[str]:
        """List all artifacts for a given run."""
        prefix = f"runs/{run_id}/"
        bucket = self._client.bucket(self.artifacts_bucket)
        blobs = self._client.list_blobs(bucket, prefix=prefix)
        return [f"gs://{self.artifacts_bucket}/{b.name}" for b in blobs]

    # ------------------------------------------------------------------
    # Bucket management
    # ------------------------------------------------------------------

    def bucket_exists(self, bucket_name: str) -> bool:
        try:
            self._client.get_bucket(bucket_name)
            return True
        except Exception:
            return False

    def ensure_bucket_exists(self, bucket_name: str, location: str = "us-central1") -> None:
        if not self.bucket_exists(bucket_name):
            bucket = self._client.create_bucket(bucket_name, location=location)
            logger.info("gcs_bucket_created", bucket=bucket_name, location=location)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_path(gcs_path: str) -> tuple[str, str]:
        """Parse gs://bucket/path into (bucket, path)."""
        path = gcs_path.removeprefix("gs://")
        bucket, _, blob = path.partition("/")
        if not bucket or not blob:
            raise ValueError(
                f"Invalid GCS path '{gcs_path}'. Expected gs://bucket/object."
            )
        return bucket, blob
