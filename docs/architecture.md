# StellarScope Architecture

This document describes the major application components, their responsibilities,
and how they communicate. It is intended for new developers who need to
understand the system design without reading all the source code.

## Overall Style: Modular Monolith

All application code lives in the `src/stellarapi/` package. This keeps things
simple for an MVP while maintaining modularity and clear separation of concerns.
The background worker runs as a separate async process that shares the same code
base.

```
src/stellarapi/
├── api/           - FastAPI route handlers (HTTP)
├── core/          - Core utilities (logging)
├── database/      - SQLAlchemy models, engine, session
├── monitoring/    - Worker, orchestration, job claiming
├── rpc_client.py  - Async Stellar RPC client
├── xdr/           - XDR decoder module
└── __init__.py
```

## Major Components

### 1. FastAPI Application (`src/stellarapi/main.py`)

- Creates the FastAPI app and registers routers
- Health endpoint: `GET /health` (no database dependency)
- Transaction API: `POST /register`, `GET /{hash}`, `GET /`
- Passes the `settings` config and `async_engine` to dependencies

### 2. API Routes (`src/stellarapi/api/`)

- **`health.py`** — `GET /health` health check response
- **`transactions.py`** —
  - `POST /api/v1/transactions/register` — validate hash, persist job with
    `monitoring_status=ACTIVE`, `rpc_status=pending`; does NOT synchronously
    poll Stellar RPC
  - `GET /api/v1/transactions/{hash}` — retrieve monitoring info, decode XDR
    diagnostic events where available
  - `GET /api/v1/transactions/` — list with pagination, filtering by
    `monitoring_status`/`rpc_status`, deterministic ordering by `created_at`

### 3. RPC Client (`src/stellarapi/rpc_client.py`)

- Async wrapper around `stellar_sdk.SorobanServer.get_transaction`
- Returns a normalized `GetTransactionResult` dataclass
- Handles `SUCCESS`, `FAILED`, `NOT_FOUND` statuses
- Custom exceptions: `RpcCommunicationError` (network/timeouts),
  `RpcResponseError` (malformed response), `XdrDecodingError`,
  `UnsupportedXdrStructure`
- Configurable timeout via `settings.RPC_TIMEOUT_SECONDS` (default 30s)
- Preserves raw RPC response in its entirety for evidence
- Fee-bump transaction handling: inspects inner result XDR rather than assuming
  normal top-level structure

### 4. XDR Decoder (`src/stellarapi/xdr/decoder.py`)

- Best-effort decoding: any field that cannot be extracted is `None`/empty,
  never an exception (except invalid base64, which is logged)
- Transaction result decoding with trailing-bytes handling (Soroban RPC quirk:
  trims last 4 bytes on `ValueError`)
- Transaction metadata decoding where available
- Diagnostic event decoding where available; individual event failures don't
  prevent other events from being decoded
- Fee-bump result decoding: inspects inner result XDR, prefers inner over outer
- Does NOT treat `in_successful_contract_call` as generic success/failure indicator
- Original base64 XDR values preserved even when decoding fails

### 5. Monitoring Orchestration (`src/stellarapi/monitoring/orchestration.py`)

- **`perform_monitoring_check()`** — Core orchestration step for a single transaction:
  1. Calls `rpc_client.get_transaction(transaction_hash)` to get latest RPC response
  2. Normalizes and decodes the response (result XDR, meta XDR, diagnostic events)
  3. Updates the `monitoring_jobs` table with new RPC status, decoded evidence,
     and lifecycle state
  4. Schedules `next_check_at` based on the outcome
- Key invariants preserved:
  - `NOT_FOUND` does not decode result fields; only raw RPC response stored
  - `SUCCESS`/`FAILED` transitions monitoring to `COMPLETED`; previously
    `COMPLETED` is never overwritten
  - Fee-bump transactions inspect inner result XDR
  - Raw evidence (`raw_rpc_response`, base64 XDR fields) always preserved,
    even when decoding fails
- Runs inside a database transaction; only updates if job is still `ACTIVE`
  (prevents worker from overwriting a `COMPLETED` result recorded by another worker)

### 6. Job Claiming (`src/stellarapi/monitoring/job_claimer.py`)

