"""Application settings loaded from environment variables / .env file."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration values for the ETL Agent application.

    Values are read from environment variables (case-insensitive) and, when
    running locally, from a .env file in the project root.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Anthropic
    anthropic_api_key: str
    anthropic_model: str = "claude-3-5-sonnet-20241022"
    anthropic_max_tokens: int = 8192
    llm_max_retries: int = 3

    # GCP
    gcp_project_id: str
    gcp_region: str = "us-central1"
    gcs_raw_bucket: str
    gcs_processed_bucket: str
    gcs_artifacts_bucket: str
    dataproc_cluster_name: str = "etl-agent-cluster"
    dataproc_region: str = "us-central1"
    dataproc_staging_bucket: str

    # GitHub
    github_token: str
    github_repo_owner: str
    github_repo_name: str
    github_base_branch: str = "main"

    # FastAPI
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_debug: bool = False
    api_key: str

    # CORS — comma-separated list of allowed origins
    cors_origins: str = "http://localhost:3000,http://localhost:8501"

    # Application
    log_level: str = "INFO"
    artifacts_dir: str = "./artifacts"
    state_backend: str = "memory"      # memory | firestore | postgres
    database_url: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",")]


# Singleton — imported by other modules
settings = Settings()
