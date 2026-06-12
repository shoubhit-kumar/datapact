# ADR-001: Azure Function as Core Engine

**Status:** Accepted  
**Date:** June 2026  
**Author:** Shoubhit Kumar

## Context

DataPact needs a compute layer that can:
- Run dynamic SQL profiling against any database
- Call Azure OpenAI for reasoning
- Query Azure AI Search for RAG
- Process documents via Document Intelligence
- Be triggered from both Power Automate (Copilot Studio) and Service Bus

## Decision

Use Azure Functions (Python 3.11, Consumption plan) as the core engine with multiple HTTP triggers and one Service Bus trigger.

## Alternatives Considered

| Option | Rejected because |
|---|---|
| Power Automate only | Cannot run dynamic SQL, no Python, connector limitations |
| Azure Container Apps | Over-engineered for current scale, higher cost |
| Azure Databricks | Too heavy, not suitable for real-time API calls |
| Direct Copilot Studio | Cannot run arbitrary Python, no persistent storage |

## Consequences

**Positive:**
- Serverless — zero cost when idle
- Python 3.11 — full access to pyodbc, azure-search, azure-ai-formrecognizer
- HTTP + Service Bus triggers in one deployment
- Scales automatically under load

**Negative:**
- 5-minute execution limit on Consumption plan — mitigated by async Service Bus pattern
- Cold start latency — mitigated by warming via QueueAnalysis pre-call
- SQL connection timeout on cold start — mitigated by 60s timeout setting