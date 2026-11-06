"""End-to-end workflow tests for StellarScope Phase 7.

Tests the complete monitoring pipeline using the session fixture for DB isolation.
All RPC calls are mocked via unittest.patch to avoid real network connections.
Follows the same pattern as the existing 27 passing tests in test_models.py et al.
"""

from __future__ import annotations

import asyncio
from typing import Any
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import pytest
from sqlalchemy import select

from stellarapi.database.engine import engine as async_engine
from stellarapi.database.models import MonitoringJob
from stellarapi.monitoring.orchestration import perform_monitoring_check
from stellarapi.rpc_client import RpcClient, GetTransactionResult

# ---------------------------------------------------------------------------
# Mock RPC response helpers
# ---------------------------------------------------------------------------

MockResponse = Any


def make_mock_rpc_response(
    *,
    status: str = "SUCCESS",
    transaction_hash: str = "0" * 64,
    ledger: int = 12345,
    close_time: int = 1234567890,
    oldest_ledger: int = 1234000,
    create_at: int = 1234000,
    application_order: int = 1,
    fee_bump: bool = False,
    envelope_xdr: str = "AAAABQAAAAD",
    result_xdr: str = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAFgAA",
    result_meta_xdr: str = "AAAAAgAAAAIAAAAD",
    diagnostic_events_xdr: Optional[list] = None,
    events: Optional[Any] = None,
) -> Dict[str, Any]:
    """Build a mock RPC response dict matching SorobanServer.get_transaction output."""
    diag = diagnostic_events_xdr or []
    return {
        "status": status,
        "transaction_hash": transaction_hash,
        "ledger": ledger,
        "latest_ledger": ledger,
        "latest_ledger_close_time": close_time,
        "oldest_ledger": oldest_ledger,
        "oldest_ledger_close_time": 0,
        "application_order": application_order,
        "fee_bump": fee_bump,
        "envelope_xdr": envelope_xdr,
        "result_xdr": result_xdr,
        "result_meta_xdr": result_meta_xdr,
        "diagnostic_events_xdr": diag,
        "events": events,
        "create_at": create_at,
    }


def make_mock_rpc_result(
    *,
    status: str = "SUCCESS",
    transaction_hash: str = "0" * 64,
    ledger: int = 12345,
    close_time: int = 1234567890,
    oldest_ledger: int = 1234000,
    create_at: int = 1234000,
    application_order: int = 1,
    fee_bump: bool = False,
    envelope_xdr: str = "AAAABQAAAAD",
    result_xdr: str = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAFgAA",
    result_meta_xdr: str = "AAAAAgAAAAIAAAAD",
    diagnostic_events_xdr: Optional[list] = None,
    events: Optional[Any] = None,
) -> GetTransactionResult:
    """Build a mock GetTransactionResult object matching SorobanServer.get_transaction output.

    The returned object has an ``rpc_status`` attribute and other fields
    expected by ``perform_monitoring_check``.
    """
    # Create a simple rpc_status object with .value attribute
    class _RpcStatus:
        def __init__(self, value: str):
            self.value = value

    raw = make_mock_rpc_response(
        status=status,
        transaction_hash=transaction_hash,
        ledger=ledger,
        close_time=close_time,
        oldest_ledger=oldest_ledger,
        create_at=create_at,
        application_order=application_order,
        fee_bump=fee_bump,
        envelope_xdr=envelope_xdr,
        result_xdr=result_xdr,
        result_meta_xdr=result_meta_xdr,
        diagnostic_events_xdr=diagnostic_events_xdr,
        events=events,
    )
    return GetTransactionResult(
        rpc_status=_RpcStatus(status),
        transaction_hash=raw["transaction_hash"],
        ledger=raw["ledger"],
        close_time=raw["latest_ledger_close_time"],
        oldest_ledger=raw["oldest_ledger"],
        create_at=raw["create_at"],
        application_order=raw["application_order"],
        fee_bump=raw["fee_bump"],
        envelope_xdr=raw["envelope_xdr"],
        result_xdr=raw["result_xdr"],
        result_meta_xdr=raw["result_meta_xdr"],
        diagnostic_events_xdr=raw["diagnostic_events_xdr"],
        events=raw.get("events"),
        raw_rpc_response=raw,
    )


