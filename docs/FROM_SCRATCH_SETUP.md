# From-Scratch Setup Guide

This guide is for a macOS machine with **nothing installed** — no Python, no GCP account, no API keys. Follow every step in order.

---

## Part 1 — Install System Tools

### 1.1 Xcode Command Line Tools (Git + compiler)

**What it is:** A lightweight Apple package (~1 GB) that installs Git, a C/C++ compiler (Clang), and Make.  
**Why you need it for this project:** Homebrew (next step) requires a compiler to build packages. Git is used to clone this repository and to let the PR Agent commit and push generated PySpark code to GitHub branches automatically.

Open **Terminal** (Cmd + Space → "Terminal") and run:

```bash
xcode-select --install
```

A dialog will pop up. Click **Install** and wait (~5 min). When it finishes:

```bash
git --version   # should print: git version 2.x.x
```

---

### 1.2 Homebrew (macOS package manager)

**What it is:** The standard package manager for macOS — like an app store for developer tools, but free and command-line driven.  
**Why you need it for this project:** Every other tool in this section (Python, gcloud, Terraform) is installed via a single `brew install` command. Without it you'd need to manually download and configure each one.

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the on-screen instructions. At the end of the install it will print two `eval` commands — **run both of them** to add `brew` to your PATH. Then:

```bash
brew --version   # should print: Homebrew 4.x.x
```

---

### 1.3 Python 3.11

**What it is:** The programming language the entire agent is written in.  
**Why you need it for this project:** All four AI agents (Task Breakdown, Code Generator, Test Generator, PR Agent), the LangGraph orchestrator, the FastAPI web server, and the GCP/GitHub clients are Python code. Version 3.11 specifically is required because the `tomllib` standard library module and several async improvements it relies on were introduced in 3.11.

```bash
brew install python@3.11
```

Verify:

```bash
python3.11 --version   # Python 3.11.x
```

---

### 1.4 Google Cloud SDK (`gcloud`)

**What it is:** Google's official command-line interface for managing every GCP service.  
**Why you need it for this project:** Used in four key ways — (1) authenticating your local machine so Python/Terraform can talk to GCP without a key file, (2) storing your API keys in Secret Manager, (3) monitoring Dataproc jobs during development, and (4) deploying the FastAPI container to Cloud Run.

```bash
brew install --cask google-cloud-sdk
```

After install, restart Terminal (close and reopen), then:

```bash
gcloud --version   # Google Cloud SDK 4xx.x.x
```

---

### 1.5 Terraform

**What it is:** An Infrastructure-as-Code (IaC) tool — you describe cloud resources in `.tf` files and Terraform creates/updates/deletes them to match.  
**Why you need it for this project:** Running `terraform apply` once provisions all ~50 GCP resources the project needs: 3 GCS buckets (raw, processed, artifacts), a Dataproc Spark cluster, a Cloud Run service, Secret Manager secrets, IAM service accounts and permissions, and an Artifact Registry for Docker images. Without it you'd have to click through dozens of GCP Console screens manually.

```bash
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
```

Verify:

```bash
terraform --version   # Terraform v1.x.x
```

---

### 1.6 Docker Desktop

**What it is:** A tool that packages an application and all its dependencies into a portable container image that runs identically on any machine or cloud service.  
**Why you need it for this project:** The FastAPI server is deployed to Cloud Run as a Docker image. The `Dockerfile` in `infra/docker/` bundles Python, the app code, and a JRE (needed for PySpark) into one image. During CI/CD, GitHub Actions builds this image and pushes it to GCP Artifact Registry — Docker Desktop lets you do the same build locally to test before pushing.

