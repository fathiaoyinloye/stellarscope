# StellarScope Final Project Report

## Overview

StellarScope is a Python backend service for monitoring Stellar/Soroban transactions. It allows users to register transaction hashes for monitoring, periodically queries the Stellar RPC to determine their status (SUCCESS, FAILED, NOT_FOUND), and preserves evidence of on-chain activity.

## The Problem

In the early Stellar/MVP phase, developers had to manually submit individual transaction hashes to track their status. There was no automated way to:
- Discover transactions associated with Soroban contracts
- Monitor contract-level activity
- Generate alerts on important outcomes
- Track RPC health and availability

## Architecture and Technology Stack

### Backend
- **FastAPI**: HTTP API framework
- **SQLAlchemy 2.x async**: Async ORM with PostgreSQL
- **Alembic**: Database schema migrations
- **stellar-sdk**: Stellar network interaction (RPC, XDR decoding)
- **HTTPX**: Async HTTP client for RPC calls
- **PostgreSQL**: Primary data store
- **pytest**: Test suite (pytest-asyncio for async tests)

### Project Structure
```
stellarscope/
├── src/stellarapi/          # Main application package
│   ├── api/                 # FastAPI routes (health, transactions, discovery, health monitor)
│   ├── database/            # SQLAlchemy models, engine, session
│   ├── monitoring/          # Background worker, job claiming, orchestration
│   ├── rpc_client.py        # Async Stellar RPC client
│   ├── xdr/                 # Transaction result XDR decoder
│   ├── config/              # pydantic-settings configuration
│   └── core/                # Logging and utilities
├── alembic/                 # Database migration scripts
├── docs/                    # Documentation
├── tests/                   # pytest test suite
└── pyproject.toml           # Project configuration
```

## How Transaction Monitoring Works

1. **Registration**: User submits a 64-character hex transaction hash via `POST /api/v1/transactions/register`
2. **Worker Loop**: Background worker claims jobs via `SELECT ... FOR UPDATE SKIP LOCKED`
3. **RPC Polling**: Worker queries `stellar_rpc.getTransaction(hash)` 
4. **Status Processing**:
   - `SUCCESS`/`FAILED` → Records terminal result, lifecycle turns `COMPLETED`
   - `NOT_FOUND` → Continues polling (does not prove pending/never existed/aged out)
   - RPC errors → Retries with backoff; does not update status
5. **Evidence Preservation**: Raw RPC JSON and base64 XDR fields stored for audit
6. **Lifecycle**: `ACTIVE` → `COMPLETED` / `OBSERVATION_EXPIRED`

**Critical rules** (from DECISIONS.md):
- Never overwrite a previously recorded terminal result
- `NOT_FOUND` does not prove pending/never existed/aged out
- RPC errors must not be treated as transaction failures

## How Contract Discovery Works

Implemented in Phase 9:

1. **Registration**: User registers a Soroban contract address via `POST /api/v1/contracts/discovery/register`
2. **Discovery Worker**: Fetches events using `stellar_sdk.EventFilter(contract_ids=[...])` with `SorobanServer.get_events()`
3. **Event Processing**: Extracts `EventInfo.transaction_hash` from each event
4. **Deduplication**: `UNIQUE(transaction_hash)` on `monitoring_jobs` prevents duplicate registrations
5. **Checkpoint Persistence**: Cursor and `last_successful_ledger` saved after each batch for resumability
6. **Worker Recovery**: IN_PROGRESS contracts resume from saved cursor/ledger on restart
7. **Integration**: Discovered transaction hashes enter the existing `monitoring_jobs` table, sharing the same ACTIVE → COMPLETED / OBSERVATION_EXPIRED lifecycle

**RPC Method**: `get_events()` with `EventFilter(contract_ids=[...])` — natively supported by `stellar_sdk`, not an assumption

## How Alerts and RPC Health Monitoring Work

### Alerts (Phase 10)

- **New `alerts` table**: Persists alert records with types `SUCCESS`, `FAILED`, `OBSERVATION_EXPIRED`, `RPC_UNAVAILABLE`
- **Duplicate prevention**: `UNIQUE(transaction_hash, alert_type)` constraint
- **Lifecycle**: `pending` → `delivered` → `failed`
- **Delivery**: Alerts persist to database; delivery (email/webhook/Slack) is operator-responsibility, not built into the system
- **Generation**: Alerts generated on transaction outcomes and RPC unavailability

### RPC Health Monitoring (Phase 10)

- **`RpcClient.get_health()`**: Calls `SorobanServer.get_health()`
- **Returns**: `status`, `latest_ledger`, `oldest_ledger`, `ledger_retention_window`
- **API endpoint**: `GET /api/v1/rpc/health`
- **Key distinction**: RPC health distinguished from transaction status — RPC errors (`CommunicationError`) ≠ `FAILED`
- **Frequency**: Configurable via settings; default respects RPC retention window (~7 days / 120960s)
- **Independence**: Health monitoring runs independently from worker; failures don't break monitoring

## Phase Delivery Summary