- **`claim_job()`** — Atomically claim a single monitoring job for processing:
  1. `SELECT ... FROM monitoring_jobs WHERE monitoring_status='ACTIVE' AND
     (locked_until IS NULL OR locked_until < now()) ORDER BY locked_until ASC
     NULLS FIRST, created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED`
  2. If a job is found, UPDATE its `locked_by`, `locked_until`,
     `next_check_at`, and increment `attempt_count`
  3. Return the job dict to the worker
- **`release_job()`** — Sets `locked_by = NULL`; the job remains in its
  current state so another worker may claim it
- Lease-based ownership: a worker may only claim a job whose lease has
  expired (`locked_until < now()`) and whose `monitoring_status` is `ACTIVE`
- Competing workers do not simultaneously claim the same active job
- Expired leases are recovered automatically

### 7. Background Worker (`src/stellarapi/monitoring/worker.py`)

- Runs until a graceful shutdown is requested (SIGINT/SIGTERM)
- Processes jobs in bounded batches (default 5 jobs per cycle)
- Respects the configured polling schedule (`MONITORING_INTERVAL_SECONDS`,
  default 60s)
- Recovers jobs whose worker leases have expired (`locked_until < now()`)
- Handles individual job failures without terminating the entire worker
- Exits cleanly when no eligible jobs remain and a shutdown has been requested
- Command to start: `python -m stellarapi.monitoring.worker`

## How a Transaction Moves Through the System

```
1. Register
   └─ POST /api/v1/transactions/register
     └─ Validate 64-char hex hash, testnet-only
     └─ Persist MonitoringJob:
         - rpc_status = pending
         - monitoring_status = ACTIVE
         - next_check_at = now()
     └─ Return registration UUID + status

2. Worker Claims Job
   └─ run_worker() loop
     └─ claim_job(engine, rpc_client)
       └─ SELECT ... FOR UPDATE SKIP LOCKED
       └─ UPDATE locked_by, locked_until, next_check_at, attempt_count++
       └─ Return job dict

3. RPC Check
   └─ perform_monitoring_check(rpc_client, job_id, ...)
     └─ rpc_client.get_transaction(transaction_hash)
     └─ GetTransactionResult dataclass (normalized fields + raw_rpc_response)

4. XDR Decode
   └─ decode_transaction_result(result_xdr) / decode_fee_bump_result()
     └─ Best-effort: None for unrecognized structures
     └─ Original base64 XDR preserved in raw_rpc_response

5. DB Update
   └─ UPDATE monitoring_jobs
     └─ rpc_status = SUCCESS/FAILED/NOT_FOUND
     └─ monitoring_status = COMPLETED / ACTIVE (with NOT_FOUND)
     └─ ledger, failure_reason, result_xdr, envelope_xdr set from RPC
     └─ locked_by = NULL (lease released)
     └─ next_check_at scheduled (or None for terminal)

6. API Retrieve
   └─ GET /api/v1/transactions/{hash}
     └─ Query MonitoringJob from DB
     └─ Return TransactionResponse (monitoring_status, rpc_status, ledger,
         result_xdr_base64, envelope_xdr_base64, diagnostic_events, etc.)
     └─ is_terminal = monitoring_status in (COMPLETED, OBSERVATION_EXPIRED)
     └─ is_active = monitoring_status == ACTIVE
```

## Why This Architecture?

### Modular Monolith (vs Micro-services)

- **Simplicity**: All code in one repo, one deployment, no inter-service
  communication overhead
- **Shared state**: Database is the single source of truth; worker and API share
  the same models and logic
- **Easier debugging**: Full stack trace in one process; no distributed tracing
  needed
- **Constraints-aligned**: Project explicitly excludes Redis, Celery, message
  brokers (DECISIONS.md §6-8); PostgreSQL-based coordination is the chosen
  approach

### Separate Worker Process (vs synchronous API polling)

- **Avoids RPC thundering herd**: Worker processes jobs in bounded batches
  (default 5 per cycle) rather than the API handler making RPC calls synchronously
- **Background polling**: API returns immediately after registration; worker
  continuously polls in its own event loop
- **Worker coordination**: PostgreSQL `FOR UPDATE SKIP LOCKED` enables multiple
  worker instances to compete for jobs without a message broker
- **Graceful shutdown**: Worker can be stopped with SIGINT/SIGTERM; in-flight
  jobs complete within a 15-second timeout

### PostgreSQL (vs alternative data stores)

- **Chosen because**: Project constraints explicitly exclude Redis, Celery,
  message brokers (DECISIONS.md §85-88)
- **`SELECT ... FOR UPDATE SKIP LOCKED`**: Enables worker coordination without
  external queuing services
