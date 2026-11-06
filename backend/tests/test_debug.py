"""Debug test for database models."""
import pytest

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import sessionmaker

from stellarapi.database.models import Base, MonitoringJob

TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/stellarscope"


@pytest.fixture(scope="session", autouse=True)
async def setup_database():
    """Set up and tear down the test database schema."""
    engine = create_async_engine(
        TEST_DATABASE_URL, echo=False, future=True
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(scope="function")
async def session(setup_database) -> AsyncSession:
    """Create a function-scoped async session for each test."""
    async_session = async_sessionmaker(bind=setup_database, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session


@pytest.mark.asyncio
async def test_model_creation_and_persistence(session) -> None:
    """Test that a MonitoringJob can be created and persisted to the database."""
    job = MonitoringJob(
        transaction_hash="0" * 64,
        network="testnet",
        rpc_status="SUCCESS",
        monitoring_status="ACTIVE",
        raw_rpc_response={"status": "success", "ledger": 12345},
        envelope_xdr="AAAABQAAAAD",
        result_xdr="AAAAAAAAGAAAAAAgAA",
        result_meta_xdr="AAAAAQAAAAIAAAAD",
        ledger=12345,
        fee_bump=False,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    assert job.id is not None
    assert job.transaction_hash == "0" * 64
    assert job.network == "testnet"
    assert job.rpc_status == "SUCCESS"
    assert job.monitoring_status == "ACTIVE"
    assert job.raw_rpc_response == {"status": "success", "ledger": 12345}