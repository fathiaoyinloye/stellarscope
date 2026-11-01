"""Shared test configuration and fixtures for StellarScope."""

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.stellarapi.database.models import Base

# Use Docker Compose PostgreSQL or fallback to localhost
TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/stellarscope"


@pytest.fixture(scope="session", autouse=True)
async def setup_database():
    """Set up and tear down the test database schema."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        # Create all tables defined in Base.metadata
        await conn.run_sync(Base.metadata.create_all)

    yield  # Run tests

    # Drop all tables after tests
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(scope="function")
async def session(setup_database) -> AsyncSession:
    """Create a function-scoped async session for each test."""
    async_session = sessionmaker(setup_database, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session