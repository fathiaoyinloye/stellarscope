"""Concurrency and race condition tests for StellarScope Phase 7.

All DB operations use the async engine directly, avoiding TestClient + fixture
interference. Matches the pattern of the existing RPC integration tests.
"""

from __future__ import annotations

import asyncio
import pytest

from stellarapi.database.engine import engine as async_engine
from stellarapi.monitoring.job_claimer import claim_job, release_job
from stellarapi.database.models import MonitoringJob
from stellarapi.rpc_client import RpcClient


# ---------------------------------------------------------------------------
# 1. claim_job basic test using the engine directly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_claim_job_basic() -> None:
    """Test claim_job with fresh DB state using the engine directly.

    Creates jobs directly via the engine and tests claim_job functionality,
    avoiding TestClient + fixture interference issues.
    """
    # Create a single ACTIVE job using the engine
    async with async_engine.begin() as conn:
        await conn.execute(
            MonitoringJob.__table__.insert().values(
                transaction_hash="e" * 64,
                network="testnet",
                rpc_status="pending",
                monitoring_status="ACTIVE",
            )
        )

    # Claim the job - rpc_client is required by the implementation
    rpc_client = RpcClient()
    claimed = await claim_job(async_engine, rpc_client)
    assert claimed is not None, "Should be able to claim a job"
    assert claimed["transaction_hash"] == "e" * 64
    assert claimed["locked_by"] is not None

    # Release the lease
    await release_job(async_engine, claimed["job_id"])


# ---------------------------------------------------------------------------
# 2. Lease expiry recovery using the engine directly
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lease_expiry_recovery() -> None:
    """Test that after a job's lease expires, another worker can claim it.

    Uses the async engine directly for DB operations, avoiding
    TestClient + fixture interference.
    """
    # Create a job and claim it with a short lease using the engine
    async with async_engine.begin() as conn:
        await conn.execute(
            MonitoringJob.__table__.insert().values(
                transaction_hash="f" * 64,
                network="testnet",
                rpc_status="pending",
                monitoring_status="ACTIVE",
            )
        )

    # Claim the job with a 1-second lease
    claimed = await claim_job(async_engine, rpc_client=RpcClient(), lease_timeout_seconds=1)
    assert claimed is not None, "Should be able to claim the job"
    assert claimed["locked_by"] is not None

    # Before lease expires, another claim should fail (locked_until is in the future)
    still_claimed = await claim_job(async_engine, rpc_client=RpcClient(), lease_timeout_seconds=1)
    assert still_claimed is None, (
        "Before lease expiry, another worker should not be able to claim the same job"
    )

    # Wait for the lease to expire
    await asyncio.sleep(2)  # Wait just over 1 second

    # Now another worker should be able to claim it
    newly_claimed = await claim_job(async_engine, rpc_client=RpcClient(), lease_timeout_seconds=1)
    assert newly_claimed is not None, "After lease expiry, another worker should be able to claim"
    assert newly_claimed["transaction_hash"] == claimed["transaction_hash"]

    # Clean up
    await release_job(async_engine, claimed["job_id"])
    await release_job(async_engine, newly_claimed["job_id"])