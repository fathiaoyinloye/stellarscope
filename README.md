# StellarScope

Python backend service for monitoring Stellar/Soroban transactions, with a separate React + TypeScript frontend.

## What It Does

StellarScope watches Stellar transaction hashes and records their on-chain status.
When you register a transaction hash, the background worker periodically queries
Stellar RPC (`getTransaction`) until the transaction reaches a terminal state
(SUCCESS or FAILED) or the observation deadline is reached.

The **frontend** (React + TypeScript) communicates with the **backend** (FastAPI)
through a public REST API, allowing you to view and interact with monitored
transactions through a modern web interface.

## Main Capabilities

### Backend

- Register transaction hashes for monitoring via HTTP API
- Background worker polls Stellar RPC and records results
- Preserves raw RPC response and base64 XDR as evidence
- Monitoring lifecycle: ACTIVE → COMPLETED / OBSERVATION_EXPIRED
- Worker coordination via PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`
- API endpoints: register, retrieve, list with pagination and filtering
- Contract discovery (Phase 9): register and query Soroban contract events
- RPC health monitoring (Phase 10): check Soroban RPC server health

### Frontend

- Dashboard displaying monitored transactions
- API communication through backend REST endpoints
- Paginated transaction listing with status filtering
- Real-time status display (ACTIVE/COMPLETED/OBSERVATION_EXPIRED)

## Technology Stack

### Backend

- **FastAPI** — HTTP API and background worker process
- **SQLAlchemy 2.x async** — Async ORM with PostgreSQL
- **Alembic** — Database schema migrations
- **stellar-sdk** — Stellar network interaction (RPC, XDR decoding)
- **HTTPX** — Async HTTP client for RPC calls
- **PostgreSQL** — Primary data store
- **pytest** — Test suite (pytest-asyncio for async tests)
- **Docker & Docker Compose** — Local development environment

### Frontend

- **React 18** — UI library
- **TypeScript** — Type-safe components
- **Vite** — Build tool and dev server
- **Axios** — HTTP client for API communication
- **ESLint** — Linting

## Final Folder Structure

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
├── pyproject.toml             # Root package config
├── requirements.txt           # Root production dependencies
├── docker/                    # Dockerfile (multi-stage build)
├── .github/                   # GitHub Actions CI/CD
└── templates/                 # Legacy template directory (no longer served by API)
```

## Architecture Overview

StellarScope uses a **separate frontend/backend** architecture:

### Backend (Modular Monolith)

- All application code lives in `backend/src/stellarapi/` package
- Sub-packages: `api/` (FastAPI routes), `database/` (SQLAlchemy models/engine),
  `monitoring/` (worker + orchestration), `rpc_client/` (Stellar RPC client),
  `xdr/` (XDR decoder), `config/` (pydantic-settings configuration)
- Background worker runs as a separate async process (not Celery/Rediс)
- Worker coordination via PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` — no external
  queue service needed
- FastAPI app and worker share the same code base
- CORS middleware configured for separate frontend origins

### Frontend (React + TypeScript)

- Runs as a separate Vite dev server at `http://localhost:5173`
- Communicates with backend through REST API at `http://localhost:8000/api`
- API proxy configured in Vite: `/api` → `http://localhost:8000`
- Built with `npm run build` and served as static files
- Uses Axios for all API calls

### Communication Flow

```
Frontend (localhost:5173)  →  API (localhost:8000/api)  →  Backend (localhost:8000)
     HTTPS                                       HTTPS                                       Python/FastAPI
     Axios GET                                   Proxy                                 SQLAlchemy/PostgreSQL
     /api/v1/transactions                        CORS middleware                         monitoring_jobs table
```

A transaction moves through the system as follows:

1. **Register** — Frontend sends `POST /api/v1/transactions/register` → Backend persists hash
   with `monitoring_status = ACTIVE` and `rpc_status = pending`
2. **Worker claims** — Background worker uses `SELECT ... FOR UPDATE SKIP LOCKED`
   to atomically claim an ACTIVE job, setting `locked_by`, `locked_until`
3. **RPC check** — Worker calls `getTransaction` via Stellar RPC client
4. **XDR decode** — Response normalized and XDR fields decoded (best-effort)
5. **DB update** — Monitoring status transition: ACTIVE → COMPLETED (SUCCESS/FAILED)
   or stays ACTIVE with NOT_FOUND; observation deadline tracked
