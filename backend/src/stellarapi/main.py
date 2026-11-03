"""StellarScope FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from stellarapi.api.health import router as health_router
from stellarapi.api.transactions import router as transactions_router
from stellarapi.api.discovery import router as discovery_router
from stellarapi.api.health_monitor import router as health_monitor_router

app = FastAPI(
    title="StellarScope",
    description="Python backend service for monitoring Stellar/Soroban transactions",
    version="0.1.0",
)

# Configure CORS for separate frontend and backend origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
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