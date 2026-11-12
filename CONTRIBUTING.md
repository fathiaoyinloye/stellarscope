# Contributing to StellarScope

Thank you for considering contributing to StellarScope! This project provides transaction monitoring infrastructure for Stellar and Soroban applications. We welcome contributions from the community.

## Development Environment Setup

1. **Clone the repository:**
   ```bash
   git clone git@github.com:fathiaoyinloyle/stellarscope.git
   cd stellarscope
   ```

2. **Start the infrastructure:**
   ```bash
   docker compose up -d db
   # Wait for the database to be ready
   until PGPASSWORD=postgres psql -U postgres -d stellarscope -c 'SELECT 1' > /dev/null 2>&1; do
     sleep 2
   done
   ```

3. **Install dependencies:**
   ```bash
   pip install -e ".[dev]"
   cd frontend && npm ci
   ```

4. **Apply database migrations:**
   ```bash
   alembic upgrade head
   ```

5. **Start the application:**
   ```bash
   docker compose up -d app worker
   ```

6. **Run the backend test suite:**
   ```bash
   cd backend
   PYTHONPATH=src python -m pytest tests/ -v
   ```

7. **Build the frontend:**
   ```bash
   cd frontend
   npm run build
   ```

## Installation

- **Backend:** Python 3.12+, FastAPI, SQLAlchemy 2.x, asyncpg, PostgreSQL
- **Frontend:** Node.js 20+, React 18, Vite, Axios
- **Database:** PostgreSQL 15+
- **Stellar RPC:** Testnet or Mainnet endpoint

## Database Setup and Migrations

- Migrations are managed via Alembic. The migration chain is:
  - `20251101_initial` - Initial monitoring_jobs table
  - `20261103_contract_discovery` - contracts_discovery table
  - `20261110_alerts_table` - alerts table
- To add a new migration: `alembic revision --head 20261110_alerts_table`
- Run `alembic upgrade head` to apply pending migrations

## Backend Test Execution

- Run the full test suite: `pytest tests/ -v`
- Tests use pytest-asyncio with per-test database isolation
- The `session` fixture provides a fresh database connection for each test
- Concurrency tests verify job claiming and lease expiry recovery

## Frontend Build

- `npm run build` produces a production bundle in the `dist/` directory
- The build uses Vite with the React plugin
- Ensure `npm ci` is run before building

## Code Quality Expectations

- Follow the existing code style (ruff for linting, black for formatting if applicable)
- Write tests for new functionality
- Ensure backward compatibility with existing API endpoints
- Document any architectural decisions in DECISIONS.md

## Branch and Pull Request Workflow

1. Create a branch from `main`: `git checkout -b feature/your-feature`
2. Make your changes and commit: `git commit -m "feat: add your feature"`
3. Push to your fork: `git push origin feature/your-feature`
4. Open a Pull Request against the `main` branch
5. Ensure all CI checks pass
- Fill in the PR template with a clear description of the change
- Reference any related issues

## What a Good Contribution Should Include

- A clear description of the problem or feature request
- Minimal, focused code changes
- Tests that verify the new functionality
- Updated documentation if user-facing behavior changes
- No breaking changes to existing API endpoints without discussion

## Issues and Questions

- Use the GitHub Issue templates for bug reports and feature proposals
- For questions, visit the [Discussions](https://github.com/your-org/stellarscope/discussions) page
- Join the [Stellar Developer Discord](https://discord.gg/stellar) for real-time help