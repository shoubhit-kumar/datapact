# DataPact

**AI-Powered Data Quality Config Generator**

> Reads whatever documentation you have — DDLs, data contracts, emails, Slack threads — and turns years of scattered knowledge into production-ready data quality rules in under 60 seconds. With human oversight built in. For any database. Any table. Any framework.

[![IBM Buildathon 2026](https://img.shields.io/badge/IBM-Buildathon%202026-blue)](https://github.com/shoubhit-kumar/datapact)
[![Azure Functions](https://img.shields.io/badge/Azure-Functions-blue)](https://azure.microsoft.com/en-us/products/functions)
[![Copilot Studio](https://img.shields.io/badge/Microsoft-Copilot%20Studio-purple)](https://copilotstudio.microsoft.com)
[![Python](https://img.shields.io/badge/Python-3.11-green)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## The Problem

Every data team has documentation scattered across dozens of files — DDLs with inline comments, data contracts in YAML, issue logs in Jira exports, business rules buried in email threads and Slack messages. Converting all of this into actual, running data quality rules is always manual, always slow, always incomplete, and immediately outdated the moment the schema changes.

The consequence is real: inspectors, analysts, and downstream consumers make decisions on data that has never been formally validated.

## The Solution

DataPact ingests all your existing documentation — in whatever form it exists — and automatically synthesises it into production-ready data quality configuration files compatible with:

- **Great Expectations** — `expectation_suite.json`
- **dbt tests** — `schema.yml` patch
- **Soda Checks** — `checks.yaml`

Every generated rule is cited — you know exactly which document it came from. Confidence scores tell you which rules are solid and which need human review. A human review portal lets data stewards approve or reject low-confidence rules before they enter the config.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         INPUT LAYER                              │
│  DDL files  │  Data contracts  │  Issue logs  │  Business docs  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    DOCUMENT INGESTION                            │
│         Blob Storage → Document Intelligence → AI Search        │
└──────────────────────────┬──────────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
┌─────────────────────┐    ┌────────────────────────────────────┐
│   POWER APPS UI     │    │      COPILOT STUDIO AGENT          │
│  DB + Table select  │    │   Conversational rule generation   │
│  Document upload    │    │   Human review in chat             │
│  Results dashboard  │    │                                    │
└──────────┬──────────┘    └────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    AZURE SERVICE BUS                             │
│              Async analysis queue                                │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   AZURE FUNCTION (Core Engine)                   │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │   Profiler  │  │  AI Reasoner │  │   RAG Rule Generator  │  │
│  │  Schema     │  │  GPT-4.1     │  │   AI Search + OpenAI  │  │
│  │  Null rates │  │  Uniqueness  │  │   Document-backed      │  │
│  │  Ranges     │  │  Enum filter │  │   rules with citation  │  │
│  │  Cardinality│  │              │  │                        │  │
│  └─────────────┘  └──────────────┘  └───────────────────────┘  │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Config Generator                            │   │
│  │   Great Expectations  │  dbt schema  │  Soda Checks     │   │
│  └─────────────────────────────────────────────────────────┘   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      AZURE SQL                                   │
│  datapact_runs  │  datapact_rules  │  datapact_decisions        │
│  datapact_violations  │  datapact_documents                     │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    POWER BI DASHBOARD                            │
│     Trust score  │  Violations  │  Decision history             │
└─────────────────────────────────────────────────────────────────┘
```

---

## Key Features

### Generic — Works on Any Table
DataPact dynamically discovers columns from `INFORMATION_SCHEMA`. No hardcoding. Proven on `inspection_sites`, `inspectors`, and `inspection_records` with zero code changes.

### AI-Powered Uniqueness Detection
GPT-4.1-mini reasons over schema metadata, cardinality ratios, column comments, and sample values to identify which columns are logically intended to be unique — without relying on fragile naming conventions.

### Document-Backed Rules with Citations
Every rule generated from a document includes the source file name and exact clause. Rules generated from email threads include the sender and date.

```yaml
- rule_code: DQ-R27-500
  column_name: site_id
  rule_description: Must match pattern SITE-[0-9]{5}
  confidence: 0.95
  status: VALIDATED
  source_tools: DataPactAnalysisWorker+OpenAI+RAG
  evidence: "08_email_governance_decisions.txt: AGREED RULES #1 — pattern confirmed"
```

### Human Review Loop
Rules below confidence 0.75 enter a human review queue. Data stewards approve or reject via Power Apps or Copilot Studio chat. Every decision is persisted with timestamp, reviewer, and reasoning.

### Legal Exception Awareness
DataPact reads email threads and extracts business exceptions:
```
"Exception: The 14 disputed duplicate records from INC-2024-0041 are under legal
review. Do not auto-reject them. Flag but do not fail pipeline."
```

### Async Production Pipeline
Built on Azure Service Bus — analysis requests are queued and processed asynchronously. Power Apps polls for completion. No blocking, no timeouts.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Core engine | Azure Functions (Python 3.11) |
| Async pipeline | Azure Service Bus |
| LLM reasoning | Azure OpenAI (GPT-4.1-mini) |
| Embeddings | Azure OpenAI (text-embedding-ada-002) |
| Document parsing | Azure Document Intelligence |
| Vector search | Azure AI Search (HNSW cosine) |
| Document storage | Azure Blob Storage + Data Lake Gen2 |
| Metadata store | Azure SQL Database (Serverless) |
| Agent | Microsoft Copilot Studio (Claude Sonnet 4.6) |
| Workflow | Power Automate |
| Portal | Power Apps Canvas |
| Dashboard | Power BI via Microsoft Fabric |
| Observability | Azure App Insights |

---

## API Reference

### Core Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/QueueAnalysis` | POST | Queue a table for async analysis |
| `/api/DataPactProfiler` | POST | Direct profiling (used by Copilot agent) |
| `/api/GenerateConfigs` | POST | Generate GE/dbt/Soda configs for a run |
| `/api/UploadDocument` | POST | Upload document to Blob + create DB record |
| `/api/ProcessDocument` | POST | Parse + chunk + embed + index document |
| `/api/SearchDocuments` | POST | RAG query over indexed documents |
| `/api/ListDatabases` | GET | List available databases |
| `/api/ListTables` | POST | List tables in a database |

### QueueAnalysis Request
```json
{
  "table_name":    "inspection_sites",
  "database_name": "inspectiondb",
  "triggered_by":  "Shoubhit Kumar"
}
```

### QueueAnalysis Response
```json
{
  "status":  "queued",
  "run_id":  27,
  "message": "Analysis queued for inspection_sites in inspectiondb"
}
```

---

## Database Schema

### Metadata Tables

```sql
-- Every profiling run
datapact_runs (
    run_id, table_name, triggered_by, total_rows,
    tools_used, status, run_timestamp
)

-- Every generated rule
datapact_rules (
    rule_id, run_id, rule_code, table_name, column_name,
    rule_description, confidence, status, source_tools,
    evidence, conflict_detected, conflict_detail, created_at
)

-- Every human decision
datapact_decisions (
    decision_id, rule_id, rule_code, table_name, column_name,
    decision, decision_reason, condition_applied, decided_by,
    decided_at, final_confidence, final_status
)

-- Every detected violation
datapact_violations (
    violation_id, run_id, rule_code, table_name, column_name,
    violation_type, severity, affected_rows, violation_detail,
    sample_values, remediation_sql, status, detected_at
)

-- Indexed documents
datapact_documents (
    doc_id, table_name, database_name, doc_name, doc_type,
    blob_url, status, chunks_indexed, uploaded_by,
    uploaded_at, processed_at
)
```

---

## Getting Started

### Prerequisites
- Azure subscription with Owner access
- Python 3.11
- Azure Functions Core Tools v4
- Azure CLI

### 1. Clone the repo
```bash
git clone https://github.com/shoubhit-kumar/datapact.git
cd datapact
```

### 2. Configure environment
```bash
cp src/function_app/local.settings.json.example src/function_app/local.settings.json
# Fill in your values
```

### 3. Deploy the function app
```bash
cd src/function_app
func azure functionapp publish YOUR_FUNCTION_APP_NAME --python
```

### 4. Create database tables
Run `scripts/create_tables.sql` in Azure SQL Query Editor.

### 5. Upload sample documents
```bash
cd scripts
python upload_all_docs.py
```

### 6. Run your first analysis
```bash
curl -X POST "https://YOUR_FUNC.azurewebsites.net/api/queueanalysis?code=YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"table_name": "your_table", "database_name": "your_db", "triggered_by": "Your Name"}'
```

---

## Rule Generation Pipeline

```
1. Schema profiling      → column types, nullability, constraints
2. Statistical profiling → null rates, distinct values, ranges, cardinality
3. RAG query             → retrieve relevant document chunks
4. AI reasoning          → uniqueness detection, enum filtering
5. Rule generation       → data-driven rules from profiling
6. Doc rule generation   → document-backed rules from RAG
7. Confidence scoring    → weighted by source reliability
8. Human review queue    → rules below 0.75 flagged for approval
9. Config generation     → GE + dbt + Soda files to Blob Storage
```

---

## Confidence Scoring

| Source | Weight | Example |
|---|---|---|
| DB constraint (PK/UNIQUE) | 0.98 | `NOT NULL` enforced in DDL |
| Data contract | 0.95 | Explicitly stated in YAML |
| Schema IS_NULLABLE=NO | 0.95 | DDL schema |
| Email/Slack governance decision | 0.95 | Agreed rules document |
| 0% null rate observed | 0.85 | NullProfiler |
| High cardinality + name pattern | 0.85 | Uniqueness inference |
| Low cardinality enum | 0.80 | DistinctValues profiler |
| Range from sample data | 0.75 | RangeProfiler |
| Null rate flagged | 0.65 | NullProfiler |
| High null rate | 0.55 | NullProfiler |

---

## Sample Input Documents

DataPact works with whatever documentation you have:

| Type | Format | What DataPact extracts |
|---|---|---|
| DDL / SQL schema | `.sql` | Column types, constraints, inline comments |
| Data contract | `.yaml` / `.json` | Allowed values, patterns, SLAs, nullability |
| Issue log | `.csv` / `.xlsx` | Historical failures, known data quality gaps |
| Business wiki | `.txt` / `.pdf` | Domain rules, zone classifications, regulatory constraints |
| Slack export | `.txt` | Informal data decisions, format confirmations |
| Email thread | `.txt` | Governance decisions, legal exceptions |
| Sample data | `.csv` | Pattern inference, value distributions |

---

## Team

**Team Null Hypothesis** — IBM Buildathon 2026

- Shoubhit Kumar — Data Engineer, IBM Kolkata

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Acknowledgements

Built during IBM Buildathon 2026 using Microsoft Azure, Copilot Studio, and Power Platform.
The insight comes from 3 years of real data engineering pain. The execution required
multi-agent orchestration, RAG implementation, and production Azure architecture.