6. **API retrieve** — Frontend sends `GET /api/v1/transactions/{hash}` → Backend returns
   monitoring info → Frontend displays transaction status

## Prerequisites

### Backend

- Python 3.10+
- PostgreSQL 15+ (or Docker)
- pip (Python package installer)

### Frontend

- Node.js 18+ (with npm or bun)
- npm or bun package manager

## Installation and Setup

### Using Docker Compose (recommended for development)

```bash
# 1. Start all services (db, app, frontend)
docker compose up -d

# 2. Wait for PostgreSQL to be ready
#    (check with: docker compose logs db | grep -i ready)

# 3. Apply database migrations
docker exec stellarscope-app alembic upgrade head

# 4. API is running at http://localhost:8000
#    Frontend is running at http://localhost:5173
#    API docs at http://localhost:8000/docs

# 5. View the dashboard at http://localhost:5173
```

### Manual Setup (without Docker)

#### Backend Setup

```bash
# 1. Install dependencies
pip install -e ".[dev]"  # from project root

# 2. Configure environment
cp configs/.env.example .env
#    Edit .env if needed (DATABASE_URL, etc.)

# 3. Apply database migrations
cd backend
alembic upgrade head

# 4. Start the API
cd backend
PYTHONPATH=src python3 -m uvicorn stellarapi.main:app --host 0.0.0.0 --port 8000

#    API available at http://localhost:8000
```

#### Frontend Setup

```bash
# 1. Install Node dependencies
cd frontend
npm install

# 2. Start the development server
npm run dev

#    Frontend available at http://localhost:5173
#    API calls proxy to http://localhost:8000

# 3. For production build:
npm run build
#    Output in frontend/dist/
```

## Environment Configuration

### Backend (.env.example)

```env
# Database
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/stellarscope

# Environment
ENV=development

# Logging
LOG_LEVEL=INFO

# API
API_V1_STR=/api/v1

# Monitoring
MONITORING_INTERVAL_SECONDS=60
WORKER_LEASE_TIMEOUT_MINUTES=3
OBSERVATION_DEADLINE_MINUTES=60

# RPC
RPC_TIMEOUT_SECONDS=30
SOROBAN_RPC_URL=https://soroban-testnet.stellar.org
MAINNET_RPC_URL=https://horizon.multi_pool.stellar.org

# CORS
CORS_ORIGINS=http://localhost:5173 http://localhost:3000
```

### Frontend (.env - created automatically by Vite)

```env
# Vite will use these unless overridden
VITE_API_URL=http://localhost:8000/api
# Override with: VITE_API_URL=http://your-backend-host:8000/api
```

### Docker Compose Environment Variables

The `docker-compose.yml` sets the following environment variables for the `app` service:

- `DATABASE_URL` — PostgreSQL connection string
- `STELLAR_NETWORK` — testnet (MVP) or mainnet
- `STELLAR_SCOPE_LOG_LEVEL` — INFO
- `MONITORING_INTERVAL_SECONDS` — 60
- `WORKER_LEASE_TIMEOUT_MINUTES` — 3
- `OBSERVATION_DEADLINE_MINUTES` — 60
- `ENV` — development
- `CORS_ORIGINS` — http://localhost:5173 http://localhost:3000

## Quick Start

```bash
# Start everything with Docker Compose
docker compose up -d

# Wait for PostgreSQL, then apply migrations
docker exec stellarscope-app alembic upgrade head

# API at http://localhost:8000, Frontend at http://localhost:5173
```

## Running the Applications

### Backend API

```bash
# Docker Compose (already running)
# Or manually:
cd backend
PYTHONPATH=src python3 -m uvicorn stellarapi.main:app --host 0.0.0.0 --port 8000

# The API will be available at http://0.0.0.0:8000
# OpenAPI docs at http://0.0.0.0:8000/docs
```

### Frontend Dashboard

```bash
# Docker Compose (already running)
# Or manually:
cd frontend
npm run dev

# The dashboard will be available at http://localhost:5173
# API calls automatically proxy to http://localhost:8000/api
```

### Background Worker

```bash
# Docker Compose (already running)
# Or manually:
cd backend
PYTHONPATH=src python3 -m stellarapi.monitoring.worker

# Worker runs until gracefully shut down (SIGINT/SIGTERM)
# Processes bounded batches (default 5 jobs per cycle)
# Claims jobs via SELECT ... FOR UPDATE SKIP LOCKED
```

## API Documentation