- **JSONB columns**: Store `raw_rpc_response`; avoids adding many individual
  columns that may become stale (DECISIONS.md §59)
- **Check constraints**: Enforce valid `rpc_status` and `monitoring_status`
  values at the database level
- **Alembic migrations**: Standard SQLAlchemy migration tool; lightweight;
  no unnecessary ORM abstraction (DECISIONS.md §11)

## Component Diagram

```mermaid
graph TD
    subgraph "FastAPI Application"
        A[HTTP API Routes]
        H[Health Endpoint]
    end

    subgraph "Background Worker"
        W[Worker Loop]
        C[claim_job() ]
        R[perform_monitoring_check()]
        L[release_job() ]
    end

    subgraph "Stellar RPC"
        S[SorobanServer.get_transaction()]
    end

    subgraph "XDR Decoding"
        D[decode_transaction_result()]
        E[decode_diagnostic_events()]
        F[decode_fee_bump_result()]
    end

    subgraph "PostgreSQL (monitoring_jobs table)"
        J[MonitoringJob Row]
        I[locked_by, locked_until, next_check_at]
        S[result_xdr, envelope_xdr, raw_rpc_response JSONB]
    end

    A -->|POST /register| J
    A -->|GET /{hash}| J
    A -->|GET /| J

    W -->|claim_job| J
    W -->|release_job| J
    W -->|perform_monitoring_check| R
    R -->|rpc_client.get_transaction| S
    R -->|XDR decode| D & E & F
    R -->|UPDATE| J

    style J fill:#f9f,stroke:#333,stroke-width:1px
    style W fill:#bbf,stroke:#333,stroke-width:1px
    style A fill:#bfb,stroke:#333,stroke-width:1px
```

## Communication Flow

1. **API → Database**: Registration and retrieval use async SQLAlchemy sessions
2. **Worker → Database**: Job claiming, status updates, lease management use
   `async_engine.begin()` transactions
3. **Worker → RPC Client**: `get_transaction()` call via HTTPX/aiohttp to
   Soroban RPC endpoint
4. **RPC Client → XDR Decoder**: Normalized `GetTransactionResult`; best-effort
   XDR decoding into `TransactionResultInfo`, `TransactionMetaInfo`,
   `DiagnosticEventInfo`
5. **All components → PostgreSQL**: Shared `monitoring_jobs` table with check
   constraints on `rpc_status` (`SUCCESS|FAILED|NOT_FOUND|pending`) and
   `monitoring_status` (`ACTIVE|COMPLETED|OBSERVATION_EXPIRED`)



## Phase 9: Contract-Level Transaction Discovery
- **New table**: `contracts_discovery` with unique constraint on `(contract_address, network)`
- **Model**: `ContractDiscovery` with status lifecycle PENDING → IN_PROGRESS → COMPLETED/ERROR
- **API**: `POST /api/v1/contracts/discovery/register`, `GET /api/v1/contracts/discovery/{address}`
- **Worker**: Cursor-based pagination using `stellar_sdk.get_events()` with `EventFilter(contract_ids=[...])`
- **Checkpoint**: Cursor + `last_successful_ledger` saved after each batch
- **Idempotency**: `UNIQUE(transaction_hash)` prevents duplicate registrations
- **Integration**: Discovered tx hashes enter existing `monitoring_jobs` table

## Phase 10: Alerts and RPC Health Monitoring
- **New table**: `alerts` with lifecycle pending → delivered → failed
- **Alert types**: SUCCESS, FAILED, OBSERVATION_EXPIRED, RPC_UNAVAILABLE
- **Duplicate prevention**: `UNIQUE(transaction_hash, alert_type)`
- **RPC health**: `get_health()` via `SorobanServer.get_health()`, returns status/ledger/retention window
- **API**: `GET /api/v1/rpc/health`
- **Key principle**: RPC health errors ≠ transaction failures
- **Delivery**: Database-persisted only; delivery channel operator-responsibility

## Architectural Decisions
- Alerts use separate `alerts` table (not modifying `monitoring_jobs`) to preserve existing schema
- RPC health monitoring runs independently from transaction worker
- No built-in alert delivery channel (operator responsibility per approved scope)
- Testnet-only operation per DECISIONS.md

## Technology Stack
- FastAPI + SQLAlchemy 2.x async + PostgreSQL
- stellar_sdk for RPC communication
- Alembic for migrations
- pytest for testing
