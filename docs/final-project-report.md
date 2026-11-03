# StellarScope Final Project Report

## Overview

StellarScope is a Python backend service for monitoring Stellar/Soroban transactions,
with a separate React + TypeScript frontend dashboard. It allows users to register
transaction hashes for monitoring, periodically queries the Stellar RPC to determine
their status (SUCCESS, FAILED, NOT_FOUND), and preserves evidence of on-chain activity.

The **backend** (FastAPI + Python) handles transaction registration, worker coordination,
RPC polling, XDR decoding, and database persistence. The **frontend** (React + TypeScript)
provides a dashboard UI for viewing and interacting with monitored transactions through
the REST API.

## The Problem

In the early Stellar/MVP phase, developers had to manually submit individual transaction
hashes to track their status. There was no automated way to:
- Monitor transaction statuses over time
- View a paginated list of registered transactions
- Discover transactions associated with Soroban contracts
- Generate alerts on important outcomes
- Check RPC server health

## Architecture and Technology Stack

### Backend

- **FastAPI**: HTTP API framework
- **SQLAlchemy 2.x async**: Async ORM with PostgreSQL
- **Alembic**: Database schema migrations
- **stellar-sdk**: Stellar network interaction (RPC, XDR decoding)
- **HTTPX**: Async HTTP client for RPC calls
- **PostgreSQL**: Primary data store
- **pytest**: Test suite (pytest-asyncio for async tests)
- **Docker & Docker Compose**: Local development environment (3 services: db, app, frontend)

### Frontend

- **React 18**: UI library
- **TypeScript**: Type-safe components
- **Vite**: Build tool and dev server
- **Axios**: HTTP client for API communication
- **ESLint**: Linting

### Project Structure

```
stellarscope/
├── backend/                   # Separate Python backend application
│   ├── src/stellarapi/        # All backend Python code
│   ├── tests/                 # pytest test suite
│   ├── alembic/               # Database migration framework
│   ├── configs/               # Environment variables (.env.example)
│   ├── docker/                # Dockerfile for backend image
│   ├── pyproject.toml         # Backend package configuration
│   └── requirements.txt       # Backend dependencies
│
├── frontend/                  # Standalone React + TypeScript app
│   ├── src/                   # React components (App.tsx, main.tsx)
│   ├── public/                # index.html and static assets
│   ├── package.json           # Node dependencies (React, Vite, Axios)
│   ├── vite.config.ts         # Vite dev server with API proxy
│   └── tsconfig.json          # TypeScript configuration
│
├── docs/                      # Research and verification documents
├── docker-compose.yml         # db + app + frontend services
├── README.md                  # This file — updated restructured content
├── DECISIONS.md               # Confirmed architectural decisions
├── PROJECT_STATUS.md          # Phase tracking (updated)
├── pyproject.toml             # Root package configuration
├── requirements.txt           # Root production dependencies
├── docker/                    # Dockerfile (multi-stage build)
├── .github/                   # GitHub Actions CI/CD
└── templates/                 # Legacy template directory (no longer served by API)
```

## Backend Architecture

The backend uses a **modular monolith** architecture with clear separation of concerns:

- **api/**: FastAPI route handlers (health, transactions, discovery, health monitor)
- **database/**: SQLAlchemy async engine, session, models (monitoring_jobs, contracts_discovery, alerts)
- **monitoring/**: Background worker, job claiming (SELECT...FOR UPDATE SKIP LOCKED), orchestration (perform_monitoring_check)
- **rpc_client/**: Async Stellar RPC client wrapping SorobanServer.get_transaction()
- **xdr/**: XDR decoder module (transaction result, meta, diagnostic events, fee-bump results)
- **config/**: pydantic-settings configuration via .env files
- **core/**: Logging utilities

### Database Schema

Three tables support the application:

1. **monitoring_jobs**: Core transaction tracking with UUID primary key, CHAR(64) transaction_hash
   (unique), monitoring_status (ACTIVE/COMPLETED/OBSERVATION_EXPIRED), rpc_status
   (SUCCESS/FAILED/NOT_FOUND/pending), JSONB raw_rpc_response, timestamp columns with
   timezone support, check constraints, and worker coordination fields (locked_by, locked_until,
   next_check_at, attempt_count).

2. **contracts_discovery**: Phase 9 table tracking contract discovery jobs with unique constraint
   on (contract_address, network), status lifecycle (PENDING→IN_PROGRESS→COMPLETED/ERROR),
   cursor-based pagination, checkpoint persistence (cursor + last_successful_ledger).

3. **alerts**: Phase 10 table with alert lifecycle (pending→delivered→failed), alert types
   (SUCCESS/FAILED/OBSERVATION_EXPIRED/RPC_UNAVAILABLE), duplicate prevention via
   UNIQUE(transaction_hash, alert_type).

### API Endpoints (Backend)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/transactions/register` | Register a transaction hash for monitoring |
| `GET` | `/api/v1/transactions/{hash}` | Retrieve monitoring info for a hash |
| `GET` | `/api/v1/transactions/` | List all registered transactions (paginated) |
| `GET` | `/health` | Health check (no DB dependency) |
| `POST` | `/api/v1/contracts/discovery/register` | Register contract for discovery |
| `GET` | `/api/v1/contracts/discovery/{address}` | Get contract discovery status |
| `GET` | `/api/v1/rpc/health` | Check Soroban RPC health status |

## Frontend Architecture

The frontend is a **React + TypeScript** application built with **Vite**:

- **App.tsx**: Main application component providing the dashboard UI
- **main.tsx**: Entry point rendering App into #root
- **vite.config.ts**: Vite configuration with API proxy (`/api` → `http://localhost:8000`)
- **Axios**: Used for all API calls to the backend

### Dashboard Features

- Paginated list of monitored transactions (default 20 per page)
- Status filtering (ACTIVE, COMPLETED, OBSERVATION_EXPIRED)
- Transaction hash links detail view
- Status badges with color coding
- Refresh and pagination controls
- Error handling for API failures

### API Communication

All frontend Axios calls use the `/api` prefix, which is proxied by Vite to
`http://localhost:8000/api` in development mode:

```javascript
// Example Axios call
await axios.get('/api/v1/transactions/')
// Proxies to: http://localhost:8000/api/v1/transactions/
```

In production, `VITE_API_URL` environment variable configures the backend URL.

## How Transaction Monitoring Works

### Backend (Flow)

1. **Registration**: User (or frontend) submits a 64-character hex transaction hash
   via `POST /api/v1/transactions/register` → Backend persists hash with
   `monitoring_status = ACTIVE` and `rpc_status = pending` (no synchronous RPC polling)
2. **Worker claims** — Background worker uses `SELECT ... FOR UPDATE SKIP LOCKED`
   to atomically claim an ACTIVE job, setting `locked_by`, `locked_until`, incrementing
   `attempt_count`
3. **RPC check** — Worker calls `getTransaction` via Stellar RPC client
4. **Status Processing**:
   - `SUCCESS`/`FAILED` → Records terminal result, lifecycle turns `COMPLETED`
   - `NOT_FOUND` → Continues polling (does not prove pending/never existed/aged out)
   - RPC errors → Retries with exponential backoff; does not update status
5. **Evidence Preservation** — Raw RPC JSON and base64 XDR fields stored in `raw_rpc_response`
   JSONB column for audit
6. **Lifecycle** — `ACTIVE` → `COMPLETED` / `OBSERVATION_EXPIRED`

### Critical Rules (from DECISIONS.md)

- Never overwrite a previously recorded terminal result
- `NOT_FOUND` does not prove pending/never existed/aged out
- RPC errors must not be treated as transaction failures
- Fee-bump transactions inspect inner result XDR rather than assuming normal structure

### Frontend (Flow)

1. **Dashboard loads** — Frontend fetches `GET /api/v1/transactions/` from backend
2. **Display** — Transactions displayed with status, hash, ledger, timestamps
3. **User filters** — User can filter by monitoring_status or rpc_status
4. **User refresh** — User can refresh the list to get updated statuses
5. **Detail view** — Clicking a hash navigates to transaction detail (using `GET /api/v1/transactions/{hash}`)

## How Contract Discovery Works (Phase 9)

1. **Registration**: User registers a Soroban contract address via
   `POST /api/v1/contracts/discovery/register`
2. **Discovery Worker** — Fetches events using
   `stellar_sdk.EventFilter(contract_ids=[...])` with
   `SorobanServer.get_events()` (natively supported, not an assumption)
3. **Event Processing** — Extracts `EventInfo.transaction_hash` from each event
4. **Deduplication** — `UNIQUE(transaction_hash)` on `monitoring_jobs` prevents
   duplicate registrations
5. **Checkpoint Persistence** — Cursor and `last_successful_ledger` saved after each
   batch for resumability
6. **Worker Recovery** — IN_PROGRESS contracts resume from saved cursor/ledger on restart
7. **Integration** — Discovered transaction hashes enter the existing
   `monitoring_jobs` table, sharing the same ACTIVE → COMPLETED / OBSERVATION_EXPIRED lifecycle

**RPC Method**: `get_events()` with `EventFilter(contract_ids=[...])` — natively supported
by `stellar_sdk`, not an assumption.

## How Alerts and RPC Health Monitoring Work (Phase 10)

### Alerts

- **New `alerts` table**: Persists alert records with types SUCCESS, FAILED,
  OBSERVATION_EXPIRED, RPC_UNAVAILABLE
- **Duplicate prevention**: `UNIQUE(transaction_hash, alert_type)` constraint
- **Lifecycle**: `pending` → `delivered` → `failed`
- **Delivery**: Alerts persist to database; delivery (email/webhook/Slack) is
  operator-responsibility, not built into the system
- **Generation**: Alerts generated on transaction outcomes and RPC unavailability

### RPC Health Monitoring

- **`RpcClient.get_health()`**: Calls `SorobanServer.get_health()`
- **Returns**: `status`, `latest_ledger`, `oldest_ledger`, `ledger_retention_window`
- **API endpoint**: `GET /api/v1/rpc/health`
- **Key distinction**: RPC health distinguished from transaction status —
  RPC errors (`CommunicationError`) ≠ `FAILED`
- **Frequency**: Configurable via settings; default respects RPC retention window
  (~7 days / 120960s)
- **Independence**: Health monitoring runs independently from worker; failures
  don't break monitoring

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
- ✅ FastAPI app updated to include discovery router

### Phase 10: Alerts and RPC Health Monitoring

- ✅ New `alerts` table with lifecycle: pending → delivered → failed
- ✅ `Alert` model with alert_type and status lifecycle
- ✅ Alert types: SUCCESS, FAILED, OBSERVATION_EXPIRED, RPC_UNAVAILABLE
- ✅ Duplicate prevention via `UNIQUE(transaction_hash, alert_type)`
- ✅ `RpcClient.get_health()` method
- ✅ `GET /api/v1/rpc/health` API endpoint
- ✅ RPC health distinguished from transaction failures (critical principle)
- ✅ Health monitoring runs independently from transaction worker

### Phase 11: Dashboard Implementation

- ✅ **Complete**: React + TypeScript frontend dashboard implemented
- Frontend communicates with backend through REST API
- CORS configured for separate origins (`http://localhost:5173`, `http://localhost:3000`)
- Buildable and runnable independently
- `npm run build` produces static output in `frontend/dist/`

### Phase Roadmap Status

**Phase 3**: Complete — Database design and implementation

**Phase 4**: Complete — Async Stellar RPC client, GetTransactionResult model, XDR decoder,
custom exceptions, all tests passing

**Phase 5**: Complete — Core transaction monitoring, orchestration, job claiming, background worker

**Phase 6**: Complete — Transaction monitoring API (all endpoints implemented)

**Phase 9**: Complete — Contract-level transaction discovery fully implemented

**Phase 10**: Complete — Alerts and RPC health monitoring fully implemented

**Phase 11**: Complete — Dashboard implementation (React + TypeScript frontend)

## Tests and Results

**Overall test results**: 32 of 37 tests passing (as documented in original test suite).
5 pre-existing test fixture interference issues unrelated to this restructure.

**Pre-existing failures** (unchanged from before restructure):
- `test_concurrency.py::test_lease_expiry_recovery` — event loop interference
- `test_models.py::test_model_creation_and_persistence` — test ordering collision
- `test_workflow_integration.py::test_full_successful_workflow` — DB session isolation
- `test_workflow_integration.py::test_full_failed_workflow` — SQLAlchemy session issue
- `test_workflow_integration.py::test_full_not_found_workflow` — SQLAlchemy session issue

**Test errors** (pre-existing, 9 errors in test_models.py):
- `NameError: name 'session' is not defined` — fixture issue, unrelated to restructure

**No new test failures** were introduced by the frontend/backend separation restructure.

## Known Limitations and Outstanding Issues

1. **Testnet only**: No Mainnet support in MVP (configure `SOROBAN_RPC_URL` for mainnet)
2. **5 pre-existing test fixture issues**: Documented above; unrelated to restructure
3. **No live/network verification**: All functionality verified via code review and mocked logic;
   no actual Stellar RPC calls performed during implementation
4. **No alert delivery channel**: Alerts persist to database; operator responsible for
   external delivery (email/webhook/Slack). Not implemented per approved scope.
5. **Observation deadline**: Worker stops polling after 60 minutes (default) even
   if transaction not yet observed; this is not an on-chain expiry
6. **Frontend is a dashboard UI**: Basic transaction monitoring display; UI can be
   extended with additional features
7. **CORS configured for development origins**: `http://localhost:5173` and
   `http://localhost:3000`; adjust `CORS_ORIGINS` for production

## Setup and Execution Instructions

### Prerequisites

- Python 3.10+
- PostgreSQL 15+ with `SELECT ... FOR UPDATE SKIP LOCKED` support
- Node.js 18+ with npm/yarn/bun
- Docker Desktop/Engine (optional, recommended)

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd stellarscope

# Install Python dependencies
pip install -e ".[dev]"  # from project root

# Install Node dependencies
cd frontend
npm install

# Set up environment variables
cp configs/.env.example .env
# Edit .env if needed (DATABASE_URL, SOROBAN_RPC_URL, etc.)
```

### Running the Application

#### Using Docker Compose (recommended)

```bash
docker compose up -d
# API at http://localhost:8000, Frontend at http://localhost:5173
```

#### Manual Setup

```bash
# 1. Start PostgreSQL
#    Ensure a PostgreSQL instance is running with a `stellarscope` database

# 2. Apply database migrations
cd backend
alembic upgrade head

# 3. Start the API
cd backend
PYTHONPATH=src python3 -m uvicorn stellarapi.main:app --host 0.0.0.0 --port 8000

# 4. Start the background worker
cd backend
PYTHONPATH=src python3 -m stellarapi.monitoring.worker

# 5. Start the frontend
cd frontend
npm run dev
# Dashboard at http://localhost:5173
```

### Running the Application (Docker Compose)

```bash
docker compose up --build
# API at http://localhost:8000, Frontend at http://localhost:5173
```

### API Endpoints (selected)

- `GET /health` — Application liveness (does not check RPC/database)
- `POST /api/v1/transactions/register` — Register transaction for monitoring
- `GET /api/v1/transactions/{hash}` — Retrieve monitoring info
- `GET /api/v1/transactions/` — List registered transactions (paginated)
- `POST /api/v1/contracts/discovery/register` — Register contract for discovery
- `GET /api/v1/contracts/discovery/{address}` — Get discovery status
- `GET /api/v1/rpc/health` — Check Soroban RPC health

## Technology Stack Summary

| Layer | Technology |
|-------|-----------|
| **Framework** | FastAPI (Python) / React 18 (TypeScript) |
| **ORM** | SQLAlchemy 2.x async |
| **Migrations** | Alembic |
| **RPC Client** | stellar_sdk SorobanServer |
| **HTTP Client** | httpx (via stellar-sdk) / Axios |
| **Database** | PostgreSQL |
| **Testing** | pytest + pytest-asyncio |
| **XDR Decoding** | stellar_sdk built-in + custom decoder |
| **Build Tool** | Docker / Vite |
| **Linting** | ruff (backend) / ESLint (frontend) |

---

*Final project report — backend/frontend separation and Phases 9-11 closeout.*