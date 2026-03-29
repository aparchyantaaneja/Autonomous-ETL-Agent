# Architecture — Autonomous ETL Agent

## System Overview

The Autonomous ETL Agent is a **multi-agent system** built on LangGraph that autonomously transforms DevOps user stories into production-ready PySpark pipelines, complete with tests, documentation, and GitHub PRs — deployed to GCP Dataproc and scheduled via Cloud Composer.

---

## Agent Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI / Cloud Run                       │
│                  POST /stories → async job                   │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                  LangGraph StateGraph                        │
│                                                             │
│   ┌──────────────────┐                                      │
│   │ Task Breakdown   │  UserStory → ETLSpec                 │
│   │ Agent            │  (LangChain + Claude)                │
│   └────────┬─────────┘                                      │
│            │                                                │
│   ┌────────▼─────────┐                                      │
│   │ Code Generator   │  ETLSpec → PySpark Script + Notebook │
│   │ Agent            │  (LangGraph state machine + Claude)  │
│   └────────┬─────────┘                                      │
│            │                                                │
│   ┌────────▼─────────┐                                      │
│   │ Test Generator   │  Code → pytest suite + fixtures      │
│   │ Agent            │  (Claude + template validation)      │
│   └────────┬─────────┘                                      │
│            │                                                │
│   ┌────────▼─────────┐                                      │
│   │ Upload Artifacts │  All files → GCS                     │
│   │ (GCSClient)      │                                      │
│   └────────┬─────────┘                                      │
│            │                                                │
│   ┌────────▼─────────┐                                      │
│   │ PR Agent         │  Branch + Commit + PR (PyGithub)     │
│   └────────┬─────────┘                                      │
│            │  (optional)                                    │
│   ┌────────▼─────────┐                                      │
│   │ Dataproc Deploy  │  Submit PySpark job to Dataproc      │
│   └────────┬─────────┘                                      │
│            │  (optional)                                    │
│   ┌────────▼─────────┐                                      │
│   │ Airflow DAG Gen  │  Generate + upload DAG to Composer   │
│   └──────────────────┘                                      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## LangGraph State Machine

The orchestrator is implemented as a `StateGraph` in [`src/orchestrator/graph.py`](../src/orchestrator/graph.py).

### State Object (`AgentState`)

```python
class AgentState(BaseModel):
    run_id: str
    status: RunStatus            # PENDING | RUNNING | DONE | FAILED
    story: UserStory
    etl_spec: ETLSpec            # output of Task Breakdown Agent
    generated_code: GeneratedCode # output of Code Generator
    generated_tests: GeneratedTests # output of Test Generator
    artifact_uris: dict[str, str] # GCS URIs after upload
    pr_result: PRResult          # GitHub PR URL and number
    dataproc_job_id: str         # (optional)
    airflow_dag_id: str          # (optional)
    error_step: str              # set if any node raises
    error_message: str
```

### Node → Next Node Routing

| From | To (success) | To (error) |
|---|---|---|
| `task_breakdown` | `code_generation` | `handle_error` |
| `code_generation` | `test_generation` | `handle_error` |
| `test_generation` | `upload_artifacts` | `handle_error` |
| `upload_artifacts` | `create_pr` | `handle_error` |
| `create_pr` | `deploy_dataproc` or END | `handle_error` |
| `deploy_dataproc` | `generate_dag` or END | `handle_error` |
| `generate_dag` | END | `handle_error` |
| `handle_error` | END | — |

---

## Agent Internals

### Task Breakdown Agent

**Input:** `UserStory` (id, title, description, acceptance criteria)  
**Output:** `ETLSpec` (operations list, source/target datasets, quality requirements)

Flow:
1. Format story JSON + framework config into prompt template
2. Call Claude with `complete_json()` → validated `ETLSpec` via Pydantic
3. Log operation types extracted

Claude prompt: `config/agent_prompts.yaml` → `task_breakdown`