Download the installer from [https://www.docker.com/products/docker-desktop/](https://www.docker.com/products/docker-desktop/) (choose **Apple Silicon** if your Mac has an M-series chip, or **Intel** if it has an Intel chip).

Run the downloaded `.dmg`, drag Docker to Applications, then launch it from Spotlight. Wait for the whale icon in the menu bar to stop animating (Docker is ready).

```bash
docker --version   # Docker version 26.x.x
```

---

## Part 2 — Create Accounts & Get API Keys

### 2.1 Google Cloud Account + $300 Free Credit

1. Go to [https://console.cloud.google.com](https://console.cloud.google.com)
2. Sign in with your Google account (or create one)
3. Click **"Try Google Cloud for free"** → follow the wizard
4. Add a credit card — **you will not be charged** unless you manually upgrade; the free trial gives **$300 credit for 90 days**
5. After setup you land in the Cloud Console

#### Create a GCP Project

In the Cloud Console, click the project selector at the top → **"New Project"**:

| Field | What to enter |
|---|---|
| Project name | `etl-agent-project` (or any name you like) |
| Project ID | This is auto-generated (e.g., `etl-agent-project-123456`) — **copy this, you need it later** |
| Billing account | Select the billing account from step 3 above |

Click **Create** and wait ~30 seconds for it to provision.

#### Enable Billing on the Project

Go to **Billing → My Projects** and confirm your new project shows a billing account linked. If not, click the overflow menu → **Change billing**.

---

### 2.2 Anthropic API Key (Claude)

1. Go to [https://console.anthropic.com](https://console.anthropic.com)
2. Sign up for a free account
3. Go to **API Keys** → **Create Key** → give it a name like `etl-agent`
4. **Copy the key immediately** — it is only shown once. It looks like `sk-ant-api03-...`
5. Add $5 credit under **Plans & Billing → Add credit** (Claude API charges per token; STORY-003 costs roughly $0.10–0.30 per full pipeline run)

---

### 2.3 GitHub Personal Access Token

1. Log in to [https://github.com](https://github.com) (create a free account if needed)
2. Go to **Settings → Developer settings → Personal access tokens → Tokens (classic)** → **Generate new token (classic)**
3. Set:
   - **Note:** `etl-agent`
   - **Expiration:** 90 days (or No expiration)
   - **Scopes:** check `repo` (all sub-items) and `workflow`
4. Click **Generate token** and **copy it** — looks like `ghp_...`

---

## Part 3 — Clone the Repository

```bash
git clone https://github.com/aparchyantaaneja/Autonomous-ETL-Agent.git
cd Autonomous-ETL-Agent
```

---

## Part 4 — Python Virtual Environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

You should see `(.venv)` in your terminal prompt. Every time you open a new terminal for this project, run `source .venv/bin/activate` again.

---

## Part 5 — Fill In Your Environment Variables

```bash
cp .env.template .env
```

Open `.env` in any text editor (VS Code: `code .env`). Fill in the values you collected in Part 2:

```dotenv
# Anthropic
ANTHROPIC_API_KEY=sk-ant-api03-YOUR_KEY_HERE
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

# GCP
GCP_PROJECT_ID=etl-agent-project-123456      # ← your Project ID from step 2.1
GCP_REGION=us-central1

# GitHub
GITHUB_TOKEN=ghp_YOUR_TOKEN_HERE
GITHUB_REPO_OWNER=aparchyantaaneja            # ← your GitHub username
GITHUB_REPO_NAME=Autonomous-ETL-Agent

# API security key (generate a random one)
API_KEY=
```

Generate the `API_KEY` value:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste the output into `.env` as the `API_KEY` value.

---

## Part 6 — Authenticate with Google Cloud

### 6.1 Log in with your Google account

```bash
gcloud auth login
```

A browser window opens. Sign in with the same Google account you used in Part 2.1.

### 6.2 Set your project

```bash
gcloud config set project YOUR_PROJECT_ID   # e.g., etl-agent-project-123456
```

### 6.3 Application Default Credentials (for local development)

```bash
gcloud auth application-default login
```

Another browser window opens. Sign in again. This creates a local credentials file that Python/Terraform use automatically.

### 6.4 Verify

```bash
gcloud projects describe YOUR_PROJECT_ID
```

You should see project details. If you see a `PERMISSION_DENIED` error, your billing account isn't linked — go back to step 2.1.

---

## Part 7 — Provision GCP Infrastructure with Terraform

This creates all cloud resources: GCS buckets, Dataproc cluster, Cloud Run service, Secret Manager secrets, service accounts, IAM permissions.

```bash
cd infra/terraform

# Download providers (only needed once)
terraform init

# Preview what will be created (~50 resources)
terraform plan \
  -var="project_id=YOUR_PROJECT_ID" \
  -var="github_repo_owner=YOUR_GITHUB_USERNAME"
```

Review the output. When ready:

```bash
terraform apply \
  -var="project_id=YOUR_PROJECT_ID" \
  -var="github_repo_owner=YOUR_GITHUB_USERNAME"
```

Type `yes` when prompted. This takes **3–8 minutes**.

> **Cost estimate:** With a Dataproc cluster running, you spend ~$0.10/hour on preemptible workers. The cluster auto-deletes after jobs finish. Cloud Run only charges when processing requests.

### 7.1 Capture Terraform Outputs

```bash
terraform output -json
cd ../..
```

Copy the values from the output into your `.env`:

| Terraform output key | `.env` variable |
|---|---|
| `artifacts_bucket_name` | `GCS_ARTIFACTS_BUCKET` |
| `raw_data_bucket_name` | `GCS_RAW_BUCKET` |
| `processed_data_bucket_name` | `GCS_PROCESSED_BUCKET` |
| `dataproc_cluster_name` | `DATAPROC_CLUSTER_NAME` |

---

## Part 8 — Store Secrets in Secret Manager

Terraform created the secret *resources* but they're empty. Now add the actual values:

```bash
# Anthropic API key
echo -n "sk-ant-api03-YOUR_KEY" | \
  gcloud secrets versions add etl-agent-anthropic-key --data-file=-

# GitHub token
echo -n "ghp_YOUR_TOKEN" | \
  gcloud secrets versions add etl-agent-github-token --data-file=-

# API key (same value as API_KEY in .env)
echo -n "YOUR_API_KEY" | \
  gcloud secrets versions add etl-agent-api-key --data-file=-
```

Verify:

```bash
gcloud secrets list
# Should show 3 secrets: etl-agent-anthropic-key, etl-agent-github-token, etl-agent-api-key
```

---

## Part 9 — Verify: Run Unit Tests

```bash
source .venv/bin/activate   # if not already active
pytest tests/agents/ -v -m "not integration"
```

All tests should pass (with `PASSED` green output). If you see `ModuleNotFoundError`, make sure your venv is active.

---

## Part 10 — Run the API Server Locally

```bash
uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000
```

Open your browser to [http://localhost:8000/docs](http://localhost:8000/docs) — you'll see the interactive Swagger API UI.

In a new Terminal tab (keep uvicorn running):

```bash
source .venv/bin/activate
cd Autonomous-ETL-Agent

# Health check
curl http://localhost:8000/health

# Submit your first story
curl -X POST http://localhost:8000/stories \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "STORY-001",
    "title": "Filter and deduplicate customers",
    "description": "Remove null emails and deduplicate by customer_id from the raw customers dataset."
  }'
```

You'll get back a `run_id`. Poll for status:

```bash
curl http://localhost:8000/runs/THE_RUN_ID_FROM_ABOVE \
  -H "X-API-Key: YOUR_API_KEY" | python -m json.tool
```

---

## Part 11 — Deploy to Cloud Run (optional for MVP)

Once you've verified the API works locally, push it to GCP Cloud Run:

```bash
# Configure Docker to push to GCP Artifact Registry
gcloud auth configure-docker us-central1-docker.pkg.dev

# Build
docker build -f infra/docker/Dockerfile -t etl-agent-api:latest .

# Tag
docker tag etl-agent-api:latest \
  us-central1-docker.pkg.dev/YOUR_PROJECT_ID/etl-agent/api:latest

# Push
docker push \
  us-central1-docker.pkg.dev/YOUR_PROJECT_ID/etl-agent/api:latest

# Deploy
gcloud run deploy etl-agent-api \
  --image us-central1-docker.pkg.dev/YOUR_PROJECT_ID/etl-agent/api:latest \
  --region us-central1 \
  --platform managed \
  --memory 2Gi \
  --cpu 2 \
  --allow-unauthenticated
```

Cloud Run will print a URL like `https://etl-agent-api-xxxx-uc.a.run.app`. Test it:

```bash
curl https://etl-agent-api-xxxx-uc.a.run.app/health
```

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `brew: command not found` | Homebrew PATH not set after install | Close and reopen Terminal; re-run the `eval` line from the install output |
| `python3.11: command not found` | Python not installed or PATH not updated | `brew install python@3.11` then restart Terminal |
| `gcloud: command not found` | SDK not in PATH | Restart Terminal after `brew install --cask google-cloud-sdk` |
| `Error: Billing account not found` | No billing on project | In GCP Console → Billing → link account to project |
| `google.auth.exceptions.DefaultCredentialsError` | Not authenticated | Run `gcloud auth application-default login` |
| `Error 403: The caller does not have permission` | APIs not enabled or IAM missing | Re-run `terraform apply`; it enables all required APIs |
| `ANTHROPIC_API_KEY not set` | `.env` not populated or venv not active | Check `.env` has the key; run `source .venv/bin/activate` |
| `ModuleNotFoundError` | venv not active | `source .venv/bin/activate` |
| `terraform: No such file or directory` | Terraform not installed | `brew install hashicorp/tap/terraform` |
| Terraform `apply` fails on quota | GCP project quota | In Console → IAM & Admin → Quotas, request increase (usually instant) |

---

## Cost Summary

| Resource | Estimated Cost |
|---|---|
| Dataproc (n1-standard-2 workers, preemptible) | ~$0.04/hr per worker when running |
| Cloud Run | First 2M requests/month free |
| GCS storage | ~$0.02/GB/month |
| Secret Manager | ~$0.06 per 10k accesses |
| Claude API (claude-3-5-sonnet) | ~$0.10–0.30 per pipeline run |
| **Total for casual development** | **< $5/month** |

The $300 GCP free credit more than covers testing this project for months.
