"""StellarScope application configuration via environment variables.

Uses pydantic-settings for environment variable loading with .env file support.

Environment variables (via .env file or process environment):

STELLAR_SCOPE_DATABASE_URL         postgresql+asyncpg://user:pass@host:port/db
STELLAR_SCOPE_ENV                  development|test|production
STELLAR_SCOPE_LOG_LEVEL            INFO|WARNING|ERROR|DEBUG
STELLAR_SCOPE_API_V1_STR           /api/v1
STELLAR_SCOPE_MONITORING_INTERVAL_SECONDS 60
STELLAR_SCOPE_WORKER_LEASE_TIMEOUT_MINUTES 3
STELLAR_SCOPE_OBSERVATION_DEADLINE_MINUTES 60
STELLAR_SCOPE_SOROBAN_RPC_URL      https://soroban-testnet.stellar.org
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/stellarscope"

    # Environment
    ENV: str = "development"

    # Logging
    LOG_LEVEL: str = "INFO"

    # API
    API_V1_STR: str = "/api/v1"

    # Monitoring
    MONITORING_INTERVAL_SECONDS: int = 60

    # Worker lease timeout (minutes)
    WORKER_LEASE_TIMEOUT_MINUTES: int = 3

    # Observation deadline (minutes)
    OBSERVATION_DEADLINE_MINUTES: int = 60

    # RPC timeout (seconds)
    RPC_TIMEOUT_SECONDS: int = 30

    # Soroban RPC URL
    SOROBAN_RPC_URL: str = "https://soroban-testnet.stellar.org"

    # Mainnet RPC URL (optional, defaults to testnet)
    MAINNET_RPC_URL: str = "https://horizon.multi pool.stellar.org"


# Singleton instance; created at module import time so other packages can
# `from stellarapi.config.settings import settings` without circular risks.
settings = Settings()  # type: ignore[assignment]