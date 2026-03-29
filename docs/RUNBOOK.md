# Runbook — Autonomous ETL Agent

## Deployment

### Cloud Run Deployment (CI/CD — automatic)

Every merge to `main` triggers the GitHub Actions workflow:
1. Lint + unit tests
2. Build Docker image → push to Artifact Registry
3. Deploy to Cloud Run
4. Smoke test `/health`

### Manual Deployment

```bash
# Build
docker build -f infra/docker/Dockerfile -t etl-agent-api:latest .

# Push to Artifact Registry
REGISTRY=us-central1-docker.pkg.dev/YOUR_PROJECT/etl-agent
docker tag etl-agent-api:latest $REGISTRY/api:latest
docker push $REGISTRY/api:latest

# Deploy
gcloud run deploy etl-agent-api \
  --image $REGISTRY/api:latest \
  --region us-central1 \
  --platform managed \
  --memory 2Gi --cpu 2
```

---

## Monitoring

### Logs

All agent decisions and errors are logged to **Cloud Logging** via `structlog`.

```bash
# Tail API logs
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=etl-agent-api" \
  --limit 50 --format "value(textPayload)"

# Filter for errors only
gcloud logging read 'severity=ERROR AND resource.type=cloud_run_revision' \
  --limit 20
```

### Key Log Fields

| Field | Description |
|---|---|
| `run_id` | Pipeline run identifier |
| `story_id` | User story being processed |
| `current_step` | Current orchestrator node |
| `input_tokens` / `output_tokens` | Per-call LLM token usage |
| `latency_ms` | LLM call duration |

### Dataproc Job Monitoring

```bash
# List recent jobs
gcloud dataproc jobs list --region us-central1

# Get job details
gcloud dataproc jobs describe JOB_ID --region us-central1

# Stream job driver output
gcloud dataproc jobs wait JOB_ID --region us-central1
```

### Cloud Composer (Airflow)

Access Airflow UI via Cloud Composer:

```bash
gcloud composer environments describe etl-agent-composer \
  --location us-central1 \
  --format "value(config.airflowUri)"
```

---

## Common Operations

### Re-run a Failed Story

Simply re-submit the story via API — each submission creates a new run:

```bash
curl -X POST https://YOUR_API_URL/stories \
  -H "X-API-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"id": "STORY-042", "title": "...", "description": "..."}'
```

### View All Runs

```bash
curl https://YOUR_API_URL/runs \
  -H "X-API-Key: YOUR_KEY" | python -m json.tool
```

### Download a Generated Script from GCS

```bash
gsutil cp gs://YOUR_PROJECT-artifacts/runs/RUN_ID/pipeline_story_042.py .
```

### Manually Submit a Dataproc Job

```bash
gcloud dataproc jobs submit pyspark \
  gs://YOUR_ARTIFACTS_BUCKET/runs/RUN_ID/pipeline_story_001.py \
  --cluster etl-agent-cluster \
  --region us-central1 \
  -- --input-path gs://raw/customers/ --output-path gs://processed/customers_clean/
```

### Upload an Airflow DAG Manually

```bash
COMPOSER_BUCKET=$(gcloud composer environments describe etl-agent-composer \
  --location us-central1 --format "value(config.dagGcsPrefix)")
gsutil cp orchestration/my_dag.py $COMPOSER_BUCKET/
```

---

## Scaling

### Cloud Run

Cloud Run auto-scales to 5 instances by default (see Terraform). Increase `max_instance_count` for higher throughput.

GCP note: Each concurrent story submission runs in a separate async background task within an instance. For very high concurrency, consider using Cloud Tasks instead of asyncio background tasks.

### Dataproc Cluster Sizing

Edit `infra/terraform/main.tf` `worker_config.num_instances` and re-apply. Alternatively, use Dataproc Serverless (no cluster management):

```bash
gcloud dataproc batches submit pyspark gs://BUCKET/script.py \
  --region us-central1 \
  --subnet default
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Run stuck in `RUNNING` | LLM call timeout | Check Cloud Run logs for `anthropic` timeout errors; increase `ANTHROPIC_MAX_TOKENS` |
| `code_generation` step fails | Claude returned invalid Python | Review `code_gen_issues_found` log; check prompt templates |
| `create_pr` fails with 422 | GitHub branch already exists | Branch `etl/auto/{story-id}` exists; delete it manually or resubmit |
| Dataproc job `ERROR` state | Script runtime error | Check `gcloud dataproc jobs describe JOB_ID` for driver output |
| Cloud Run 503 | Cold start / instance scaling | Use `min_instance_count = 1` in Terraform for always-warm service |
| Secret Manager access denied | Wrong IAM binding | Verify `etl-agent-sa` has `roles/secretmanager.secretAccessor` |

---

## Security Checklist

- [ ] `.env` file not committed to git (`.gitignore` covers this)
- [ ] Secrets in Secret Manager, not hardcoded
- [ ] Service account follows least-privilege IAM
- [ ] Cloud Run requires API key header
- [ ] Generated PRs require human approval before merge
- [ ] Terraform state stored in GCS (remote backend, not local)
- [ ] Artifact Registry images scanned by Cloud Build

---

## Updating the LLM Model

To change the Claude model (e.g., to a newer version):

1. Update `ANTHROPIC_MODEL` in `.env` / Cloud Run env vars
2. Test with `pytest tests/agents/ -v` to confirm prompt compatibility
3. Deploy updated Cloud Run service

Current default: `claude-3-5-sonnet-20241022`
