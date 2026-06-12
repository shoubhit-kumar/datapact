# ADR-002: Service Bus for Async Analysis Pipeline

**Status:** Accepted  
**Date:** June 2026  
**Author:** Shoubhit Kumar

## Context

A full DataPact analysis involves:
1. SQL profiling (multiple queries)
2. RAG query to AI Search
3. Two OpenAI API calls (uniqueness reasoning + doc rule generation)
4. Saving 37+ rules and violations to SQL

Total execution time: 45-90 seconds. Too long for a synchronous HTTP request from Power Apps.

## Decision

Use Azure Service Bus Basic tier with a single queue `datapact-analysis-queue`.

Flow:
1. Power Apps calls `QueueAnalysis` → inserts PENDING run → publishes message → returns `run_id` immediately
2. Service Bus triggers `DataPactAnalysisWorker` → runs full pipeline → updates status to COMPLETED
3. Power Apps polls `datapact_runs` every 5 seconds until status = COMPLETED

## Alternatives Considered

| Option | Rejected because |
|---|---|
| Synchronous HTTP | Times out after 230s, blocks Power Apps UI |
| Azure Durable Functions | More complex, overkill for single-step workflow |
| Azure Logic Apps | Too expensive, limited Python support |
| Polling Azure Function | Creates unnecessary compute load |

## Consequences

**Positive:**
- Non-blocking UI — user can navigate away and return
- Retry logic built into Service Bus (max 10 retries)
- Dead-letter queue catches permanently failed analyses
- Decouples profiling from rule generation

**Negative:**
- Duplicate processing if Service Bus retries before first attempt completes — mitigated by idempotent `save_rules` with duplicate check
- 5-second polling adds minor latency to results display