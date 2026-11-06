# StellarScope

StellarScope is a backend service for monitoring Stellar/Soroban transaction hashes.
It watches transactions until a terminal RPC status (SUCCESS/FAILED) is observed
or the observation deadline expires, persisting the lifecycle state in PostgreSQL.

A separate React frontend provides a dashboard for viewing monitored transactions.

## Problem

Stellar transaction monitoring requires periodic `getTransaction` RPC polling until
a terminal result is available. Manual polling is impractical for operators tracking
many hashes. StellarScope automates this workflow with lease-based worker coordination,
XDR evidence preservation, and a web-based dashboard.

## Core Capabilities

- **Transaction monitoring**: Register hashes and poll `getTransaction` until terminal
  status (SUCCESS/FAILED) or observation deadline.
- **RPC evidence preservation**: Raw RPC response and base64 XDR fields stored in JSONB;
  best-effort XDR decoding with trailing-bytes handling.
- **Job coordination**: PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` enables worker
  coordination without external queue services.
- **Lifecycle separation**: RPC terminal status (SUCCESS/FAILED/NOT_FOUND) is kept
  distinct from StellarScope monitoring lifecycle (ACTIVE/COMPLETED/OBSERVATION_EXPIRED).
- **Frontend dashboard**: Paginated transaction list, status filtering, status badges.
- **Contract discovery**: Register and query Soroban contract events (cursor-based
  pagination, checkpoint persistence).
- **RPC health monitoring**: Dedicated health endpoint; RPC errors distinguished from
  transaction failures (critical principle).

## Architecture

StellarScope uses a separate frontend/backend architecture.

**Backend** (modular monolith, FastAPI):
- All code in `backend/src/stellarapi/` package
- Sub-packages: `api/` (routes), `database/` (models/engine), `monitoring/` (worker
  + orchestration), `rpc_client/` (Stellar RPC client), `xdr/` (XDR decoder), `config/`
  (pydantic-settings)
- Background worker runs as a separate async process (no Celery/Rediс)
- Worker coordination via PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`
- FastAPI app and worker share the same code base
- CORS configured for separate frontend origins

**Frontend** (React + TypeScript):
- Vite dev server at `http://localhost:5173`
- Communicates with backend via REST API at `http://localhost:8000/api`
- Axios for all API calls; built with `npm run build`

**Workflow**:
1. **Register** — `POST /api/v1/transactions/register` → hash persisted with
   `monitoring_status = ACTIVE`, `rpc_status = pending`
2. **Worker claims** — `SELECT ... FOR UPDATE SKIP LOCKED` atomically claims ACTIVE job,
   sets `locked_by`, `locked_until`
3. **RPC check** — Worker calls `getTransaction` via Stellar RPC client
4. **XDR decode** — Normalized and decoded (best-effort); fee-bump inspected via inner
   result XDR
5. **DB update** — Status transition: ACTIVE → COMPLETED (SUCCESS/FAILED) or stays ACTIVE
   with NOT_FOUND; observation deadline tracked
6. **API retrieve** — `GET /api/v1/transactions/{hash}` → monitoring info → dashboard

## Technology Stack

| Layer | Technology |
|---|---|
| backend | FastAPI, SQLAlchemy 2.x async, asyncpg, Alembic |
| RPC | stellar-sdk (py-stellar-base), HTTPX |
| database | PostgreSQL, JSONB column for raw RPC response |
| worker | Async process (no Celery/Rediс) |
| frontend | React 18, TypeScript, Vite, Axios |
| CI/CD | GitHub Actions |
| container | Docker + Docker Compose |

## Getting Started

**Recommended: Docker Compose**

```bash
# Start all services (db, app, frontend)
docker compose up -d

# Wait for PostgreSQL to be ready
# (check: docker compose logs db | grep -i ready)

# Apply database migrations
docker exec stellarscope-app alembic upgrade head

# API at http://localhost:8000, Frontend at http://localhost:5173
# API docs at http://localhost:8000/docs
```

**Manual setup** (without Docker):

**Backend**:
```bash
pip install -e ".[dev]"
cp configs/.env.example .env
cd backend && alembic upgrade head
PYTHONPATH=src python3 -m uvicorn stellarapi.main:app --host 0.0.0.0 --port 8000
```

**Frontend**:
```bash
cd frontend && npm install && npm run dev
```

## API

### Key Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/transactions/register` | Register a transaction hash for monitoring |
| `GET` | `/api/v1/transactions/{hash}` | Retrieve monitoring info for a hash |
| `GET` | `/api/v1/transactions/` | List all registered transactions (paginated, filterable) |
| `GET` | `/health` | Health check (no DB dependency) |
| `POST` | `/api/v1/contracts/discovery/register` | Register contract for discovery |
| `GET` | `/api/v1/contracts/discovery/{address}` | Get contract discovery status |
| `GET` | `/api/v1/rpc/health` | Check Soroban RPC health status |

Auto-generated OpenAPI/Swagger documentation:
- HTML: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Deployment

