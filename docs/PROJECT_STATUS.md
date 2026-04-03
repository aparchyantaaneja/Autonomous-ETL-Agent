# Project Status — Autonomous ETL Agent
**As of March 2026**

---

## Where I Am Right Now

I have completed the **full codebase** for the Autonomous ETL Agent. Every component has been designed, architected, and written — the folder structure, all source files, configuration, infrastructure, tests, CI/CD, and documentation are all in the repository.

What I am currently working on is the **end-to-end working implementation** — meaning setting up the actual GCP environment, connecting the real API keys, running Terraform to provision the cloud infrastructure, and verifying the system runs successfully from a real user story all the way to a deployed Dataproc job and GitHub PR.

In short: **the code is written, the cloud is not yet provisioned and tested live.**

---

## What I Have Built (Code Complete)

### Core AI Pipeline
The heart of the project is a **4-agent pipeline** orchestrated by LangGraph:

1. **Task Breakdown Agent** — takes a plain-English user story (e.g. "calculate RFM scores for customers") and uses Claude AI to parse it into a structured ETL specification — identifying source datasets, transformation operations, target output, and quality requirements.

2. **Code Generator Agent** — takes that structured spec and generates a production-ready PySpark script + a Jupyter notebook. It runs the generated code through a validation loop (syntax check, structural checks) and asks Claude to refine it if issues are found.

3. **Test Generator Agent** — automatically writes a full pytest test suite for the generated pipeline — schema tests, null checks, business logic assertions, and sample fixture data.

4. **PR Agent** — creates a GitHub feature branch, commits all the generated files (script, notebook, tests) in one atomic commit, and opens a pull request with an auto-generated description and labels.

### Infrastructure & Deployment
- **GCP Infrastructure (Terraform)** — all cloud resources defined as code: 3 GCS buckets (raw data, processed data, artifacts), Dataproc Spark cluster, Cloud Run service, Secret Manager for API keys, Artifact Registry for Docker images, Service Accounts with IAM permissions
- **REST API (FastAPI)** — web service running on Cloud Run with endpoints to submit stories, track run status, and trigger deployments. Secured with an API key header.
- **Airflow DAG Generator** — dynamically generates Cloud Composer DAG files to schedule the ETL pipelines on a recurring basis
- **Docker** — the API is containerised and ready to deploy to Cloud Run
- **CI/CD (GitHub Actions)** — automated pipeline that runs linting + tests → builds the Docker image → pushes to Artifact Registry → deploys to Cloud Run on every merge to main

### Supporting Work
- `config/framework_config.yaml` — coding standards, naming conventions, data quality rules the agents follow
- `config/agent_prompts.yaml` — all LLM prompt templates for each agent
- Unit tests for all agents
- Integration tests (skipped without live credentials)
- Sample stories and fixture data for testing
- Educational Jupyter notebook: Amazon iPhone 17 campaign RFM analysis (matches the most complex sample story)

---

## What Is Still In Progress

### Live Environment Setup
- GCP project created and billing linked — **in progress**
- Running `terraform apply` to provision all cloud resources — **not done yet**
- Populating Secret Manager with real API keys (Anthropic, GitHub) — **not done yet**
- Running the full pipeline end-to-end with a real user story — **not done yet**
- Deploying the API container to Cloud Run — **not done yet**

### Known Gap
The in-memory run store (`_runs` dict in the API) will lose history on Cloud Run restarts. This is a known limitation I have documented — the fix (Firestore) is in my backlog as a Phase 2 item.

---

## What Is Planned Next (Phase 2)

These are features I have scoped but not built yet — documented in [ENHANCEMENTS.md](ENHANCEMENTS.md):

| Feature | Why |
|---|---|
| Conversational Agent + Jira Integration | Chat-based requirement gathering that auto-creates and tracks Jira tickets |
| Persistent Run History (Firestore) | So run state survives server restarts |
| Wait for PR Approval Before Deploying | Only deploy to Dataproc after a human reviews and merges the PR |
| Agent Memory (RAG) | Let the AI learn from past accepted pipelines |
| Automated Data Quality Checks | Great Expectations suite after every pipeline run |
| Streaming Pipelines | Spark Structured Streaming support, not just batch |
| Dataproc Serverless | Lower cost — pay only when a job runs |
| Monitoring Dashboard | Cloud Monitoring metrics and alerts |

---

## Repository Structure Summary

```
src/
  agents/          ← 4 AI agents (task breakdown, code gen, test gen, PR)
  orchestrator/    ← LangGraph state machine connecting all agents
  api/             ← FastAPI web service
  llm/             ← Claude AI client wrapper
  gcp/             ← GCS and Dataproc clients
  github/          ← GitHub API client

config/            ← YAML prompt templates and framework standards
tests/             ← Unit and integration tests + sample fixtures
orchestration/     ← Airflow DAG template
infra/
  terraform/       ← All GCP infrastructure as code
  docker/          ← Dockerfile for Cloud Run deployment
.github/workflows/ ← CI/CD pipeline
docs/              ← Architecture, API reference, setup guides, runbook
notebooks/         ← Amazon campaign RFM demo notebook
```

---

## Technologies Used

| Layer | Technology |
|---|---|
| AI / LLM | Anthropic Claude (claude-3-5-sonnet) |
| Agent Framework | LangChain + LangGraph |
| Compute | GCP Dataproc (Apache Spark) |
| Data Format | PySpark + Delta Lake |
| Orchestration | Cloud Composer (Managed Airflow) |
| API | FastAPI on Cloud Run |
| Infrastructure | Terraform |
| CI/CD | GitHub Actions |
| Storage | Google Cloud Storage |
| Containerisation | Docker |
| Testing | pytest |
