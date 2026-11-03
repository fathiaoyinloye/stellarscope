"""Database access layer for StellarScope monitoring_jobs table.

Provides asynchronous repository functions for the MonitoringJob model.
Kept separate from API and worker logic as per the approved architecture.

All functions use async SQLAlchemy sessions and operate on the
``monitoring_jobs`` table defined in ``src.stellarapi.database.models``.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from stellarapi.database.models import MonitoringJob


async def create_job(session, *, transaction_hash, network="testnet", **kwargs):
    """Create a new monitoring job.

    Args:
        session: AsyncSession instance.
        transaction_hash: 64-char hex Stellar transaction hash.
        network: Stellar network (default: "testnet").
        **kwargs: Additional MonitoringJob fields (rpc_status, monitoring_status,
            raw_rpc_response, etc.).

    Returns:
        The created MonitoringJob instance.

    Raises:
        IntegrityError: If transaction_hash already exists (unique constraint).
    """
    job = MonitoringJob(
        transaction_hash=transaction_hash,
        network=network,
        **kwargs,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


async def get_job_by_hash(session, transaction_hash):
    """Get a monitoring job by transaction hash."""
    result = await session.execute(
        select(MonitoringJob).where(MonitoringJob.transaction_hash == transaction_hash)
    )
    return result.scalar_one_or_none()


async def get_active_jobs(session):
    """Get all ACTIVE monitoring jobs, ordered by creation time."""
    result = await session.execute(
        select(MonitoringJob)
        .where(MonitoringJob.monitoring_status == "ACTIVE")
        .order_by(MonitoringJob.created_at)
    )
    return result.scalars().all()


async def get_pending_jobs(session):
    """Get all pending monitoring jobs for worker claiming."""
    result = await session.execute(
        select(MonitoringJob)
        .where(MonitoringJob.monitoring_status == "ACTIVE")
        # Note: worker uses FOR UPDATE SKIP LOCKED at the query level,
        # not a WHERE clause filter here. This just returns ACTIVE jobs.
    )
    return result.scalars().all()


async def get_jobs_by_rpc_status(session, rpc_status):
    """Get all jobs with a specific RPC status."""
    result = await session.execute(
        select(MonitoringJob).where(MonitoringJob.rpc_status == rpc_status)
    )
    return result.scalars().all()


async def get_completed_jobs(session):
    """Get all COMPLETED monitoring jobs."""
    result = await session.execute(
        select(MonitoringJob).where(MonitoringJob.monitoring_status == "COMPLETED")
    )
    return result.scalars().all()


async def get_observation_expired_jobs(session):
    """Get all OBSERVATION_EXPIRED monitoring jobs."""
    result = await session.execute(
        select(MonitoringJob).where(MonitoringJob.monitoring_status == "OBSERVATION_EXPIRED")
    )
    return result.scalars().all()


async def update_job_status(session, transaction_hash, monitoring_status, rpc_status=None):
    """Update a job's monitoring and/or RPC status."""
    job = await get_job_by_hash(session, transaction_hash)
    if job is None:
        return None

    if monitoring_status is not None:
        job.monitoring_status = monitoring_status
    if rpc_status is not None:
        job.rpc_status = rpc_status

    await session.commit()
    await session.refresh(job)
    return job


async def update_job_lock(session, transaction_hash, locked_by, locked_until, next_check_at, increment_attempts=True):
    """Update job lock fields for worker coordination.

    Args:
        session: AsyncSession instance.
        transaction_hash: The job's transaction hash.
        locked_by: Worker identifier (e.g. "hostname:pid").
        locked_until: Lease expiry TIMESTAMPTZ.
        next_check_at: Earliest next poll TIMESTAMPTZ.
        increment_attempts: Whether to increment the attempt counter.

    Returns:
        The updated MonitoringJob instance, or None if not found.
    """
    job = await get_job_by_hash(session, transaction_hash)
    if job is None:
        return None

    job.locked_by = locked_by
    job.locked_until = locked_until
    job.next_check_at = next_check_at

    if increment_attempts:
        job.attempt_count += 1

    await session.commit()
    await session.refresh(job)
    return job


async def release_job_lock(session, transaction_hash):
    """Release a job lock by clearing locked_by and locked_until."""
    job = await get_job_by_hash(session, transaction_hash)
    if job is None:
        return None

    job.locked_by = None
    job.locked_until = None
    # Optionally reset next_check_at or leave it; here we leave it

    await session.commit()
    await session.refresh(job)
    return job


async def increment_attempt_count(session, transaction_hash):
    """Increment the attempt counter for a job."""
    job = await get_job_by_hash(session, transaction_hash)
    if job is None:
        return None

    job.attempt_count += 1
    await session.commit()
    await session.refresh(job)
    return job


async def delete_job(session, transaction_hash):
    """Delete a monitoring job by transaction hash."""
    job = await get_job_by_hash(session, transaction_hash)
    if job is None:
        return False

    await session.delete(job)
    await session.commit()
    return True