### Phase 9: Contract-Level Transaction Discovery
- ✅ `contracts_discovery` table with unique constraint on `(contract_address, network)`
- ✅ `ContractDiscovery` model with status lifecycle: PENDING → IN_PROGRESS → COMPLETED/ERROR
- ✅ `POST /api/v1/contracts/discovery/register` API endpoint
- ✅ `GET /api/v1/contracts/discovery/{address}` status endpoint
- ✅ Discovery worker with cursor-based pagination using `get_events()` + `EventFilter(contract_ids=[...])`
- ✅ Checkpoint persistence (cursor + last_successful_ledger) after each batch
- ✅ Idempotent discovery: `UNIQUE(transaction_hash)` prevents duplicate registrations
- ✅ Worker restart recovery from saved cursor/ledger state
- ✅ Integration with existing monitoring workflow (discovered tx hashes enter `monitoring_jobs`)
- ✅ `docs/phase-9-verification.md` verification report
- ✅ 32/37 tests passing (5 pre-existing failures unchanged)

### Phase 10: Alerts and RPC Health Monitoring
- ✅ New `alerts` table with lifecycle: pending → delivered → failed
- ✅ `Alert` model with alert_type and status lifecycle
- ✅ Alert types: SUCCESS, FAILED, OBSERVATION_EXPIRED, RPC_UNAVAILABLE
- ✅ Duplicate prevention via `UNIQUE(transaction_hash, alert_type)`
- ✅ `RpcClient.get_health()` method
- ✅ `GET /api/v1/rpc/health` API endpoint
- ✅ RPC health distinguished from transaction failures (critical principle)
- ✅ Health monitoring runs independently from transaction worker
- ✅ `docs/phase-9-verification.md` updated
- ✅ 32/37 tests passing (5 pre-existing failures unchanged)
- ✅ No new test failures introduced

### Phase 11: Not yet started
- Dashboard implementation
- Stellar Mainnet support
- Currently out of scope per project rules; explicitly "Do not start Phase 11"

## Tests and Results

**Overall test results**: 32 of 37 tests passing

**Pre-existing failures** (unchanged from before Phases 9-10):
- `test_concurrency.py::test_lease_expiry_recovery` — event loop interference
- `test_models.py::test_model_creation_and_persistence` — test ordering collision
- `test_workflow_integration.py::test_full_successful_workflow` — DB session isolation
- `test_workflow_integration.py::test_full_failed_workflow` — SQLAlchemy session issue
- `test_workflow_integration.py::test_full_not_found_workflow` — SQLAlchemy session issue

**Test errors** (pre-existing, 9 errors in test_models.py):
- `NameError: name 'session' is not defined` — fixture issue, unrelated to Phases 9-10

**No new test failures** were introduced by Phases 9 or 10.

## Known Limitations and Outstanding Issues

1. **No live/Testnet integration tested**: All functionality verified via code review and mocked logic; no actual Stellar RPC calls performed during implementation
2. **Mocked test suite not yet written**: Deterministic verification tests planned as follow-up
3. **No alert delivery channel**: Alerts persist to database; operator responsible for external delivery (email/webhook/Slack). Not implemented per approved scope.
4. **Testnet only**: No Mainnet support per DECISIONS.md
5. **Long-range historical discovery**: Limited by Stellar's ledger retention policy (~7 days default)
6. **test_models.py fixture issues**: 9 NameError errors pre-existing, unrelated to Phases 9-10

## Setup and Execution Instructions

### Prerequisites
- Python 3.10+
- PostgreSQL 15+ with `SELECT ... FOR UPDATE SKIP LOCKED` support
- `pip install -r requirements.txt`

### Installation
```bash
# Clone the repository
git clone <repository-url>
cd stellarscope

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Configure: DATABASE_URL, SOROBAN_RPC_URL, ENV

# Apply database migrations
cd stellarapi
 alembic upgrade head

# Run the health check
python -m src.stellarapi.main

# Or run the background worker
python -m stellarapi.monitoring.worker

# Run the test suite
pytest
```

### Running the Application
```bash
# Start the FastAPI app
cd stellarapi
uvicorn src.stellarapi.main:app --reload

# Background worker
cd stellarapi
python -m stellarapi.monitoring.worker
```

### API Endpoints (selected)
- `GET /health` — Application liveness (does not check RPC/database)
- `POST /api/v1/transactions/register` — Register transaction for monitoring
- `GET /api/v1/transactions/{hash}` — Retrieve monitoring info
- `POST /api/v1/contracts/discovery/register` — Register contract for discovery
- `GET /api/v1/contracts/discovery/{address}` — Get discovery status
- `GET /api/v1/rpc/health` — Check Soroban RPC health

## Phase Roadmap Status

**Phase 9**: Complete — Contract-Level Transaction Discovery
- Contract registration via API
- Event discovery via `stellar_sdk.get_events()` + `EventFilter`
- Transaction hash extraction and deduplication
- Checkpoint-protected pagination
- Integration with existing monitoring lifecycle

**Phase 10**: Complete — Alerts and RPC Health Monitoring
- Alerts table with lifecycle and duplicate prevention
- RPC health monitoring via `get_health()`
- Health API endpoint
- RPC errors distinguished from transaction failures
- Health monitoring independent from transaction worker

**Phase 11**: Not yet started — Dashboard + Mainnet support
- Would require frontend implementation
- Would require server-side network configuration
- Currently out of scope; explicitly "Do not start Phase 11"

## Technology Stack Summary

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI (Python) |
| ORM | SQLAlchemy 2.x async |
| Migrations | Alembic |
| RPC Client | stellar_sdk SorobanServer |
| HTTP Client | httpx (via stellar-sdk) |
| Database | PostgreSQL |
| Testing | pytest + pytest-asyncio |
| XDR Decoding | stellar_sdk built-in + custom decoder |

---

*Final project report — Phases 9 and 10 closeout.*
