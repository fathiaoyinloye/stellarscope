"""StellarScope FastAPI application entry point."""

from fastapi import FastAPI

from src.stellarapi.api.health import router as health_router
from src.stellarapi.api.transactions import router as transactions_router
from src.stellarapi.api.discovery import router as discovery_router
from src.stellarapi.api.health_monitor import router as health_monitor_router

app = FastAPI(
    title="StellarScope",
    description="Python backend service for monitoring Stellar/Soroban transactions",
    version="0.1.0",
)

app.include_router(health_router)

from fastapi.staticfiles import StaticFiles
app.mount("/dashboard", StaticFiles(directory="templates", html=True))
app.include_router(health_monitor_router)
app.include_router(discovery_router)
app.include_router(transactions_router)


@app.get("/", include_in_schema=False)
async def root():
    """Root endpoint returning service info."""
    return {
        "message": "StellarScope API",
        "version": "0.1.0",
        "docs": "/docs",
    }