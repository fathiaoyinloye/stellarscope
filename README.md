# stellarscope

StellarScope — Python backend service for monitoring Stellar/Soroban transactions.

## Repository Structure

The project follows a modular monolith architecture with a separately executable background monitoring worker:

```
configs/          - Environment and runtime configuration
alembic/          - Database schema migrations (SQLAlchemy 2.x async)
src/stellarapi/   - FastAPI application package
├── __init__.py
├── main.py        - FastAPI app entry point
│   ├── config/               - Settings (pydantic-settings)
│   ├── core/                 - Core utilities (logging)
│   ├── database/             - SQLAlchemy async engine/session
│   │   ├── models.py         - SQLAlchemy 2.x async models (Phase 3)
│   │   ├── engine.py         - Async engine & session factory
│   │   ├── session.py        - Async session context manager
│   │   └── migration.py      - Database access layer (Phase 3)
│   ├── api/                  - API routes (health, future: register/monitor)
│   └── worker/               - Background worker skeleton
tests/             - pytest test suite
│   ├── conftest.py
│   ├── test_health.py       - Health endpoint test
│   └── test_models.py       - Database model tests (Phase 3, requires PostgreSQL)
docker/            - Docker configuration
pyproject.toml     - Project configuration (build, deps, tools)
requirements.txt   - Python dependencies
.github/           - GitHub Actions workflows (CI, testing, Docker builds)
PROJECT_STATUS.md  - Project status tracking
```

## Key Architecture Decisions

- **FastAPI** for the HTTP API and worker process
- **SQLAlchemy 2.x async** with **Alembic** for database migrations
- **stellar-sdk** for Stellar network interaction
- **HTTPX** for async HTTP client needs
- **PostgreSQL** as the primary data store
- Modular monolith: all application code in `src/stellarapi/`, with separate sub-packages for API routes, core logic, services, and the background worker

## Technology Stack

- Python 3.10+
- FastAPI
- PostgreSQL
- SQLAlchemy 2.x async
- Alembic
- stellar-sdk
- HTTPX
- pytest and pytest-asyncio
- Docker and Docker Compose
- GitHub Actions

## Getting Started

See `configs/.env.example` for environment variable configuration.

### Quick Start (Development)

1. **Install dependencies**

   ```bash
   # Using the project's pyproject.toml
   pip install -e ".[dev]"

   # Or using requirements.txt
   pip install -r requirements.txt
   ```

2. **Start PostgreSQL**

   Using Docker Compose (included in the repo):

   ```bash
   docker compose up -d
   ```

   This starts a PostgreSQL instance on `localhost:5432` with:
   - Database name: `stellarscope`
   - User: `postgres`
   - No password (Docker default)

   Alternatively, ensure a PostgreSQL instance is running and set the
   ``DATABASE_URL`` environment variable accordingly:

   ```
   DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST:PORT/stellarscope
   ```

3. **Configure environment**

   Copy the example env file and adjust as needed:

   ```bash
   cp configs/.env.example .env
   ```

   Edit ``.env`` if you changed the PostgreSQL connection details.

4. **Run the application**

   ```bash
   uvicorn src.stellarapi.main:app --host 0.0.0.0 --port 8000
   ```

   Or using the project entrypoint:

   ```bash
   stellarscope
   ```

   The API will be available at ``http://0.0.0.0:8000``.

5. **Verify the health endpoint**

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

6. **Run the test suite**

   ```bash
   pytest -v
   ```

   Phase 2 tests (health check) should pass. Phase 3 database tests
   require PostgreSQL and can be run after migration:

   ```bash
   alembic upgrade head
   pytest -v tests/test_models.py
   ```

### Directory Overview

```
stellarscope/
├── configs/                 - Environment variables (.env.example)
├── alembic/                 - Database migration framework
│   ├── versions/20251101_initial.py  - Initial migration (Phase 3)
│   ├── env.py
│   └── script.py.mako
├── src/stellarapi/          - Modular monolith package
│   ├── __init__.py
│   ├── main.py              - FastAPI entry point
│   ├── config/               - Settings (pydantic-settings)
│   ├── core/                 - Core utilities (logging)
│   ├── database/             - SQLAlchemy async engine/session
│   │   ├── models.py         - SQLAlchemy 2.x async models (Phase 3)
│   │   ├── engine.py         - Async engine & session factory
│   │   ├── session.py        - Async session context manager
│   │   └── migration.py      - Database access layer (Phase 3)
│   ├── api/                  - API routes (health, future: register/monitor)
│   └── worker/               - Background worker skeleton
├── tests/                   - pytest test suite
│   ├── conftest.py
│   ├── test_health.py       - Health endpoint test
│   └── test_models.py       - Database model tests (Phase 3, requires PostgreSQL)
├── docs/                    - Phase 1 research & design documents
│   ├── requirements.md
│   ├── technical-research.md
│   ├── monitoring-design.md
│   └── risks-and-open-questions.md
├── DECISIONS.md             - Confirmed architectural decisions
├── PROJECT_STATUS.md        - Phase tracking
├── docker/                  - Dockerfile (app image)
├── docker-compose.yml       - Local dev: PostgreSQL + app
├── pyproject.toml           - Project metadata, dependencies, tool config
├── requirements.txt         - Python dependencies (prod + dev)
└── .github/                 - GitHub Actions CI/CD
```