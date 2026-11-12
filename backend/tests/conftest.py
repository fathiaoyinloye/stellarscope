"""Shared test configuration and fixtures for StellarScope."""

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from stellarapi.database.models import Base

TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/stellarscope"

# Module-level variable to track if tables are created
_tables_created = False


@pytest.fixture(scope="function", autouse=True)
async def setup_database() -> None:
    """Create all tables and truncate data for each test function."""
    global _tables_created
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, future=True, poolclass=None)
    if not _tables_created:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _tables_created = True
    # Truncate monitoring_jobs and contracts_discovery to ensure clean state
    # for concurrency/race condition tests that insert jobs directly via engine.
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE monitoring_jobs, contracts_discovery CASCADE"))
    yield


@pytest.fixture(scope="function")
async def session() -> AsyncSession:
    """Create a new database session for each test, rolled back after."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, future=True, poolclass=None)
    async_session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    session_instance = async_session()
    try:
        yield session_instance
    finally:
        await session_instance.rollback()
        await session_instance.close()