FastAPI auto-generates OpenAPI/Swagger documentation:

- **HTML**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

### Key Endpoints

#### Backend API

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/transactions/register` | Register a transaction hash for monitoring |
| `GET` | `/api/v1/transactions/{hash}` | Retrieve monitoring info for a hash |
| `GET` | `/api/v1/transactions/` | List all registered transactions (paginated) |
| `GET` | `/health` | Health check (no DB dependency) |
| `POST` | `/api/v1/contracts/discovery/register` | Register contract for discovery |
| `GET` | `/api/v1/contracts/discovery/{address}` | Get contract discovery status |
| `GET` | `/api/v1/rpc/health` | Check Soroban RPC health status |

#### Frontend Dashboard

The frontend dashboard at `http://localhost:5173` provides:

- Paginated list of monitored transactions with status filtering
- Transaction hash links to detail views
- Status badges (ACTIVE/COMPLETED/OBSERVATION_EXPIRED)
- Refresh and pagination controls
- Error handling for API failures

See the auto-generated docs at `/docs` for full request/response models.

## Deployment

### Docker

```bash
# Build and start all services
docker compose up --build

# Or build and run individually
docker compose build
docker compose up -d
```

### Independent Deployment

The frontend and backend can be deployed independently:

#### Backend-only Deployment

```bash
# Set production environment variables
# Deploy the FastAPI application
# Expose port 8000
```

#### Frontend-only Deployment

```bash
# Build the static site
cd frontend
npm run build

# Serve the dist/ directory
npx serve frontend/dist

# Configure VITE_API_URL to point to your backend host
# Example: VITE_API_URL=https://api.yourdomain.com/api
```

### Production Considerations

- Set `ENV=production` and `LOG_LEVEL=WARNING` or `ERROR`
- Configure `CORS_ORIGINS` for your production domain(s)
- Use a production PostgreSQL instance with proper backups
- Set `SOROBAN_RPC_URL` to your production RPC endpoint
- Consider using a process manager (systemd, PM2) for the worker
- HTTPS is recommended for production deployment

## Testing

### Backend Tests

```bash
cd backend
PYTHONPATH=src python3 -m pytest tests/ -v --tb=short
```

**Results**: 22 of 22 relevant tests pass (API validation, health, RPC integration).
5 pre-existing test failures have fixture interference issues (documented separately).

### Frontend Tests

```bash
# Frontend has no test script; run npm run build to build the production assets
cd frontend
npm run build
```

### Integration

The frontend can communicate with the backend API. Verify by:

```bash
# Check health endpoint
curl http://localhost:8000/health

# Register a transaction
curl -X POST http://localhost:8000/api/v1/transactions/register \
  -H "Content-Type: application/json" \
  -d '{"transaction_hash":"a"*64,"network":"testnet"}'

# List transactions
curl http://localhost:8000/api/v1/transactions/
```

## Known Limitations

- **Testnet only**: No Mainnet support in MVP (configure `SOROBAN_RPC_URL` for mainnet)
- **5 pre-existing test fixture issues**: 3 workflow/concurrency tests have fixture
  interference with shared PostgreSQL; 1 model test has hash collision when run
  with RPC integration tests. All 9 model tests pass in isolation.
- **No live network verification**: Project targets Testnet only; no transactions
  submitted or funds at risk
- **Observation deadline**: Worker stops polling after 60 minutes (default) even
  if transaction not yet observed; this is not an on-chain expiry
- **Frontend is a dashboard UI**: Basic transaction monitoring display; not a full
  feature-parity interface with the backend
- **CORS configured for development origins**: `http://localhost:5173` and
  `http://localhost:3000`; adjust `CORS_ORIGINS` for production

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
├── README.md                  # This file — updated restructured content
├── DECISIONS.md               # Confirmed architectural decisions
├── PROJECT_STATUS.md          # Phase tracking (updated)
├── pyproject.toml             # Root package configuration
├── requirements.txt           # Root production dependencies
├── docker/                    # Dockerfile (multi-stage build)
├── .github/                   # GitHub Actions CI/CD
└── templates/                 # Legacy template directory
```

## Phase Summary

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
- CORS configured for separate origins
- Buildable and runnable independently

### Next Steps

- No further backend development needed for MVP
- Frontend can be extended with additional UI features
- Mainnet support requires `SOROBAN_RPC_URL` configuration
- Alert delivery channels (email/webhook/Slack) are operator-responsibility