**Docker Compose**:
```bash
docker compose up --build
# Or: docker compose build && docker compose up -d
```

**Independent deployment**:
- Backend: FastAPI on port 8000 with production ENV + LOG_LEVEL
- Frontend: `npm run build` → serve `frontend/dist/`; configure `VITE_API_URL`

**Production considerations**:
- Set `ENV=production` and `LOG_LEVEL=WARNING` or `ERROR`
- Configure `CORS_ORIGINS` for production domain(s)
- Use production PostgreSQL with backups
- Set `SOROBAN_RPC_URL` to production RPC endpoint
- Consider a process manager (systemd, PM2) for the worker
- HTTPS recommended

## Testing

**Backend**:
```bash
cd backend && PYTHONPATH=src python3 -m pytest tests/ -v --tb=short
```
Results: 24 of 28 tests pass. 4 failures are due to a confirmed asyncpg event-loop
conflict between SQLAlchemy 2.x greenlet-spawn and pytest-asyncio per-test event loops;
each failing test passes when run individually. 5 pre-existing test fixture issues have been
documented separately; all model tests pass in isolation.

**Frontend**:
```bash
cd frontend && npm run build
```

**Integration**:
Verify by querying the health endpoint and registering a transaction:
```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/api/v1/transactions/register \
  -H "Content-Type: application/json" -d '{"transaction_hash":"a"*64,"network":"testnet"}'
```

## Known Limitations

- **Testnet only**: No Mainnet support in MVP; configure `SOROBAN_RPC_URL` for mainnet
- **4 test failures**: Confirmed asyncpg event-loop conflict between SQLAlchemy 2.x
  greenlet-spawn and pytest-asyncio per-test event loops; each test passes when run
  individually. All other tests pass consistently.
- **No live network verification**: Project targets Testnet only; no transactions
  submitted or funds at risk
- **Observation deadline**: Worker stops polling after 60 minutes (default) even if
  transaction not yet observed; this is not an on-chain expiry
- **Frontend is a dashboard UI**: Basic transaction monitoring display; not a full
  feature-parity interface
- **CORS configured for development origins**: `http://localhost:5173` and
  `http://localhost:3000`; adjust for production

## Repository Structure

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
├── README.md                  # This file
├── DECISIONS.md               # Confirmed architectural decisions
├── PROJECT_STATUS.md          # Phase tracking
├── pyproject.toml             # Root package configuration
├── requirements.txt           # Root production dependencies
├── docker/                    # Dockerfile (multi-stage build)
├── .github/                   # GitHub Actions CI/CD
└── templates/                 # Legacy template directory
```

## Phase Summary

### Phase 9: Contract-Level Transaction Discovery
- `contracts_discovery` table with unique constraint on `(contract_address, network)`
- `ContractDiscovery` model: PENDING → IN_PROGRESS → COMPLETED/ERROR
- `POST /api/v1/contracts/discovery/register` and `GET /api/v1/contracts/discovery/{address}`
- Cursor-based pagination with `stellar_sdk.get_events()` + `EventFilter`
- Checkpoint persistence (cursor + last_successful_ledger) after each batch
- Idempotent discovery: `UNIQUE(transaction_hash)` prevents duplicate registrations

### Phase 10: Alerts and RPC Health Monitoring
- `alerts` table: lifecycle pending → delivered → failed
- Alert types: SUCCESS, FAILED, OBSERVATION_EXPIRED, RPC_UNAVAILABLE
- `UNIQUE(transaction_hash, alert_type)` prevents duplicates
- `RpcClient.get_health()` method; `GET /api/v1/rpc/health` API endpoint
- RPC health distinguished from transaction failures (critical principle)

### Phase 11: Dashboard Implementation
- React + TypeScript dashboard complete
- Communicates with backend via REST API
- CORS configured for separate origins
- Buildable and runnable independently

## Important Technical Distinctions

- **RPC statuses**: Only `SUCCESS`, `FAILED`, `NOT_FOUND` are returned by Stellar RPC;
  `PENDING` is not an RPC status.
- **Monitoring lifecycle is separate from RPC status**: ACTIVE → COMPLETED/OBSERVATION_EXPIRED
  is StellarScope's internal state; it does not mean the on-chain transaction expired.
- **RPC errors ≠ transaction failures**: Retry on RPC errors; do not update status to `FAILED`.
- **Observation deadline ≠ on-chain expiry**: Worker stops polling after deadline; this
  reflects StellarScope's monitoring window, not blockchain transaction expiry.
- **Raw RPC JSON and XDR fields preserved in DB**: Evidence retention; XDR decoding
  may fail later but the raw data is retained.

## Project Documentation

- `DECISIONS.md` — Confirmed architectural decisions with rationale
- `PROJECT_STATUS.md` — Phase tracking and completed features
- `AUDIT_REPORT.md` and `VERIFICATION_REPORT.md` — Independent verification documents
- `.github/workflows/ci.yml` — GitHub Actions CI configuration
- `docker-compose.yml` — db + app + frontend services
- `pyproject.toml` and `requirements.txt` — Python package configuration
