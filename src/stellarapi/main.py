"""StellarScope FastAPI application entry point."""

from fastapi import FastAPI

from src.stellarapi.api.health import router as health_router

app = FastAPI(
    title="StellarScope",
    description="Python backend service for monitoring Stellar/Soroban transactions",
    version="0.1.0",
)

app.include_router(health_router)


@app.get("/", include_in_schema=False)
async def root():
    """Root endpoint returning service info."""
    return {
        "message": "StellarScope API",
        "version": "0.1.0",
        "docs": "/docs",
    }