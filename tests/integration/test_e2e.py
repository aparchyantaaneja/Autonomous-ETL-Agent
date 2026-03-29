"""End-to-end integration tests for the full ETL Agent pipeline.

These tests require:
- A valid .env file or environment variables set
- GCP credentials configured
- GCS buckets accessible

Run with:
    pytest tests/integration/ -v -m integration

Mark individual tests to skip in CI if credentials aren't available.
"""

from __future__ import annotations

import json
import os

import pytest


# ---------------------------------------------------------------------------
# Skip integration tests if required env vars are not set
# ---------------------------------------------------------------------------

REQUIRED_ENV_VARS = [
    "ANTHROPIC_API_KEY",
    "GCP_PROJECT_ID",
    "GCS_ARTIFACTS_BUCKET",
    "GITHUB_TOKEN",
    "GITHUB_REPO_OWNER",
    "GITHUB_REPO_NAME",
    "API_KEY",
]

_missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
_skip_integration = bool(_missing)

pytestmark = pytest.mark.skipif(
    _skip_integration,
    reason=f"Integration env vars not set: {', '.join(_missing)}",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _api_headers() -> dict[str, str]:
    return {"X-API-Key": os.environ["API_KEY"], "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Tests — agent pipeline (mocked GCP / GitHub for CI safety)
# ---------------------------------------------------------------------------


class TestTaskBreakdownIntegration:
    """Test the Task Breakdown Agent with real Claude API."""

    @pytest.mark.integration
    def test_parse_simple_story_real_llm(self):
        from src.agents.task_breakdown import TaskBreakdownAgent, UserStory
        from src.config import settings
        from src.llm.claude_client import build_claude_client

        llm = build_claude_client(settings)
        agent = TaskBreakdownAgent(llm)

        story = UserStory(
            id="INT-001",
            title="Filter and dedupe customers",
            description="Remove customers with null email addresses and deduplicate by customer_id.",
        )
        spec = agent.parse_story(story)

        assert spec.story_id == "INT-001"
        assert len(spec.operations) >= 1
        assert any(op.type in ("filter", "dedupe") for op in spec.operations)


class TestAPIIntegration:
    """Test the FastAPI application with the real running server."""

    @pytest.mark.integration
    def test_health_endpoint(self):
        import httpx

        base_url = os.environ.get("API_BASE_URL", "http://localhost:8000")
        resp = httpx.get(f"{base_url}/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    @pytest.mark.integration
    def test_submit_story_and_poll(self):
        import time

        import httpx

        base_url = os.environ.get("API_BASE_URL", "http://localhost:8000")
        headers = _api_headers()

        with open("tests/fixtures/sample_stories.json") as f:
            stories = json.load(f)

        # Submit the first (simplest) story
        story = stories[0]
        resp = httpx.post(f"{base_url}/stories", json=story, headers=headers)
        assert resp.status_code == 202, resp.text

        run_id = resp.json()["run_id"]

        # Poll until done or timeout
        max_wait = 300  # seconds
        poll_interval = 10
        elapsed = 0
        final_status = None

        while elapsed < max_wait:
            time.sleep(poll_interval)
            elapsed += poll_interval
            detail_resp = httpx.get(
                f"{base_url}/runs/{run_id}", headers=headers
            )
            assert detail_resp.status_code == 200
            status = detail_resp.json()["status"]
            if status in ("DONE", "FAILED"):
                final_status = status
                break

        assert final_status == "DONE", (
            f"Run did not complete. Final status: {final_status}\n"
            + json.dumps(detail_resp.json(), indent=2)
        )

    @pytest.mark.integration
    def test_get_runs_list(self):
        import httpx

        base_url = os.environ.get("API_BASE_URL", "http://localhost:8000")
        resp = httpx.get(f"{base_url}/runs", headers=_api_headers())
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.integration
    def test_unauthorized_request_rejected(self):
        import httpx

        base_url = os.environ.get("API_BASE_URL", "http://localhost:8000")
        resp = httpx.get(f"{base_url}/runs", headers={"X-API-Key": "bad-key"})
        assert resp.status_code == 401
