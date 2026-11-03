# StellarScope

Python backend service for monitoring Stellar/Soroban transactions.

## What It Does

StellarScope watches Stellar transaction hashes and records their on-chain status.
When you register a transaction hash, the background worker periodically queries
Stellar RPC (`getTransaction`) until the transaction reaches a terminal state
(SUCCESS or FAILED) or the observation deadline is reached.

## Main Capabilities

- Register transaction hashes for monitoring via HTTP API
- Background worker polls Stellar RPC and records results
- Preserves raw RPC response and base64 XDR as evidence
- Monitoring lifecycle: ACTIVE → COMPLETED / OBSERVATION_EXPIRED
- Worker coordination via PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`
- API endpoints: register, retrieve, list with pagination and filtering
- Testnet-only MVP (no Mainnet support)

## Technology Stack

- **FastAPI** — HTTP API and background worker process
- **SQLAlchemy 2.x async** — Async ORM with PostgreSQL
- **Alembic** — Database schema migrations
- **stellar-sdk** — Stellar network interaction (RPC, XDR decoding)
- **HTTPX** — Async HTTP client for RPC calls
- **PostgreSQL** — Primary data store
- **pytest** — Test suite (pytest-asyncio for async tests)
- **Docker & Docker Compose** — Local development environment

## Architecture Overview

StellarScope uses a **modular monolith** architecture:

- All application code lives in `src/stellarapi/` package
- Sub-packages: `api/` (FastAPI routes), `database/` (SQLAlchemy models/engine),
  `monitoring/` (worker + orchestration), `rpc_client/` (Stellar RPC client),
  `xdr/` (XDR decoder), `core/` (logging)
- **Background worker** runs as a separate async process (not Celery/Rediс)
- Worker coordination via PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` — no external
  queue service needed
- API and worker share the same code base; worker is invoked via `python -m`
  `stellarapi.monitoring.worker`

A transaction moves through the system as follows:

1. **Register** — POST `/api/v1/transactions/register` persists the hash with
   `monitoring_status = ACTIVE` and `rpc_status = pending`
2. **Worker claims** — Background worker uses `SELECT ... FOR UPDATE SKIP LOCKED`
   to atomically claim an ACTIVE job, setting `locked_by`, `locked_until`
3. **RPC check** — Worker calls `getTransaction` via Stellar RPC client
4. **XDR decode** — Response normalized and XDR fields decoded (best-effort)
5. **DB update** — Monitoring status transition: ACTIVE → COMPLETED (SUCCESS/FAILED)
   or stays ACTIVE with NOT_FOUND; observation deadline tracked
6. **API retrieve** — GET `/api/v1/transactions/{hash}` returns monitoring info

## Prerequisites

- Python 3.10+
- PostgreSQL 15+ (or Docker)
- `pip` (Python package installer)

## Installation and Setup

### Using Docker Compose (recommended for development)

```bash
# 1. Start PostgreSQL and the API container
docker compose up -d

# 2. Wait for PostgreSQL to be ready
#    (check with: docker compose logs db | grep -i ready)

# 3. Apply database migrations
docker exec stellarscope-app alembic upgrade head

# 4. Start the API (already running in the app container)
#    API available at http://localhost:8000

# 5. Start the background worker in a separate terminal
docker exec stellarscope-app python -m stellarapi.monitoring.worker

# 6. Access the API documentation at http://localhost:8000/docs
```

### Manual Setup (without Docker)

```bash
# 1. Install dependencies
pip install -e ".[dev]"

# 2. Start PostgreSQL
#    Ensure a PostgreSQL instance is running with a `stellarscope` database
#    User: postgres, accessible on localhost:5432

# 3. Configure environment
cp configs/.env.example .env
#    Edit .env if needed (DATABASE_URL, etc.)

# 4. Apply database migrations
alembic upgrade head

# 5. Start the API
uvicorn src.stellarapi.main:app --host 0.0.0.0 --port 8000

# 6. Start the background worker (separate terminal)
python -m stellarapi.monitoring.worker

# 7. Access the API at http://localhost:8000
```

### Environment Configuration

```bash
cp configs/.env.example .env
```

