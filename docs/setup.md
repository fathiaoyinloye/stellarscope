# StellarScope Setup Guide

This guide explains how to set up StellarScope for development and how to run
the application and worker. It is intended for new developers who need to get
the project running on their machine.

## Prerequisites

- **Python 3.10+**
- **PostgreSQL 15+** (or Docker Desktop/Engine with Docker Compose)
- **pip** (Python package installer)
- **git** (for cloning the repository)

## 1. Quick Start (Docker Compose Recommended)

The easiest way to get started is using Docker Compose, which brings up PostgreSQL
and the API in containers.

```bash
# 1. Clone the repository (if not already done)
git clone <repository-url>
cd stellarscope

# 2. Start PostgreSQL and the API container
docker compose up -d

# 2b. Wait for PostgreSQL to be ready
#    Check: docker compose logs db | grep -i "ready\|initialized"
#    Or: pg_isready -U postgres -h localhost

# 3. Apply database migrations inside the app container
docker exec stellarscope-app alembic upgrade head

# 4. The API is now running at http://localhost:8000
#    OpenAPI docs at http://localhost:8000/docs

# 5. Start the background worker in a separate terminal
docker exec stellarscope-app python -m stellarapi.monitoring.worker
```

### Docker Compose Services

| Service | Description | Ports |
|---|---|---|
| `db` | PostgreSQL 15-alpine | `5432:5432` |
| `app` | StellarScope API + worker capable | `8000:8000` |

### Running Just the Tests in Docker CI

The GitHub Actions CI uses `python:3.12-slim`. To reproduce:

```bash
docker run --rm -v "$PWD:/app" -w /app python:3.12-slim \
  bash -c "pip install --upgrade pip && pip install -e '.[dev]' && pytest -v"
```

## 2. Manual Setup (Without Docker)

### Step 1: Install Dependencies

```bash
git clone <repository-url>
cd stellarscope
pip install -e ".[dev]"
```

The `.[dev]` extra installs test and development dependencies.

### Step 2: Configure Environment

```bash
cp configs/.env.example .env
```

Edit `.env` as needed. Key variables and defaults:

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/stellarscope` | PostgreSQL connection string |
| `STELLAR_SCOPE_SOROBAN_RPC_URL` | `https://soroban-testnet.stellar.org` | Stellar RPC endpoint |
| `STELLAR_NETWORK` | `testnet` | Network identifier (testnet only in MVP) |
| `MONITORING_INTERVAL_SECONDS` | `60` | Seconds between worker poll cycles |
| `WORKER_LEASE_TIMEOUT_MINUTES` | `3` | How long a worker holds a claimed job lease |
| `OBSERVATION_DEADLINE_MINUTES` | `60` | Max minutes the worker monitors a transaction |
| `RPC_TIMEOUT_SECONDS` | `30` | Timeout for individual RPC calls |
| `ENV` | `development` | Environment name (affects logging, etc.) |

### Step 3: Start PostgreSQL

Ensure a PostgreSQL instance is running and accessible. If using Docker Compose:

```bash
docker compose up -d db
```

If manual:

- PostgreSQL must have a `stellarscope` database
- User `postgres` must have access (password-less with `trust` auth, or set
  `DATABASE_URL` with credentials)
- Listening on `localhost:5432` (or adjust `DATABASE_URL` host port)

### Step 4: Apply Database Migrations

```bash
alembic upgrade head
```

This creates the `monitoring_jobs` table with all columns, constraints, and
partial indexes defined in `src/stellarapi/database/models.py`.

### Step 5: Start the API

```bash
uvicorn src.stellarapi.main:app --host 0.0.0.0 --port 8000
```

The API will be available at `http://0.0.0.0:8000`.

