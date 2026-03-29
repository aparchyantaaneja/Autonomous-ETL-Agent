# Pending Enhancements — Autonomous ETL Agent

This is my personal backlog of features I plan to add on top of the Phase 1 implementation. Phase 1 is complete and covers the full core pipeline (user story → PySpark code → tests → GitHub PR → Dataproc → Airflow). Everything listed here is what comes next.

---

## What's In Progress (Phase 1)

- AI agents that read a user story and generate production-ready PySpark code
- Automatic test generation (pytest suite)
- GitHub PR creation with the generated code
- Deployment to GCP Dataproc
- Airflow DAG scheduling via Cloud Composer
- REST API (FastAPI on Cloud Run)
- Full GCP infrastructure via Terraform
- CI/CD pipeline via GitHub Actions

---

## What's Planned in Phase 2 and 3

---

### 1. Conversational Agent + Jira Integration
**Priority: High**

Right now someone has to manually write and submit a JSON user story to the API — there's no guided experience. I want to add a **chat-based agent** that talks to the user first, asks clarifying questions about the ETL requirement, and only submits once everything is confirmed.

On top of that, it will automatically create a **Jira ticket**, move it to **In Progress** when work starts, and update it with the PR link and run status when the pipeline finishes.

**Example conversation:**

```
Me:    "I need a pipeline for RFM scoring on Amazon customer transactions"

Agent: "Sure! A few quick questions:
        - What's the source data location?
        - What time window for recency? (e.g. last 90 days)
        - Where should the output go?
        - Any rows to exclude?"

Me:    "Source: gs://raw/transactions, 90 days, output to customer_segments table,
        exclude cancelled orders"

Agent: "Here's the plan — does this look right?
        Filter cancelled → Calculate RFM scores → Upsert to customer_segments"

Me:    "Yes"

Agent: "✓ Jira ticket ETL-42 created and moved to In Progress
        ✓ Pipeline submitted — tracking ID: abc-123"
```

When pipeline finishes:
- Jira ticket gets updated with the GitHub PR link
- If it fails, ticket moves to **Needs Review** with the error reason

---

### 2. Persistent Run History (Firestore)
**Priority: High**

Currently all pipeline run history is stored in-memory, so it disappears every time the server restarts. I plan to move this to **Google Cloud Firestore** so run history is permanently saved and survives restarts.

---

### 3. Wait for PR Approval Before Deploying
**Priority: Medium**

Right now the pipeline marks itself as complete right after creating the PR — even though nobody has reviewed or merged the code yet. I want it to **pause** after PR creation and only trigger the Dataproc deployment automatically once the PR is actually merged on GitHub.

---

### 4. Agent Memory — Learn from Past Pipelines
**Priority: Medium**

Every new story is treated completely from scratch. I want the AI to **remember past pipelines** that were accepted and use them as examples when generating new code. This makes the output progressively better aligned with the patterns I prefer. Plan is to use Vertex AI Vector Search to store and retrieve similar past pipelines.

---

### 5. Automated Data Quality Checks (Great Expectations)
**Priority: Medium**

Phase 1 adds basic null checks inside the Spark script. I want to add a proper **data quality validation step** that runs after the pipeline using Great Expectations — checking things like column distributions, referential integrity, and freshness — and writes a quality report to GCS.

---

### 6. Streaming Pipelines
**Priority: Low**

Phase 1 only handles batch jobs. I want to be able to say "this is a streaming pipeline" and have the agent generate a **Spark Structured Streaming** job that reads from Pub/Sub and writes to BigQuery continuously instead of batch.

---

### 7. Switch to Dataproc Serverless
**Priority: Low**

Phase 1 uses a persistent Dataproc cluster that costs money even when idle. I plan to switch to **Dataproc Serverless** which only charges when a job is actually running — much more cost-effective for intermittent ETL jobs.

---

### 8. Monitoring Dashboard
**Priority: Low**

No visual overview of pipeline health right now. I want to build a **Cloud Monitoring dashboard** showing pipeline success rate, how long each run takes, LLM cost per story, and PR merge rate — with alerts if error rate spikes.

---

## Summary

| # | Feature | Priority | Status |
|---|---|---|---|
| 1 | Conversational Agent + Jira Integration | High | Pending |
| 2 | Persistent Run History (Firestore) | High | Pending |
| 3 | Wait for PR Approval Before Deploying | Medium | Pending |
| 4 | Agent Memory (RAG from past pipelines) | Medium | Pending |
| 5 | Automated Data Quality Checks | Medium | Pending |
| 6 | Streaming Pipeline Support | Low | Pending |
| 7 | Dataproc Serverless Migration | Low | Pending |
| 8 | Monitoring Dashboard | Low | Pending |
