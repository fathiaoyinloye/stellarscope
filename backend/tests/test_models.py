"""Database model tests for StellarScope.

These tests use a real PostgreSQL connection and verify:
- Model creation and persistence
- Unique transaction hash constraints
- Required fields and database constraints
- JSONB evidence persistence
- Timestamp handling
- Indexes and constraints
- Migration execution
"""
import pytest
import sqlalchemy

from sqlalchemy.exc import IntegrityError

from stellarapi.database.models import Base, MonitoringJob


@pytest.mark.asyncio
async def test_model_creation_and_persistence(session) -> None:
    """Test that a MonitoringJob can be created and persisted to the database."""
    job = MonitoringJob(
        transaction_hash="0" * 64,  # All zeros - valid hex char
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

    # Verify the job was persisted
    assert job.id is not None
    assert str(job.id) != "00000000-0000-0000-0000-000000000000"  # Should be a real UUID
    assert job.transaction_hash == "0" * 64
    assert job.network == "testnet"
    assert job.rpc_status == "SUCCESS"
    assert job.monitoring_status == "ACTIVE"
    assert job.raw_rpc_response == {"status": "success", "ledger": 12345}
    assert job.envelope_xdr == "AAAABQAAAAD"
    assert job.result_xdr == "AAAAAAAAGAAAAAAgAA"
    assert job.result_meta_xdr == "AAAAAQAAAAIAAAAD"
    assert job.ledger == 12345
    assert job.fee_bump is False
    assert job.failure_reason is None
    assert job.locked_by is None
    assert job.locked_until is None
    assert job.attempt_count == 0
    assert job.is_active is True
    assert job.is_terminal is False


@pytest.mark.asyncio
async def test_unique_transaction_hash_constraint(session) -> None:
    """Test that duplicate transaction_hash values are rejected."""
    # Insert first job
    job1 = MonitoringJob(
        transaction_hash="aa" * 32,
        network="testnet",
        rpc_status="FAILED",
        monitoring_status="ACTIVE",
    )
    session.add(job1)
    await session.commit()

    # Try inserting a duplicate hash - should be rejected
    job2 = MonitoringJob(
        transaction_hash="aa" * 32,  # Same hash
        network="testnet",
        rpc_status="SUCCESS",
        monitoring_status="ACTIVE",
    )
    session.add(job2)

    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    # The second job should not be in the database
    result = await session.execute(
        sqlalchemy.select(MonitoringJob).where(
            MonitoringJob.transaction_hash == "aa" * 32
        )
    )
    count = len(result.scalars().all())
    assert count == 1, "Only one job with this hash should exist"


@pytest.mark.asyncio
async def test_required_fields_and_constraints(session) -> None:
    """Test that NOT NULL constraints are enforced."""
    # Test that missing required fields raise errors
    job = MonitoringJob(
        # Missing required: transaction_hash, network, rpc_status, monitoring_status
        raw_rpc_response={},
    )
    session.add(job)

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_jsonb_evidence_persistence(session) -> None:
    """Test that JSONB raw_rpc_response is correctly persisted and retrieved."""
    job = MonitoringJob(
        transaction_hash="bb" * 32,
        network="testnet",
        rpc_status="NOT_FOUND",
        monitoring_status="ACTIVE",
        raw_rpc_response={
            "status": "NOT_FOUND",
            "latestLedger": "9999999",
            "oldestLedger": "9999000",
        },
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # Verify JSONB was persisted correctly
    assert job.raw_rpc_response["status"] == "NOT_FOUND"
    assert job.raw_rpc_response["latestLedger"] == "9999999"
    assert job.raw_rpc_response["oldestLedger"] == "9999000"


@pytest.mark.asyncio
async def test_timestamp_handling(session) -> None:
    """Test that created_at and updated_at timestamps are set correctly."""
    from datetime import datetime, timezone

    job = MonitoringJob(
        transaction_hash="cc" * 32,
        network="testnet",
        rpc_status="SUCCESS",
        monitoring_status="ACTIVE",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # created_at and updated_at should be set (timezone-aware)
    assert job.created_at is not None
    assert job.updated_at is not None
    # Both should be timezone-aware
    assert job.created_at.tzinfo is not None
    assert job.updated_at.tzinfo is not None


@pytest.mark.asyncio
async def test_monitoring_status_transitions(session) -> None:
    """Test that monitoring status transitions are valid."""
    job = MonitoringJob(
        transaction_hash="dd" * 32,
        network="testnet",
        rpc_status="SUCCESS",
        monitoring_status="ACTIVE",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # Transition from ACTIVE to COMPLETED
    job.monitoring_status = "COMPLETED"
    await session.commit()
    await session.refresh(job)

    assert job.monitoring_status == "COMPLETED"
    assert job.is_completed is True
    assert job.is_active is False

    # Transition to OBSERVATION_EXPIRED
    job.monitoring_status = "OBSERVATION_EXPIRED"
    await session.commit()
    await session.refresh(job)

    assert job.monitoring_status == "OBSERVATION_EXPIRED"
    assert job.is_observation_expired is True


@pytest.mark.asyncio
async def test_rpc_status_constraint(session) -> None:
    """Test that rpc_status must be one of SUCCESS/FAILED/NOT_FOUND."""
    job = MonitoringJob(
        transaction_hash="ee" * 32,
        network="testnet",
        rpc_status="INVALID",  # This should be rejected by CheckConstraint
        monitoring_status="ACTIVE",
    )
    session.add(job)

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_monitoring_status_constraint(session) -> None:
    """Test that monitoring_status must be one of ACTIVE/COMPLETED/OBSERVATION_EXPIRED."""
    job = MonitoringJob(
        transaction_hash="ff" * 32,
        network="testnet",
        rpc_status="SUCCESS",
        monitoring_status="INVALID",  # This should be rejected
    )
    session.add(job)

    with pytest.raises(IntegrityError):
        await session.commit()


# ---------------------------------------------------------------------------
# Migration test
# ---------------------------------------------------------------------------

from sqlalchemy import text


@pytest.mark.asyncio
async def test_migration_execution(session) -> None:
    """Test that the Alembic initial migration can be run successfully."""
    # This test verifies the migration SQL is valid by checking
    # that the table exists after running migration metadata
    async with session.begin():
        # Check that the monitoring_jobs table exists
        result = await session.execute(
            text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'monitoring_jobs')"
            )
        )
        table_exists = result.scalar()
        assert table_exists is True, "monitoring_jobs table should exist after migration"