Edit `.env` with your settings. Key variables:

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/stellarscope` | PostgreSQL connection |
| `STELLAR_SCOPE_SOROBAN_RPC_URL` | `https://soroban-testnet.stellar.org` | Stellar RPC endpoint |
| `STELLAR_NETWORK` | `testnet` | Network identifier (testnet only in MVP) |
| `MONITORING_INTERVAL_SECONDS` | `60` | Seconds between worker poll cycles |
| `WORKER_LEASE_TIMEOUT_MINUTES` | `3` | How long a worker holds a claimed job lease |
| `OBSERVATION_DEADLINE_MINUTES` | `60` | Max minutes the worker monitors a transaction |
| `RPC_TIMEOUT_SECONDS` | `30` | Timeout for individual RPC calls |

## Database Migration Commands

```bash
# Apply pending migrations (run after code deploy or Docker up)
alembic upgrade head

# Create a new migration (if adding new DB columns)
alembic revision --autogenerate -m "description"

# Rollback one migration
alembic downgrade -1
```

## How to Start the API

```bash
# Using uvicorn directly
uvicorn src.stellarapi.main:app --host 0.0.0.0 --port 8000

# Or using the project entrypoint (if configured in pyproject.toml)
stellarscope

# The API will be available at http://0.0.0.0:8000
# OpenAPI docs at http://0.0.0.0:8000/docs
```

## How to Start the Background Worker

```bash
python -m stellarapi.monitoring.worker
```

The worker runs until gracefully shut down (SIGINT/SIGTERM). It:

- Claims jobs via `SELECT ... FOR UPDATE SKIP LOCKED`
- Processes bounded batches (default 5 jobs per cycle)
- Calls `getTransaction` via RPC client
- Normalizes and decodes XDR
- Updates monitoring status in DB
- Supports graceful shutdown within 15 seconds

## How to Run Tests

```bash
# Run full test suite
pytest -v

# Run specific test files
pytest -v tests/test_rpc_integration.py
pytest -v tests/test_models.py
pytest -v tests/test_api_validation.py

# Model tests require database migrations first
alembic upgrade head
pytest -v tests/test_models.py
```

**Test results**: 32 of 37 tests pass consistently. The 5 failing tests
(`test_lease_expiry_recovery`, `test_model_creation_and_persistence`,
`test_workflow_integration.*`) have pre-existing fixture/interference issues with
the shared PostgreSQL test database. All 9 model tests pass in isolation.

## API Documentation

FastAPI auto-generates OpenAPI/Swagger documentation:

- **HTML**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

### Key Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/transactions/register` | Register a transaction hash for monitoring |
| `GET` | `/api/v1/transactions/{hash}` | Retrieve monitoring info for a hash |
| `GET` | `/api/v1/transactions/` | List all registered transactions (paginated) |
| `GET` | `/health` | Health check (no DB dependency) |

See the auto-generated docs at `/docs` for full request/response models.

## Current Limitations

- **Testnet only**: No Mainnet support in MVP
- **5 pre-existing test fixture issues**: 3 workflow/concurrency tests have fixture
  interference with shared PostgreSQL; 1 model test has hash collision when run
  with RPC integration tests; all 9 model tests pass in isolation
- **No live network verification**: Project targets Testnet only; no transactions
  submitted or funds at risk
- **No frontend**: Backend-only MVP; UI can be added later
- **Observation deadline**: Worker stops polling after 60 minutes (default) even
  if transaction not yet observed; this is not an on-chain expiry

## Repository Structure

```
stellarscope/
├── configs/              - Environment variables (.env.example)
├── alembic/              - Database migration framework
├── src/stellarapi/       - Modular monolith package
│   ├── main.py          - FastAPI entry point
│   ├── config/          - Settings (pydantic-settings)
│   ├── core/            - Core utilities (logging)
│   ├── database/        - SQLAlchemy async engine/session
│   ├── api/             - API routes (health, transaction monitoring)
│   ├── rpc_client.py    - Phase 4: Async Stellar RPC client
│   ├── xdr/             - Phase 4: XDR decoder module
│   └── worker/          - Background worker skeleton
├── tests/                - pytest test suite
├── docs/                - Phase 1-4 research and verification documents
├── DECISIONS.md         - Confirmed architectural decisions
├── PROJECT_STATUS.md    - Phase tracking
├── docker/              - Dockerfile (app image)
├── docker-compose.yml   - Local dev: PostgreSQL + app
└── .github/             - GitHub Actions CI/CD
```