---

### Code Generator Agent

**Input:** `ETLSpec`  
**Output:** `GeneratedCode` (PySpark `.py` script + `.ipynb` notebook)

Flow:
1. Call Claude → raw code string
2. Strip markdown fences
3. Run `_validate_code()` static checks:
   - Python `compile()` syntax check
   - Structural checks (SparkSession, `__main__` guard, argparse, logging)
   - Operation coverage checks (each op type has expected patterns)
4. If issues found → call Claude refinement prompt (up to 2 attempts)
5. Wrap script in educational Jupyter notebook

---

### Test Generator Agent

**Input:** PySpark script + `ETLSpec`  
**Output:** `GeneratedTests` (conftest.py, test file, fixtures JSON)

Generates:
- `conftest.py` with shared `SparkSession` fixture (`scope="session"`)
- Unit tests for every transformation function
- Schema validation tests (correct column names and types)
- Null check tests for key columns
- Business logic assertions
- `fixtures/*.json` with realistic mock records

---

### PR Agent

**Input:** `ETLSpec`, `GeneratedCode`, `GeneratedTests`  
**Output:** `PRResult` (PR URL, PR number, branch name, commit SHA)

Workflow:
1. Generate PR title + description body via Claude
2. Generate commit message via Claude
3. Create GitHub feature branch (`etl/auto/{story-id}`)
4. Commit all 5 files in one git tree commit (uses GitHub Trees API for atomicity)
5. Open PR with labels: `auto-generated`, `etl-pipeline`, `needs-review`

---

## GCP Infrastructure

```
┌──────────────────────────────────────────────────────┐
│                   GCP Project                         │
│                                                       │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  GCS     │  │   Dataproc   │  │ Cloud Composer │  │
│  │  Buckets │  │   Cluster    │  │ (Airflow)      │  │
│  │          │  │              │  │                │  │
│  │ raw-data │  │ PySpark jobs │  │ ETL DAGs       │  │
│  │ processed│  │ (ephemeral   │  │                │  │
│  │ artifacts│  │  or persistent)                  │  │
│  └──────────┘  └──────────────┘  └────────────────┘  │
│                                                       │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │  Cloud   │  │   Secret     │  │   Artifact     │  │
│  │  Run     │  │   Manager    │  │   Registry     │  │
│  │  (API)   │  │              │  │   (Docker)     │  │
│  └──────────┘  └──────────────┘  └────────────────┘  │
└──────────────────────────────────────────────────────┘
```

### Data Flow

```
Raw Data (GCS)
    │
    ▼ Spark reads
Dataproc Job (PySpark)
    │
    ▼ Delta Lake write
Processed Data (GCS)
    │
    ▼ Airflow monitors
Cloud Composer DAG → success/failure notification
```

---

## Security Model

- All secrets stored in **GCP Secret Manager** (never in env vars in Cloud Run config or code)
- Cloud Run uses **Workload Identity** — no key files on disk
- Service accounts follow **least-privilege**: ETL Agent SA cannot create/delete clusters
- API endpoints protected by **API key** (header `X-API-Key`)
- IAM bindings defined in Terraform, reviewed via PR
- Generated code commits reviewed by humans before merge (auto-label `needs-review`)

---

## Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Agent framework | LangGraph | Explicit state machine, better error recovery than simple chains |
| LLM | Anthropic Claude | Best code generation quality for structured Python/PySpark |
| Code validation | Compile + structural checks | Fast, no Spark runtime needed during generation |
| Notebook format | Jupyter `.ipynb` | Runnable in Dataproc/Colab for educational demos |
| GCS artifact storage | Per run-ID prefix | Easy cleanup, clear audit trail |
| PR commit strategy | Git Trees API single commit | Atomic — all files committed together |
| Dataproc strategy | Ephemeral per job | Cost-optimal for batch workloads |
| State persistence | In-memory (MVP) | Simple; replace with Firestore for multi-instance |
