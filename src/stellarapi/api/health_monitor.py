"""RPC health monitoring API endpoints for StellarScope Phase 10.

Provides endpoints for:
- Checking Soroban RPC server health status.
- Exposing retention window and ledger information.
"""

from fastapi import APIRouter, HTTPException

from src.stellarapi.rpc_client import RpcClient, RpcCommunicationError

router = APIRouter(prefix="/api/v1", tags=["health-monitor"])


@router.get("/rpc/health", summary="Check Soroban RPC health status")
def rpc_health_check() -> dict:
    """Check the health of the configured Soroban RPC endpoint.

    Returns information about RPC availability, ledger status, and retention window.
    This endpoint does not poll getTransaction; it only checks the RPC service
    connectivity and health metadata.

    Returns
    -------
    dict with keys:
        - status: "healthy" | "degraded" | "unavailable"
        - latest_ledger: latest known ledger number
        - oldest_ledger: oldest ledger still retained
        - ledger_retention_window: retention window in seconds
        - rpc_status: always "healthy" when this endpoint succeeds

    Raises
    ------
    HTTPException
        - 503 if the RPC health check fails (service unavailable).
        - 500 if an unexpected error occurs.
    """
    rpc_client = RpcClient()
    # Access the server property to lazily initialize the SorobanServer
    _ = rpc_client.server
    try:
        health = rpc_client.get_health()
        return health
    except RpcCommunicationError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"RPC health check failed: {exc}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error during RPC health check: {exc}",
        )
