# Setup Guide — Autonomous ETL Agent on GCP

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | Use `pyenv` or your system Python |
| Terraform | >= 1.5 | [Install](https://developer.hashicorp.com/terraform/install) |
| Google Cloud SDK | latest | [Install](https://cloud.google.com/sdk/docs/install) |
| Docker | 24+ | For local container builds |
| Git | latest | |

---

## 1. Clone the Repository

```bash
git clone https://github.com/aparchyantaaneja/Autonomous-ETL-Agent.git
cd Autonomous-ETL-Agent
```

## 2. Create a Python Virtual Environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Configure Environment Variables

```bash
cp .env.template .env
```

Open `.env` and fill in:

| Variable | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API Keys |
| `GCP_PROJECT_ID` | Your GCP project ID |
| `GITHUB_TOKEN` | GitHub → Settings → Developer settings → Personal access tokens (repo + PR scopes) |
| `GITHUB_REPO_OWNER` | Your GitHub username or org |
| `API_KEY` | Generate a strong random string: `python -c "import secrets; print(secrets.token_hex(32))"` |

The GCS bucket names and Dataproc settings are populated automatically after Terraform runs (step 5).

## 4. Set Up GCP Project

```bash
# Log in and set your active project
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Enable Application Default Credentials for local development
gcloud auth application-default login
```

## 5. Provision GCP Infrastructure with Terraform

```bash
cd infra/terraform

# Initialize providers
terraform init

# Preview changes
terraform plan -var="project_id=YOUR_PROJECT_ID" -var="github_repo_owner=YOUR_GITHUB_USERNAME"

# Apply (takes ~5 minutes)
terraform apply -var="project_id=YOUR_PROJECT_ID" -var="github_repo_owner=YOUR_GITHUB_USERNAME"

cd ../..
```

After apply, update your `.env` with the output values:

```bash
terraform -chdir=infra/terraform output -json
```

## 6. Store Secrets in Secret Manager

After Terraform creates the secret resources, add the actual values:

```bash
# Anthropic API key
echo -n "YOUR_ANTHROPIC_KEY" | \
  gcloud secrets versions add etl-agent-anthropic-key --data-file=-

# GitHub token
echo -n "YOUR_GITHUB_TOKEN" | \
  gcloud secrets versions add etl-agent-github-token --data-file=-

# API key (same value you put in .env)
echo -n "YOUR_API_KEY" | \
  gcloud secrets versions add etl-agent-api-key --data-file=-
```

## 7. Run Unit Tests

```bash
pytest tests/agents/ -v -m "not integration"
```

All tests should pass. Coverage report is printed at the end.

## 8. Start the API Server Locally

```bash
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

Open Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)

## 9. Submit Your First Story

```bash
# Using the included sample story
curl -X POST http://localhost:8000/stories \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/sample_stories.json | python -m json.tool
```

Or using direct JSON:

```bash
curl -X POST http://localhost:8000/stories \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "STORY-001",
    "title": "Filter and dedupe customers",
    "description": "Remove null emails and deduplicate by customer_id."
  }'
```

Response:
```json
{
  "run_id": "abc-123-...",
  "message": "Story submitted for processing",
  "status": "PENDING"
}
```

Poll for status:

```bash
curl http://localhost:8000/runs/abc-123 \
  -H "X-API-Key: YOUR_API_KEY" | python -m json.tool
```

## 10. Deploy to Cloud Run

```bash
# Build and tag the Docker image
docker build -f infra/docker/Dockerfile -t etl-agent-api:latest .

# Tag for Artifact Registry
docker tag etl-agent-api:latest \
  us-central1-docker.pkg.dev/YOUR_PROJECT_ID/etl-agent/api:latest

# Push
docker push us-central1-docker.pkg.dev/YOUR_PROJECT_ID/etl-agent/api:latest

# Deploy
gcloud run deploy etl-agent-api \
  --image us-central1-docker.pkg.dev/YOUR_PROJECT_ID/etl-agent/api:latest \
  --region us-central1 \
  --platform managed \
  --memory 2Gi \
  --cpu 2
```

## 11. Set Up CI/CD (GitHub Actions)

In your GitHub repository, go to **Settings → Secrets and variables → Actions** and add:

| Secret | Value |
|---|---|
| `GCP_PROJECT_ID` | Your GCP project ID |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | Workload Identity Provider resource name |
| `GCP_SERVICE_ACCOUNT` | `etl-agent-sa@YOUR_PROJECT.iam.gserviceaccount.com` |

Workload Identity Federation setup:

```bash
# Create workload identity pool
gcloud iam workload-identity-pools create "github-pool" \
  --location="global" \
  --display-name="GitHub Actions Pool"

# Create provider
gcloud iam workload-identity-pools providers create-oidc "github-provider" \
  --location="global" \
  --workload-identity-pool="github-pool" \
  --display-name="GitHub provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# Allow the SA to be impersonated from the repo
gcloud iam service-accounts add-iam-policy-binding \
  etl-agent-sa@YOUR_PROJECT.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/attribute.repository/aparchyantaaneja/Autonomous-ETL-Agent"
```

---

## Troubleshooting

| Issue | Solution |
|---|---|
| `ANTHROPIC_API_KEY not set` | Ensure `.env` is populated and `.env` file is in project root |
| `google.auth.exceptions.DefaultCredentialsError` | Run `gcloud auth application-default login` |
| `dataproc cluster not found` | Verify cluster was created by Terraform and cluster name in `.env` matches |
| `GitHub 401 on PR creation` | Check token has `repo` and `pull_requests:write` scopes |
| `ImportError: No module named 'pyspark'` | Run `pip install -r requirements.txt` in your virtual environment |
