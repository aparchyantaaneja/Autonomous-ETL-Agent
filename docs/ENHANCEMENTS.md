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

> **Note:** Agent Memory helps with *new* pipelines being generated in a consistent style with past ones. It does not directly handle modifying an existing pipeline — that is covered by Enhancement #9 below.

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

### 9. Incremental Pipeline Enhancement with Knowledge Store
**Priority: Top — implement this first in Phase 2**

This solves one of the most common real-world data engineering needs: **you already have a pipeline running in production and you want to add something to it** — a new column, a new transformation step, a new output dataset — without rewriting it from scratch.

Currently the only option is to submit a brand new story and generate a completely new pipeline, which creates duplication and doesn't touch the existing code at all.

**The request would look like this:**
```
"In the customer_segments pipeline (run abc-123), also calculate average order
 value per customer and add it as a new column avg_order_value in the output."
```

**Phase 2 — Basic approach (implement first):**
1. Fetch the existing PySpark script from GCS
2. Send the full script + change request to Claude: *"make only this one change, preserve everything else"*
3. Validate the modified code (syntax + structural checks)
4. Open a PR showing a diff — only the changed lines — against the current version

This works well for scripts under ~500 lines and is straightforward to build on top of Phase 1.

**Phase 3 — Knowledge Store approach (the smarter upgrade):**

Rather than dumping the whole script into the prompt, build a **semantic index** of each pipeline when it is first created. Every pipeline gets broken into logical chunks and stored with metadata:

| What gets indexed | Example entry |
|---|---|
| Transformation steps | `aggregate: RFM scores per customer using ntile(4)` |
| Column lineage | `total_spend and order_count exist; avg_order_value does not` |
| Output schema | `customer_id, shipping_region, rfm_total, customer_segment` |
| Business rules | `exclude cancelled orders (status != 'cancelled')` |

When an enhancement request comes in, instead of sending the full file:
1. **Query the knowledge store** with the enhancement description → retrieve only the relevant sections (e.g. the aggregation block, the output schema)
2. **Send Claude a focused, targeted prompt** rather than ~1000 lines of code
3. **Apply the change surgically** to only the relevant section

**What the knowledge store unlocks beyond just editing:**
- **Conflict detection** — catch "this column already exists" before calling Claude at all
- **Cross-pipeline reuse** — "a similar calculation exists in pipeline XYZ, reuse that pattern"
- **Impact analysis** — "if I change the order_count aggregation, these 3 downstream pipelines are affected"
- **Natural language search** — "which of my pipelines already does RFM scoring?"

**Technology:** Vertex AI Vector Search (already on GCP, no new infrastructure needed)

**Why this is different from Agent Memory (#4):**
Agent Memory helps *new* pipelines look like past ones — it's about style consistency. This enhancement actually *modifies a specific existing pipeline*. They complement each other: Agent Memory ensures the new code follows your conventions, the Knowledge Store ensures it integrates cleanly with the existing logic.

---

## Summary

| # | Feature | Priority | Status |
|---|---|---|---|
| 9 | Incremental Pipeline Enhancement + Knowledge Store | **Top** | Pending |
| 1 | Conversational Agent + Jira Integration | High | Pending |
| 2 | Persistent Run History (Firestore) | High | Pending |
| 3 | Wait for PR Approval Before Deploying | Medium | Pending |
| 4 | Agent Memory (RAG from past pipelines) | Medium | Pending |
| 5 | Automated Data Quality Checks | Medium | Pending |
| 6 | Streaming Pipeline Support | Low | Pending |
| 7 | Dataproc Serverless Migration | Low | Pending |
| 8 | Monitoring Dashboard | Low | Pending |
