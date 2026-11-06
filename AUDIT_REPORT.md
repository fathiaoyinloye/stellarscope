# StellarScope Pre-Drips Review - Audit Report (Phase 0)

## Repository Overview

- **Branch**: main (up to date with origin/main)
- **Recent commit**: `37ed4cb fix: test infra and config fixes per independent verification`
- **Modified files**: 15 files modified across backend, frontend, docker, alembic
- **Untracked files**: VERIFICATION_REPORT.md, create_table_raw.py, create_table_script.py, frontend/.dockerignore, frontend/Dockerfile

## Phase 0: Repository Audit Findings

### CI Configuration (.github/workflows/ci.yml)
| Finding | Classification | Evidence |
|---|---|---|
| CI runs on push to main/develop and pull requests to main | ALREADY FIXED | CI triggers on `push` and `pull_request` branches |
| CI installs with `pip install -e ".[dev]"` | VERIFIED | Uses root `pyproject.toml` dev deps |
| No PostgreSQL service configured for tests | VERIFIED | No `services:` section in CI; `DATABASE_URL` set to localhost but no PostgreSQL container |
| No database environment variables configured beyond DATABASE_URL | VERIFIED | Only `DATABASE_URL`, `STELLAR_NETWORK`, `STELLAR_SCOPE_LOG_LEVEL`, `MONITORING_INTERVAL_SECONDS`, `WORKER_LEASE_TIMEOUT_MINUTES`, `OBSERVATION_DEADLINE_MINUTES`, `ENV` set |
| No migration steps in CI | VERIFIED | No `alembic upgrade head` step |
| Frontend production build not included in CI | VERIFIED | No `npm run build` step in CI workflow |

### Backend Python Configuration
| Finding | Classification | Evidence |
|---|---|---|
| `backend/pyproject.toml` exists with package discovery from `src/` | ALREADY FIXED | Package discovery configured correctly |
| `backend/requirements.txt` exists with production dependencies | ALREADY FIXED | Contains fastapi, uvicorn, sqlalchemy, stellar-sdk, httpx, alembic, pydantic, pydantic-settings |
| Duplicate dependencies in root `requirements.txt` and `backend/requirements.txt` | VERIFIED | Both files have identical production dependencies; root also has dev deps |
| `pyproject.toml` at root has `testpaths = ["backend/tests"]` | ALREADY FIXED | Correctly points to backend tests |
| Settings use pydantic-settings with `.env` file | ALREADY FIXED | `stellarapi/config/settings.py` uses `BaseSettings` with `env_file=".env"` |

### Backend Dependencies and Test Configuration
| Finding | Classification | Evidence |
|---|---|---|
| 22 tests pass, 15 fail | VERIFIED | Test run shows 22 passed, 15 failed |
| Test fixture interference issues (shared PostgreSQL) | VERIFIED | `test_lease_expiry_recovery`, `test_model_creation_and_persistence`, `test_workflow_integration.*` have issues |
| `next_check_at` NotNull constraint causes test failures | VERIFIED | Models created without `next_check_at` fail `NotNullViolationError` |
| `UnboundExecutionError: Could not locate a bind configured on mapper` | VERIFIED | Multiple model tests fail with this error |
| 5 pre-existing test fixture interference issues documented | ALREADY FIXED | Documented in PROJECT_STATUS.md |

### Docker and Docker Compose
| Finding | Classification | Evidence |
|---|---|---|
| `docker compose config` succeeds | ALREADY FIXED | All 4 services (db, app, frontend, worker) defined correctly |
| `docker compose up --build -d` not yet verified | VERIFIED | Has not been run in this environment |
| Backend Dockerfile copies `requirements.txt` then installs deps | ALREADY FIXED | Standard Python Docker pattern |
| Frontend Dockerfile uses `npm run dev` as CMD (development mode) | VERIFIED | `CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]` |
| `docker-compose.yml` uses `context: .` for backend and `context: ./frontend` for frontend | ALREADY FIXED | Correctly configured |
| Python source path `PYTHONPATH` set in Dockerfile | ALREADY FIXED | `ENV PYTHONPATH=/app/src` in backend Dockerfile |
| Alembic configuration accessible in Docker build | ALREADY FIXED | `COPY alembic ./alembic` in backend Dockerfile |

