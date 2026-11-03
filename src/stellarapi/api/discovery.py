"""Contract discovery API endpoints for StellarScope Phase 9.

Provides endpoints for:
- Registering a Soroban contract for transaction discovery.
- Retrieving discovery status and progress.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pydantic import BaseModel, Field, validator

from sqlalchemy import select

from src.stellarapi.database.models import ContractDiscovery
from src.stellarapi.database.session import get_session


class ContractRegisterRequest(BaseModel):
    """Request body for registering a contract for discovery."""

    contract_address: str = Field(
        ...,
        description="64-character hex-encoded Soroban contract address",
    )
    network: str = Field(
        "testnet",
        description="Stellar network identifier (testnet | mainnet)",
    )

    @validator("contract_address")
    def validate_contract_address(cls, v: str) -> str:
        """Validate the contract address format."""
        if not v or len(v) != 64:
            raise ValueError("contract_address must be a 64-character hex string")
        return v.lower()

    @validator("network")
    def validate_network(cls, v: str) -> str:
        """Validate the network field."""
        if v not in ("testnet", "mainnet"):
            raise ValueError(f"Unsupported network '{v}'. Supported: testnet, mainnet")
        return v


router = APIRouter(prefix="/v1/contracts/discovery", tags=["contract-discovery"])


@router.post("/register", status_code=201)
async def register_contract_discovery(request: ContractRegisterRequest) -> dict:
    """Register a Soroban contract for transaction discovery.

    Parameters
    ----------
    request:
        ContractRegisterRequest containing contract_address and network.

    Returns
    -------
    dict
        Contract discovery job identifier and current status.

    Raises
    ------
    HTTPException
        - 400 if the contract address is not exactly 64 hex characters.
        - 400 if the network is not supported.
        - 409 if a discovery job already exists for this contract/Network combo.
    """
    async with get_session() as session:
        # Check for existing discovery job (unique constraint prevention)
        result = await session.execute(
            select(ContractDiscovery).where(
                ContractDiscovery.contract_address == request.contract_address,
                ContractDiscovery.network == request.network,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Contract discovery already registered for "
                f"contract_address={request.contract_address!r} on network={request.network!r}",
            )

        # Create new discovery job
        new_job = ContractDiscovery(
            contract_address=request.contract_address,
            network=request.network,
            status="PENDING",
        )

        session.add(new_job)
        await session.commit()
        await session.refresh(new_job)

        return {
            "id": str(new_job.id),
            "contract_address": new_job.contract_address,
            "network": new_job.network,
            "status": new_job.status,
            "discovered_count": new_job.discovered_count,
            "created_at": new_job.created_at,
        }


@router.get("/{contract_address}")
async def get_discovery_status(contract_address: str, network: str = "testnet") -> dict:
    """Retrieve the discovery status and progress for a contract.

    Parameters
    ----------
    contract_address:
        64-character hex-encoded Soroban contract address.
    network:
        Stellar network identifier.  Defaults to ``testnet``.

    Returns
    -------
    dict
        Discovery job status and progress information.

    Raises
    ------
    HTTPException
        - 404 if no discovery job exists for the given contract/Network combo.
    """
    async with get_session() as session:
        result = await session.execute(
            select(ContractDiscovery).where(
                ContractDiscovery.contract_address == contract_address,
                ContractDiscovery.network == network,
            )
        )
        job = result.scalar_one_or_none()

        if job is None:
            raise HTTPException(
                status_code=404,
                detail=f"No contract discovery job found for "
                f"contract_address={contract_address!r} on network={network!r}",
            )

        return {
            "contract_address": job.contract_address,
            "network": job.network,
            "status": job.status,
            "discovered_count": job.discovered_count,
            "last_ledger": job.last_ledger,
            "last_successful_ledger": job.last_successful_ledger,
            "cursor": job.cursor,
            "error_message": job.error_message,
            "error_count": job.error_count,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }
