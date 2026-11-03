"""Asynchronous SQLAlchemy engine configuration.

Configures a single ``AsyncEngine`` using ``asyncpg`` for PostgreSQL,
with settings appropriate for a long-running FastAPI application and
a background worker that uses ``SELECT ... FOR UPDATE SKIP LOCKED``.

Key settings:
- ``future=True`` — enables the 2.0 style API (required for SQLAlchemy 2.x).
- ``echo=False`` — SQL logging can be enabled via ``LOG_LEVEL=DEBUG`` and
  watching the ``sqlalchemy.engine`` logger.
- ``pool_size`` / ``max_overflow`` — tuned for a single app process + worker.
- ``max_overflow`` allows brief spikes without immediately exhausting connections.
- ``pool_timeout`` ensures workers don't hang indefinitely when all slots are in use.
"""
from sqlalchemy import Engine, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_engine_from_config,
    async_sessionmaker,
    create_async_engine,
)

from stellarapi.config.settings import settings

#: Pre-configured :class:`~sqlalchemy.ext.asyncio.AsyncEngine`.
engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.LOG_LEVEL == "DEBUG",
    future=True,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=3600,
)

#: Factory for :class:`~sqlalchemy.ext.asyncio.AsyncSession`.
#: - ``class_=AsyncSession`` — the async session factory.
#: - ``expire_on_commit=True`` (default) — attributes are expired after commit,
#   requiring refresh access if needed after a transaction boundary.
#: - ``autoflush_before_commit=True`` — ensures consistent reads within a request.
session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=True,
    autoflush_before_commit=True,
)


def get_session() -> async_sessionmaker:
    """Return the configured :class:`~sqlalchemy.ext.asyncio.async_sessionmaker`."""
    return session_factory