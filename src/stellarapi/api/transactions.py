"""Transaction monitoring API endpoints.

Provides endpoints for:
- Registering transaction hashes for monitoring
- Retrieving monitoring information by transaction hash
- Listing monitored transactions with pagination
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from pydantic import field_validator

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.stellarapi.config.settings import settings
from src.stellarapi.database.session import get_session
from src.stellarapi.database.models import MonitoringJob
from src.stellarapi.rpc_client import RpcClient, RpcCommunicationError, RpcResponseError
from src.stellarapi.xdr.decoder import decode_diagnostic_events

router = APIRouter(prefix="/api/v1/transactions", tags=["transactions"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class TransactionRegisterRequest(BaseModel):
    """Request body for registering a transaction for monitoring."""

    transaction_hash: str = Field(
        ...,
        description="64-character hex-encoded Stellar transaction hash",
    )
    network: str = Field(
        ...,
        description="Stellar network identifier (testnet only for MVP)",
    )

    @field_validator("transaction_hash")
    def validate_transaction_hash(cls, v: str) -> str:
        """Validate the transaction hash format."""
        import re
        if not re.match(r"^[0-9a-fA-F]{64}$", v):
            raise ValueError("Invalid transaction hash format: must be 64 hex characters")
        return v.lower()

    @field_validator("network")
    def validate_network(cls, v: str) -> str:
        """Validate the network field."""
        if v != "testnet":
            raise ValueError("Unsupported network: only testnet is supported in MVP")
        return v


class TransactionRegisterResponse(BaseModel):
    """Response body for successful transaction registration."""

    id: str = Field(..., description="Registration UUID")
    transaction_hash: str = Field(..., description="Transaction hash")
    network: str = Field(..., description="Network identifier")
    monitoring_status: str = Field(..., description="Monitoring lifecycle state")
    rpc_status: str = Field(..., description="Latest RPC getTransaction status")
    created_at: datetime = Field(..., description="Registration timestamp")
    next_check_at: Optional[datetime] = Field(
        None, description="Earliest timestamp for next poll"
    )

    model_config = {"from_attributes": True}


class TransactionResponse(BaseModel):
    """Response body for transaction retrieval."""

    id: str = Field(..., description="Registration identifier")
    transaction_hash: str = Field(..., description="Transaction hash")
    network: str = Field(..., description="Network identifier")
    monitoring_status: str = Field(..., description="Monitoring lifecycle state")
    rpc_status: str = Field(..., description="Latest RPC getTransaction status")
    ledger: Optional[int] = Field(
        None, description="Ledger sequence number"
    )
    created_at: datetime = Field(..., description="Registration timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    rpc_status_timestamp: Optional[datetime] = Field(
        None, description="When RPC status was observed"
    )
    failure_reason: Optional[str] = Field(
        None, description="Human-readable failure explanation"
    )
    result_xdr_base64: Optional[str] = Field(
        None, description="Base64-encoded result XDR (if available)"
    )
    envelope_xdr_base64: Optional[str] = Field(
        None, description="Base64-encoded envelope XDR (if available)"
    )
    diagnostic_events: Optional[list[dict]] = Field(
        None, description="Decoded diagnostic events (if available)"
    )
    next_check_at: Optional[datetime] = Field(
        None, description="When next poll is scheduled (if applicable)"
    )
    is_terminal: bool = Field(
        ..., description="Whether monitoring has reached a terminal state"
    )
    is_active: bool = Field(..., description="Whether currently being monitored")

    model_config = {"from_attributes": True}


class TransactionListItem(BaseModel):
    """Summary item for transaction listing."""

    id: str = Field(..., description="Registration UUID")
    transaction_hash: str = Field(..., description="Transaction hash")
    network: str = Field(..., description="Network identifier")
    monitoring_status: str = Field(..., description="Monitoring lifecycle state")
    rpc_status: str = Field(..., description="Latest RPC status")
    created_at: datetime = Field(..., description="Registration timestamp")
    is_terminal: bool = Field(..., description="Whether terminal state reached")

    model_config = {"from_attributes": True}


class TransactionListResponse(BaseModel):
    """Response body for transaction listing with pagination."""

    items: list[TransactionListItem]
    total: int = Field(..., description="Total number of items")
    page: int = Field(..., description="Current page number")
    per_page: int = Field(..., description="Items per page")
    total_pages: int = Field(..., description="Total number of pages")


# ---------------------------------------------------------------------------
# Helper function for datetime formatting
# ---------------------------------------------------------------------------


def _format_datetime(dt: Any) -> Any:
    """Format a datetime for JSON serialization."""
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(dt)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/register",
    response_model=TransactionRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Transaction already registered"}, 422: {"description": "Validation error"}},
)
async def register_transaction(
    request: TransactionRegisterRequest,
) -> TransactionRegisterResponse:
    """Register a transaction hash for monitoring.

    Does not synchronously poll Stellar RPC. The transaction is persisted
    with ``monitoring_status = 'ACTIVE'`` and ``rpc_status = 'pending'``,
    and will be picked up by the background worker on the next cycle.

    Args:
        request: Registration request with transaction hash and network.

    Raises:
        HTTPException: 409 if already registered, 422 if validation fails.
    """
    # Normalise hash to lower case (already done by validator)
    tx_hash = request.transaction_hash
    network = request.network

    async with get_session() as session:
        # Attempt to insert; IntegrityError on unique constraint means already registered
        new_job = MonitoringJob(
            transaction_hash=tx_hash,
            network=network,
            rpc_status="pending",
            monitoring_status="ACTIVE",
            # next_check_at will use the column default (now())
        )

        try:
            session.add(new_job)
            await session.commit()
            await session.refresh(new_job)
        except IntegrityError:
            # Duplicate transaction_hash – the unique constraint catches this
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Transaction already registered",
            )

    # Schedule the transaction for monitoring – the background worker
    # will pick it up on the next cycle. We deliberately do NOT call
    # getTransaction here; the worker handles that independently.

    return TransactionRegisterResponse(
        id=str(new_job.id),
        transaction_hash=new_job.transaction_hash,
        network=new_job.network,
        monitoring_status=new_job.monitoring_status,
        rpc_status=new_job.rpc_status,
        created_at=_format_datetime(new_job.created_at),
        next_check_at=_format_datetime(new_job.next_check_at),
    )


@router.get(
    "/{transaction_hash}",
    response_model=TransactionResponse,
    responses={
        404: {"description": "Transaction not registered"},
        422: {"description": "Invalid transaction hash format"},
    },
)
async def get_transaction(
    transaction_hash: str,
) -> TransactionResponse:
    """Retrieve monitoring information for a registered transaction.

    Args:
        transaction_hash: 64-char hex-encoded transaction hash.

    Raises:
        HTTPException: 404 if not registered, 422 if hash format invalid.
    """
    # Validate hash format
    if not re.match(r"^[0-9a-fA-F]{64}$", transaction_hash):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid transaction hash format: must be 64 hex characters",
        )

    hash_lower = transaction_hash.lower()

    async with get_session() as session:
        result = await session.execute(
            select(MonitoringJob).where(MonitoringJob.transaction_hash == hash_lower)
        )
        job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transaction not registered",
        )

    # Format diagnostic events if available
    diagnostic_events: Optional[list[dict]] = None
    if job.diagnostic_events_xdr:
        # Decode diagnostic events using the XDR decoder
        try:
            decoded = decode_diagnostic_events(job.diagnostic_events_xdr)
            diagnostic_events = [
                {
                    "type": evt.type,
                    "name": evt.name,
                    "source": evt.source,
                    "attributes": evt.attributes,
                }
                for evt in decoded
            ]
        except Exception:
            # If decoding fails, keep diagnostics as None; raw XDR is preserved
            diagnostic_events = None

    # Determine terminal / active state
    is_terminal = job.monitoring_status in ("COMPLETED", "OBSERVATION_EXPIRED")
    is_active = job.monitoring_status == "ACTIVE"

    # Format datetime fields
    created_at_dt = job.created_at
    updated_at_dt = job.updated_at

    return TransactionResponse(
        id=str(job.id),
        transaction_hash=job.transaction_hash,
        network=job.network,
        monitoring_status=job.monitoring_status,
        rpc_status=job.rpc_status,
        ledger=job.ledger,
        created_at=_format_datetime(created_at_dt),
        updated_at=_format_datetime(updated_at_dt),
        rpc_status_timestamp=_format_datetime(job.updated_at)
        if job.updated_at
        else None,
        failure_reason=job.failure_reason,
        result_xdr_base64=job.result_xdr,
        envelope_xdr_base64=job.envelope_xdr,
        diagnostic_events=diagnostic_events,
        next_check_at=_format_datetime(job.next_check_at)
        if job.next_check_at
        else None,
        is_terminal=is_terminal,
        is_active=is_active,
    )


@router.get(
    "",
    response_model=TransactionListResponse,
    responses={404: {"description": "No registered transactions found"}},
)
async def list_transactions(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(
        20, ge=1, le=100, description="Items per page (max 100)"
    ),
    monitoring_status: Optional[str] = Query(
        None, description="Filter by monitoring status"
    ),
    rpc_status: Optional[str] = Query(
        None, description="Filter by RPC status"
    ),
) -> TransactionListResponse:
    """List registered monitored transactions with pagination and optional filtering.

    Args:
        page: Page number (1-indexed).
        per_page: Items per page (max 100).
        monitoring_status: Filter by monitoring lifecycle state.
        rpc_status: Filter by RPC getTransaction status.

    Returns:
        Paginated list of monitored transactions.
    """
    async with get_session() as session:
        # Build the base query
        query = select(MonitoringJob)
        count_query = select(func.count()).select_from(MonitoringJob)

        # Apply filters
        if monitoring_status:
            if monitoring_status not in ("ACTIVE", "COMPLETED", "OBSERVATION_EXPIRED"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid monitoring_status: {monitoring_status}",
                )
            query = query.where(MonitoringJob.monitoring_status == monitoring_status)
            count_query = count_query.where(
                MonitoringJob.monitoring_status == monitoring_status
            )

        if rpc_status:
            if rpc_status not in ("SUCCESS", "FAILED", "NOT_FOUND"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid rpc_status: {rpc_status}",
                )
            query = query.where(MonitoringJob.rpc_status == rpc_status)
            count_query = count_query.where(MonitoringJob.rpc_status == rpc_status)

        # Order by created_at descending (newest first)
        query = query.order_by(MonitoringJob.created_at.desc())

        # Apply pagination
        offset = (page - 1) * per_page
        query = query.offset(offset).limit(per_page)

        # Execute count query first
        total_result = await session.execute(count_query)
        total = total_result.scalar_one()

        # Execute main query
        result = await session.execute(query)
        jobs = result.scalars().all()

        items = [
            TransactionListItem(
                id=str(job.id),
                transaction_hash=job.transaction_hash,
                network=job.network,
                monitoring_status=job.monitoring_status,
                rpc_status=job.rpc_status,
                created_at=_format_datetime(job.created_at),
                is_terminal=job.monitoring_status in ("COMPLETED", "OBSERVATION_EXPIRED"),
            )
            for job in jobs
        ]

        total_pages = (total + per_page - 1) // per_page if total > 0 else 0

        return TransactionListResponse(
            items=items,
            total=total,
            page=page,
            per_page=per_page,
            total_pages=total_pages,
        )