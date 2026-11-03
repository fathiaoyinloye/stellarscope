"""StellarScope health check endpoint.

Provides a lightweight ``GET /health`` that confirms the application process
is running and the FastAPI framework is operational.  Deliberately does **not**
perform database or Stellar RPC connectivity checks at this layer so that the
application can start and be observed independently of external service
availability (per the approved constraints).

Responses
---------
- ``200 OK`` — application is up and the framework is operational.
- ``500 Internal Server Error`` — unexpected configuration or initialization error.
"""
from fastapi import APIRouter, status

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    summary="Health check",
    description="Return basic application liveness information. "
    "Does not verify database or Stellar RPC connectivity.",
    status_code=status.HTTP_200_OK,
)
async def health_check() -> dict[str, str]:
    """Return that the application is running."""
    return {
        "status": "ok",
        "service": "stellarscope",
        "version": "0.1.0",
        "environment": "development",
    }