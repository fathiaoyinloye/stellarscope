# StellarScope — Confirmed Architectural and Technical Decisions

This document records architectural and technical decisions made during Phase 1 (Requirements and Technical Research) and Phase 3 (Database Design and Implementation). Each decision includes a brief rationale.

## 1. Technology Stack

| Decision | Rationale |
|---|---|
| FastAPI as the HTTP API framework | Async-ready, lightweight, built on Starlette; aligns with approved tech roadmap. |
| SQLAlchemy 2.x async with asyncpg | Modern async ORM pattern; supports async database operations natively. |
| Alembic for database migrations | Standard SQLAlchemy migration tool; lightweight; no unnecessary ORM abstraction. |
| stellar-sdk (py-stellar-base) for Stellar RPC/XDR | Official Python SDK; provides `SorobanServer.get_transaction()`, XDR decoding, and RPC client utilities. |
| HTTPX for async HTTP client | Native async HTTP client; supports HTTP/1.1 and HTTP/2; works well with FastAPI's async model. |
| PostgreSQL as the primary data store | Relational data model fits transaction monitoring; `SELECT ... FOR UPDATE SKIP LOCKED` enables worker coordination without external queuing services. |
| pytest + pytest-asyncio for testing | Standard Python testing stack; async support essential for FastAPI and RPC calls. |
| Docker + Docker Compose for deployment | Portable execution environment; separates concerns (app, DB, worker). |
| GitHub Actions for CI/CD | Hosted CI; integrates with repository; supports test running and Docker image builds. |

## 2. Architecture: Modular Monolith

| Decision | Rationale |
|---|---|
| All application code in `src/stellarapi/` package | Keeps things simple for MVP; avoids micro‑overhead while maintaining modularity. |
| Separate sub‑packages for API routes, core logic, services, and worker (future) | Clear separation of concerns; makes it easy to extract the worker as a separate process later. |
| Background worker executed as a separate process (not Celery/Rediс) | Aligns with constraints: no Redis, no Celery. Worker coordination via PostgreSQL `FOR UPDATE SKIP LOCKED`. |
| FastAPI app and worker share the same code base | Single source of truth for routes, dependencies, and configuration. |

## 3. Transaction Monitoring Lifecycle

| Decision | Rationale |
|---|---|
| Three RPC statuses only: `SUCCESS`, `FAILED`, `NOT_FOUND` | Per Stellar RPC spec; no `PENDING` status from `getTransaction`. |
| Keep RPC status separate from monitoring lifecycle | Monitoring state (`pending`/`processing`/`done`/`observed`) is independent of RPC terminal status. |
| `SUCCESS` and `FAILED` are terminal | Once recorded, do not re‑poll. |
| `NOT_FOUND` does **not** prove pending/never existed/aged out | Must preserve raw response; do not overwrite terminal results. |
| Preserve raw RPC response and base64 XDR as evidence | XDR may become unavailable after RPC retention window (~7 days). |
| RPC errors must not be treated as transaction failures | Retry on RPC errors; do not update status to `FAILED`. |
| Handle fee‑bump by inspecting inner transaction result XDR | Do not assume normal `TransactionResult` structure for fee‑bump transactions. |
| Do not overwrite a previously recorded terminal result with a later `NOT_FOUND` | Data integrity; preservation of evidence. |
| Observation deadline = StellarScope stopped monitoring, not transaction expired | Clear semantics for operators; does not mean the on‑chain transaction expired. |

## 4. Database Design (Phase 3)

| Decision | Rationale |
|---|---|
| Single `monitoring_jobs` table | Keeps MVP schema minimal; all required data in one table; no need for JOINs for core operations. |
| UUID primary keys | Standard primary key pattern; avoids sequence/identity issues with scaling; `gen_random_uuid()` for PostgreSQL. |
| `CHAR(64)` for `transaction_hash` | Exact length for Stellar transaction hashes; enforces format at DB level. |
| Separate `rpc_status` and `monitoring_status` columns | Keeps RPC terminal status (`SUCCESS`/`FAILED`/`NOT_FOUND`) distinct from StellarScope monitoring lifecycle (`ACTIVE`/`COMPLETED`/`OBSERVATION_EXPIRED`). |
| `monitoring_status` values: `ACTIVE`/`COMPLETED`/`OBSERVATION_EXPIRED` | Maps to the three lifecycle states; `ACTIVE` = being monitored, `COMPLETED` = terminal result recorded, `OBSERVATION_EXPIRED` = deadline reached. |
| `JSONB` for `raw_rpc_response` | Preserves full RPC JSON response; supports future querying/extraction; avoids adding many individual columns that may become stale. |
| `PG_TIMESTAMP(timezone=True)` for all timestamp columns | Timezone-aware timestamps; critical for worker lease coordination and `next_check_at` scheduling. |
| Partial indexes on `status = 'pending'` and `status = 'processing'` | Enables `SELECT ... FOR UPDATE SKIP LOCKED` to claim only unlocked rows; supports concurrent workers without processing the same job. |
| Partial index on `locked_until` | Supports stale lease recovery cleanup cycle; finds jobs with expired locks. |
| Check constraints on `rpc_status` and `monitoring_status` | Ensures data integrity at the database level; prevents invalid status values. |
| `transaction_hash UNIQUE` constraint | Prevents duplicate registrations; one hash → one monitoring job. |
| `fee_bump` BOOLEAN column | Tracks whether transaction was fee-bumped; application must inspect `result_xdr` for inner result when True. |
| `failure_reason TEXT` (optional) | Human-readable failure explanation; populated from JSONB payload when available but not guaranteed. |
| No separate `diagnostic_events_xdr` column | Diagnostic events stored within `raw_rpc_response` JSONB to avoid PostgreSQL array type complications and schema complexity; events can be extracted from JSONB as needed. |