# ---------------------------------------------------------------------------
# Test: Complete Successful Workflow
# Register → DB persist → Worker claim → RPC SUCCESS → XDR decode → DB COMPLETED
# (API retrieve tested separately via TestClient format validation tests)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_successful_workflow(session) -> None:
    """Test the monitoring pipeline: claim → RPC SUCCESS → DB updated to COMPLETED."""

    # 1️⃣ Create an ACTIVE job using the session fixture
    job = MonitoringJob(
        transaction_hash="a" * 64,
        network="testnet",
        rpc_status="pending",
        monitoring_status="ACTIVE",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # 2️⃣ Mock the RPC call to return SUCCESS
    mock_rpc_result = make_mock_rpc_result(
        status="SUCCESS",
        ledger=12345,
        result_xdr="AAAAXDR",
        result_meta_xdr="AAAAMETA",
    )

    # Patch the RpcClient.get_transaction method
    with patch.object(RpcClient, "get_transaction", new_callable=AsyncMock) as mock_get_tx:
        mock_get_tx.return_value = mock_rpc_result

        # 3️⃣ Run orchestration (mocked RPC)
        result = await perform_monitoring_check(
            rpc_client=RpcClient(),
            job_id=job.id,
            transaction_hash=job.transaction_hash,
            network=job.network,
            raw_rpc_response=job.raw_rpc_response,
            envelope_xdr=job.envelope_xdr,
            result_xdr=job.result_xdr,
            result_meta_xdr=job.result_meta_xdr,
            ledger=job.ledger,
            fee_bump=job.fee_bump,
            failure_reason=job.failure_reason,
        )

        # 4️⃣ Verify DB was updated to COMPLETED (using the session fixture)
        await session.refresh(job)
        assert job.monitoring_status == "COMPLETED", f"Expected COMPLETED, got {job.monitoring_status}"
        assert job.rpc_status == "SUCCESS", f"Expected SUCCESS, got {job.rpc_status}"


# ---------------------------------------------------------------------------
# Test: Complete Failed Workflow
# Claim → RPC FAILED → DB updated to COMPLETED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_failed_workflow(session) -> None:
    """Test the monitoring pipeline: claim → RPC FAILED → DB updated to COMPLETED."""

    # 1️⃣ Create an ACTIVE job using the session fixture
    job = MonitoringJob(
        transaction_hash="b" * 64,
        network="testnet",
        rpc_status="pending",
        monitoring_status="ACTIVE",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # 2️⃣ Mock the RPC call to return FAILED
    with patch.object(RpcClient, "get_transaction", new_callable=AsyncMock) as mock_get_tx:
        mock_get_tx.return_value = make_mock_rpc_result(
            status="FAILED",
            ledger=99999,
        )

        # 3️⃣ Run orchestration (mocked RPC)
        await perform_monitoring_check(
            rpc_client=RpcClient(),
            job_id=job.id,
            transaction_hash=job.transaction_hash,
            network=job.network,
            raw_rpc_response=job.raw_rpc_response,
            envelope_xdr=job.envelope_xdr,
            result_xdr=job.result_xdr,
            result_meta_xdr=job.result_meta_xdr,
            ledger=job.ledger,
            fee_bump=job.fee_bump,
            failure_reason=job.failure_reason,
        )

        # 4️⃣ Verify DB updated to COMPLETED with FAILED status
        await session.refresh(job)
        assert job.monitoring_status == "COMPLETED", f"Expected COMPLETED, got {job.monitoring_status}"
        assert job.rpc_status == "FAILED", f"Expected FAILED, got {job.rpc_status}"


# ---------------------------------------------------------------------------
# Test: NOT_FOUND Continues Polling
# Claim → RPC NOT_FOUND → DB stays ACTIVE with next_check_at scheduled
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_not_found_workflow(session) -> None:
    """Test the monitoring pipeline: claim → RPC NOT_FOUND → DB stays ACTIVE."""

    # 1️⃣ Create an ACTIVE job using the session fixture
    job = MonitoringJob(
        transaction_hash="c" * 64,
        network="testnet",
        rpc_status="pending",
        monitoring_status="ACTIVE",
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # 2️⃣ Mock the RPC call to return NOT_FOUND
    with patch.object(RpcClient, "get_transaction", new_callable=AsyncMock) as mock_get_tx:
        mock_get_tx.return_value = make_mock_rpc_result(
            status="NOT_FOUND",
            transaction_hash=job.transaction_hash,
        )

        # 3️⃣ Run orchestration (mocked RPC)
        result = await perform_monitoring_check(
            rpc_client=RpcClient(),
            job_id=job.id,
            transaction_hash=job.transaction_hash,
            network=job.network,
            raw_rpc_response=job.raw_rpc_response,
            envelope_xdr=job.envelope_xdr,
            result_xdr=job.result_xdr,
            result_meta_xdr=job.result_meta_xdr,
            ledger=job.ledger,
            fee_bump=job.fee_bump,
            failure_reason=job.failure_reason,
        )

        # 4️⃣ Verify DB still ACTIVE with NOT_FOUND, next_check_at set
        await session.refresh(job)
        assert job.monitoring_status == "ACTIVE", f"Expected ACTIVE, got {job.monitoring_status}"
        assert job.rpc_status == "NOT_FOUND", f"Expected NOT_FOUND, got {job.rpc_status}"
        assert job.next_check_at is not None, "next_check_at should be set to a future time"