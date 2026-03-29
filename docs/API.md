# REST API Reference — Autonomous ETL Agent

Base URL: `https://etl-agent-api-<hash>-uc.a.run.app` (or `http://localhost:8000` locally)

## Authentication

All endpoints (except `/health`) require an API key in the request header:

```
X-API-Key: your_api_key_here
```

Return `401 Unauthorized` if the key is missing or invalid.

---

## Endpoints

### `GET /health`

Health check. No authentication required.

**Response 200:**
```json
{
  "status": "ok",
  "timestamp": "2026-03-27T12:00:00",
  "version": "1.0.0"
}
```

---

### `POST /stories`

Submit a user story for autonomous ETL pipeline generation.

The request is accepted immediately (`202 Accepted`) and processed asynchronously. Use the returned `run_id` to track progress.

**Request body:**
```json
{
  "id": "STORY-042",
  "title": "Aggregate monthly revenue by region",
  "description": "Join orders with customers, clean nulls, aggregate monthly revenue by geo region, and upsert results to the Delta Lake campaign table.",
  "acceptance_criteria": [
    "Join on customer_id",
    "Aggregate by (region, year, month)",
    "Delta merge upsert with (region, year, month) as key"
  ],
  "labels": ["revenue", "analytics"],
  "deploy_to_dataproc": false,
  "generate_airflow_dag": true
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | string | ✅ | Story identifier (e.g., JIRA/GitHub issue number) |
| `title` | string | ✅ | Short title for the pipeline |
| `description` | string | ✅ | Full description with transformation intent |
| `acceptance_criteria` | string[] | | Optional list of criteria for validation |
| `labels` | string[] | | Optional tags (e.g., domain, team) |
| `deploy_to_dataproc` | boolean | | Submit generated job to Dataproc after PR (default: false) |
| `generate_airflow_dag` | boolean | | Generate and upload Airflow DAG (default: false) |

**Response 202:**
```json
{
  "run_id": "3f8a2c1d-...",
  "message": "Story submitted for processing",
  "status": "PENDING"
}
```

**Response 422:** Validation error (missing required fields).

---

### `GET /runs`

List all pipeline runs, newest first.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | integer | 50 | Maximum number of runs to return |

**Response 200:**
```json
[
  {
    "run_id": "3f8a2c1d-...",
    "story_id": "STORY-042",
    "story_title": "Aggregate monthly revenue by region",
    "status": "DONE",
    "current_step": "create_pr",
    "started_at": "2026-03-27T12:00:00Z",
    "completed_at": "2026-03-27T12:03:42Z",
    "pr_url": "https://github.com/owner/repo/pull/12",
    "pr_number": 12,
    "dataproc_job_id": null,
    "dataproc_job_state": null,
    "airflow_dag_id": "etl_story_042",
    "error_message": null
  }
]
```

---

### `GET /runs/{run_id}`

Get full details for a specific pipeline run, including the ETL specification and all artifact GCS URIs.

**Response 200:**
```json
{
  "run_id": "3f8a2c1d-...",
  "story_id": "STORY-042",
  "story_title": "Aggregate monthly revenue by region",
  "status": "DONE",
  "current_step": "create_pr",
  "started_at": "2026-03-27T12:00:00Z",
  "completed_at": "2026-03-27T12:03:42Z",
  "pr_url": "https://github.com/owner/repo/pull/12",
  "pr_number": 12,
  "dataproc_job_id": null,
  "dataproc_job_state": null,
  "airflow_dag_id": "etl_story_042",
  "error_message": null,
  "etl_spec": {
    "story_id": "STORY-042",
    "title": "...",
    "summary": "...",
    "operations": [...]
  },
  "artifact_uris": {
    "script": "gs://project-artifacts/runs/3f8a2c1d/pipeline_story_042.py",
    "notebook": "gs://project-artifacts/runs/3f8a2c1d/notebook_story_042.ipynb",
    "test_file": "gs://project-artifacts/runs/3f8a2c1d/test_pipeline_story_042.py",
    "conftest": "gs://project-artifacts/runs/3f8a2c1d/conftest.py",
    "fixtures": "gs://project-artifacts/runs/3f8a2c1d/story_042_test_data.json"
  }
}
```

**Response 404:** Run not found.

---

### `POST /deploy/{run_id}`

Trigger Dataproc deployment for a run that completed successfully but was not initially deployed.

**Requires:** Run status must be `DONE` and have a `script` artifact.

**Request body (optional):**
```json
{
  "notify_on_complete": false
}
```

**Response 200:** Returns the updated `RunSummary`.

**Response 404:** Run not found.  
**Response 409:** Run not in `DONE` state, or no script artifact available.

---

## Run Status Values

| Status | Meaning |
|---|---|
| `PENDING` | Story accepted, not yet started |
| `RUNNING` | Pipeline actively executing |
| `DONE` | All steps completed, PR created |
| `FAILED` | An agent step encountered an error |

## Pipeline Steps

Steps appear in `current_step` field as the run progresses:

`task_breakdown` → `code_generation` → `test_generation` → `upload_artifacts` → `create_pr` → *(optional)* `deploy_dataproc` → *(optional)* `generate_dag`

---

## Error Handling

Failed runs include:
- `error_step`: The name of the step that failed
- `error_message`: Human-readable error description

Re-submit the story to retry. The agent will start a fresh run.