**Verify**:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{
  "status": "ok",
  "service": "stellarscope",
  "version": "0.1.0",
  "environment": "development"
}
```

### Step 6: Start the Background Worker

```bash
python -m stellarapi.monitoring.worker
```

The worker runs until gracefully shut down (SIGINT/SIGTERM). It:

- Claims jobs via `SELECT ... FOR UPDATE SKIP LOCKED`
- Processes bounded batches (default 5 jobs per cycle)
- Calls `getTransaction` via the RPC client
- Normalizes and decodes XDR
- Updates monitoring status in DB
- Supports graceful shutdown within 15 seconds

### Step 7: Access the API

- **API**: `http://localhost:8000`
- **OpenAPI docs**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

### Step 8: Run Tests

```bash
pytest -v
```

**Expected results**: 32 of 37 tests pass. The 5 failing tests have
pre-existing fixture/interference issues with the shared PostgreSQL test
database (detailed in the Known Limitations section below).

## 3. Common Setup Problems and Solutions

| Problem | Solution |
|---|---|
| `ImportError: cannot import name ...` | Ensure you installed with `pip install -e ".[dev]"` from the project root |
| `asyncpg.exceptions.CannotConnectNowError` | Verify PostgreSQL is running and `DATABASE_URL` is correct |
| `alembic: No migration file found` | Run from the project root directory; ensure `alembic.ini` is present |
| `Worker appears to hang` | Check for unhandled exceptions in worker output; verify `DATABASE_URL` |
| `422 Validation Error on hash format` | Hash must be exactly 64 hex characters (`^[0-9a-fA-F]{64}$`) |
| `409 Conflict: Transaction already registered` | The hash is already in the database; use a different hash or clear DB |

## 4. Environment Variables Reference

See `configs/.env.example` for the complete list. Important ones:

```bash
# Database
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/stellarscope

# Stellar Network
STELLAR_SCOPE_SOROBAN_RPC_URL=https://soroban-testnet.stellar.org
STELLAR_NETWORK=testnet

# Monitoring
MONITORING_INTERVAL_SECONDS=60
WORKER_LEASE_TIMEOUT_MINUTES=3
OBSERVATION_DEADLINE_MINUTES=60

# RPC
RPC_TIMEOUT_SECONDS=30

# Application
ENV=development
LOG_LEVEL=INFO
```

## 5. Known Limitations (Setup Notes)

- **Testnet only**: The `SOROBAN_RPC_URL` defaults to the Testnet endpoint.
  Mainnet RPC requires a different URL configuration.
- **5 pre-existing test fixture issues**: Three workflow/concurrency tests have
  interference when running with the shared `setup_database` pytest fixture.
  One model test (`test_model_creation_and_persistence`) has a hash collision
  when run with RPC integration tests. All 9 model tests pass in isolation.
- **Observation deadline**: Worker stops polling after 60 minutes (default) even
  if the transaction has not yet reached a terminal state. This is not an on-chain
  expiry; it is StellarScope's monitoring timeout.
- **No live network verification**: No transactions have been submitted, and no
  funds are at risk. Project targets Testnet only.

## 6. Verifying the Setup

After following the steps above, verify:

1. **API health**: `curl http://localhost:8000/health` returns 200 JSON
2. **API docs**: Open `http://localhost:8000/docs` in a browser
3. **Worker running**: Check worker terminal output for "Worker starting" log
4. **Database migrations**: `alembic upgrade head` completed without error
5. **Test suite**: `pytest -v` runs (32 passed, 5 pre-existing failures)

## 6. Docker-Specific Notes

- The `docker-compose.yml` file defines two services: `db` (PostgreSQL) and
  `app` (StellarScope API)
- The `app` service depends on `db` being healthy (`condition: service_healthy`)
- Environment variables for the app service are set in `docker-compose.yml`
- To add a new env var, update both `configs/.env.example` and
  `docker-compose.yml`
- Docker volumes: `postgres_data` persists PostgreSQL data across container restarts

## 7. Shutting Down

- **API**: Ctrl+C in the uvicorn terminal
- **Worker**: Ctrl+C (the worker catches SIGINT/SIGTERM and finishes current
  cycle within 15 seconds)
- **Docker**: `docker compose down` stops and removes containers; data in
  `postgres_data` volume persists if you use `docker compose up -d` again later