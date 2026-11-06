"""Shared test configuration and fixtures for StellarScope."""

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from stellarapi.database.models import Base

TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/stellarscope"

# Module-level variable to track if tables are created
_tables_created = False

@pytest.fixture(scope="function", autouse=True)
async def setup_database():
    global _tables_created
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest.fixture(scope="function")
async def session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, future=True)
    async_session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        async with session.begin_nested():
            yield session
        await session.rollback()