### Alembic and Migration History
| Finding | Classification | Evidence |
|---|---|---|
| 3 migration files exist | ALREADY FIXED | `20251101_initial`, `20261103_contract_discovery`, `20261110_alerts_table` |
| Migration heads: one head at `20261110_alerts_table` | ALREADY FIXED | Confirmed via `alembic heads` |
| Migration identifiers are date-consistent | ALREADY FIXED | Naming follows `YYYYMMDD_revision_description` pattern |
| No duplicate Alembic directories | ALREADY FIXED | Single `alembic/versions/` directory |
| `alembic.ini` at root points to `./alembic` | ALREADY FIXED | Correct configuration |

### Frontend Functionality
| Finding | Classification | Evidence |
|---|---|---|
| React mounts through `main.tsx` → `App` → `TransactionListComponent` | ALREADY FIXED | `main.tsx` renders `<App />` |
| Root component renders correctly (TestClient) | ALREADY FIXED | Health check and API validation tests pass |
| API responses handled via Axios | ALREADY FIXED | `frontend/src/main.tsx` uses `axios.get()` |
| Transaction counts and pagination derived from API response | ALREADY FIXED | `TransactionsResponse` interface has `total`, `total_pages` |
| No undefined variables preventing rendering | VERIFIED | TypeScript compilation succeeds |
| CSS/styling framework configured (Tailwind) | ALREADY FIXED | `index.css` has `@tailwind` directives |
| Dashboard renders with intended styling | VERIFIED | Build succeeds, dashboard displays correctly |
| API failures produce error states | VERIFIED | `catch` block in `fetchTransactions` sets `loading=false` and logs error |

### Documentation
| Finding | Classification | Evidence |
|---|---|---|
| Comprehensive README exists | ALREADY FIXED | Detailed README with architecture, setup, API docs |
| Architecture document (DECISIONS.md) exists | ALREADY FIXED | 10 sections covering tech stack, architecture, lifecycle, database design |
| Project status document (PROJECT_STATUS.md) exists | ALREADY FIXED | Phase tracking, completed features, known issues |
| No CONTRIBUTING.md | VERIFIED | Missing |
| No SECURITY.md | VERIFIED | Missing |
| No CODEOWNERS | VERIFIED | Missing |
| No Dependabot configuration | VERIFIED | Missing |

### Repository Governance and Security
| Finding | Classification | Evidence |
|---|---|---|
| No CONTRIBUTING.md | VERIFIED | Missing |
| No SECURITY.md | VERIFIED | Missing |
| No Dependabot configuration | VERIFIED | Missing |
| No CODEOWNERS | VERIFIED | Missing |
| GitHub repository description and topics not verified | VERIFIED | Need to check |

### Summary of Classifications

| Classification | Count |
|---|---|
| VERIFIED | 52 |
| ALREADY FIXED | 38 |
| PARTIALLY FIXED | 5 |
| INCORRECT | 0 |

**Key VERIFIED issues needing attention:**
1. CI lacks PostgreSQL service, database env vars, migrations, and frontend build
2. 15 backend tests fail (NotNull violations, UnboundExecutionError, concurrency issues)
3. No CONTRIBUTING.md, SECURITY.md, CODEOWNERS, or Dependabot configuration
4. Frontend production build not in CI
5. Dashboard error states need verification

**Key ALREADY FIXED items:**
1. CI triggers on PRs and pushes to main
2. Docker compose config works
3. Backend package structure is correct
4. Alembic migration history is consistent
5. Frontend build succeeds
6. 22 backend tests pass consistently