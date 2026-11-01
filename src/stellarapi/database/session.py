"""Database session wrapper for dependency injection.

Provides a callable that yields an :class:`~sqlalchemy.ext.asyncio.AsyncSession`
from the pre-configured ``session_factory``. Intended for use with FastAPI's
:func:`~fastapi.Depends` to inject a session per request or per worker cycle.

Usage (FastAPI dependency):

```python
from fastapi import Depends

async def get_db_session() -> AsyncSession:
    async with get_db_session_dependency() as session:
        yield session  # FastAPI injects this as the ``db`` parameter
```

or as a direct context manager:

```python
async with get_session() as session:
    result = await session.execute(select(...))
```
"""
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from src.stellarapi.database.engine import session_factory


@asynccontextmanager
async def get_session() -> AsyncSession:
    """Yield a single :class:`~sqlalchemy.ext.asyncio.AsyncSession`.

    The session is rolled back (rather than committed) if an exception escapes
    the ``async with`` block, keeping the transaction boundary explicit.

    Yields:
        An :class:`~sqlalchemy.ext.asyncio.AsyncSession` instance.
    """
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()