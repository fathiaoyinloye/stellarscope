"""Shared test configuration and fixtures for StellarScope."""

import pytest
from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, AsyncSession, async_sessionmaker

from stellarapi.database.models import Base

TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/stellarscope"

# Session-scoped async engine, created once for the entire test session
# Using NullPool to avoid connection pool event-loop conflicts across pytest-asyncio
_engine: AsyncEngine | None = None


@pytest.fixture(scope="session", autouse=True)
async def engine_fixture() -> AsyncEngine:
    """Create a session-scoped async engine.

    The engine is created once and shared across all tests.
    It is disposed after the last test completes.
    Uses NullPool to avoid asyncpg event-loop conflicts when the shared
    connection pool is used across pytest-asyncio event loop boundaries.
    """
    global _engine
    _engine = create_async_engine(
        TEST_DATABASE_URL, echo=False, future=True, poolclass=NullPool
    )
    yield _engine
    await _engine.dispose()


@pytest.fixture(scope="session", autouse=True)
async def setup_database(engine_fixture) -> None:
    """Create all database tables once per test session.

    After all tests complete, all tables are dropped.
    """
    async with engine_fixture.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield  # Run all tests
    async with engine_fixture.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(scope="function")
async def session(engine_fixture) -> AsyncSession:
    """Create a function-scoped async session for each test.

    Each test gets a fresh session bound to the session-scoped engine.
    The session is closed after the test. Transaction management is handled
    by the test itself (commit/refresh).
    Tables are shared across tests (created once by setup_database).
    """
    async_session = async_sessionmaker(
        bind=engine_fixture, class_=AsyncSession, expire_on_commit=False
    )
    session = async_session()
    yield session
    await session.close()