## 5. RPC Retention & Observability

| Decision | Rationale |
|---|---|
| Default RPC retention window ~7 days (120960 ledgers) | Configurable via `HISTORY_RETENTION_WINDOW` on the RPC server. |
| Transactions older than the retention window may return `NOT_FOUND` | Must not interpret this as "transaction failed/predicted." |
| Periodically call `getHealth()` to check `oldest_ledger` and `ledger_retention_window` | Informs per‑transaction "expired at" calculations. |
| Observation deadline is not an on-chain expiry | Documents a subtle but critical semantic for operators. |
| Raw RPC JSON and all available base64 XDR fields stored in DB | Evidence preservation; even if XDR decoding fails later, the raw data is retained. |

## 6. Polling & Retry Policy

| Decision | Rationale |
|---|---|
| Initial polling at 1s for 30 attempts, then 5s, then 10s | Aggressive early detection; reduces RPC load as monitoring extends. |
| Exponential backoff on RPC errors (1s, 2s, 4s, 8s, 16s, 30s cap) | Prevents thundering herd; gives RPC endpoint time to recover. |
| Maximum 20 consecutive RPC errors before logging warning and pausing | Prevents infinite loops on persistently broken endpoints. |
| Observation deadline = `first_poll_at + max_monitoring_minutes` (default 60 min) | Clear timeout; after this, worker stops polling this transaction. |
| `NOT_FOUND` is not an error; continue polling until terminal state or observation deadline | Aligns with domain rules and RPC semantics. |

## 7. What Was NOT Chosen (and Why)

| Decision | Rationale |
|---|---|
| No Redis, no Celery, no message broker | Project constraints explicitly exclude these; PostgreSQL‑based worker coordination selected instead. |
| No frontend/UI | MVP is backend‑only; frontend can be added later. |
| No Mainnet support in MVP | Targets Testnet only; Mainnet requires retention window configuration and possibly different RPC providers. |
| No automatic contract discovery or network-wide indexing | Out of scope for MVP; manually registered transaction hashes only. |
| No WebSocket streaming | Poll‑based monitoring keeps architecture simpler and aligns with bounded RPC retention. |
| No guaranteed human‑readable failure explanations | Diagnostic events may be absent; raw XDR preserved as fallback. |
| No overwrite of terminal results with `NOT_FOUND` | Data integrity rule; documented and will be enforced in software. |

## 8. Decisions Requiring Future Verification (Open Questions)

| Decision | Verification Needed | Status |
|---|---|---|
| How long `NOT_FOUND` persists after ledger close | Testnet polling over time | Open (see docs/risks-and-open-questions.md §10) |
| Diagnostic events availability vs `ENABLE_SOROBAN_DIAGNOSTIC_EVENTS` config | Testnet RPC node config analysis | Open |
| Exact ledger cadence on Testnet & mapping to 7‑day retention | Measure ledger intervals on soroban-testnet.stellar.org | Open |
| `resultXDR` structure for fee‑bump transactions | Submit fee‑bump txs on Testnet; inspect XDR | Open |
| Behaviour at RPC retention window boundary | Monitor known tx as it ages past window | Open |
| `getHealth()` `oldest_ledger` stability over time | Poll `getHealth()` at intervals | Open |

## 9. How to Reference This Document

- Add new decisions as the project evolves, keeping this file in the repository root.
- When making a change, record the decision, the rationale, and whether it replaces or supersedes an existing entry.
- Reference this file in CODEOWNERS or CONTRIBUTING.md so new contributors understand the decision‑making process.

## 10. Phase 3 Database Design Decisions

| Decision | Rationale |
|---|---|
| Single `monitoring_jobs` table | Keeps MVP schema minimal; all required data in one table; no need for JOINs for core operations. |
| UUID primary keys with `gen_random_uuid()` | Standard primary key pattern; avoids sequence/identity issues with scaling. |
| `CHAR(64)` for `transaction_hash` | Exact length for Stellar transaction hashes; enforces format at DB level. |
| Separate `rpc_status` and `monitoring_status` columns | Keeps RPC terminal status distinct from StellarScope monitoring lifecycle; enables the "never overwrite" rule. |
| `monitoring_status` values: `ACTIVE`/`COMPLETED`/`OBSERVATION_EXPIRED` | maps to the three lifecycle states; `ACTIVE` = being monitored, `COMPLETED` = terminal result recorded, `OBSERVATION_EXPIRED` = deadline reached. |
| `JSONB` for `raw_rpc_response` | Preserves full RPC JSON response; supports future querying/extraction; avoids adding many individual columns that may become stale. |
| `PG_TIMESTAMP(timezone=True)` for all timestamp columns | Timezone-aware timestamps; critical for worker lease coordination and `next_check_at` scheduling. |
| Partial indexes on `status = 'pending'` and `status = 'processing'` | Enables `SELECT ... FOR UPDATE SKIP LOCKED` to claim only unlocked rows; supports concurrent workers without processing the same job. |
| Partial index on `locked_until` | Supports stale lease recovery cleanup cycle; finds jobs with expired locks. |
| Check constraints on `rpc_status` and `monitoring_status` | Ensures data integrity at the database level; prevents invalid status values. |
| `transaction_hash UNIQUE` constraint | Prevents duplicate registrations; one hash → one monitoring job. |
| No separate `diagnostic_events_xdr` column | Diagnostic events stored within `raw_rpc_response` JSONB to avoid PostgreSQL array type complications and schema complexity. |