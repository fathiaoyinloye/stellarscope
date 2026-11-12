"""PostgreSQL-based job claiming for StellarScope worker coordination.

Provides safe job claiming using ``SELECT ... FOR UPDATE SKIP LOCKED``-style
idioms via UPDATE with lease tracking.  Designed for potentially multiple
worker instances competing for the same set of monitoring jobs.

Key design points
-----------------
- Claim jobs inside a short database transaction; commit before making RPC calls.
- Never hold a database transaction or row lock during an external RPC call.
- Lease-based ownership: a worker may only claim a job whose lease has expired
  (``locked_until < now()``) and whose monitoring status is ``ACTIVE``.
- On claim, update ``locked_by``, ``locked_until``, ``next_check_at``, and
  increment ``attempt_count`` atomically.
- On release, set ``locked_by = NULL`` and retain the observed result.
- Competing workers do not simultaneously claim the same active job.
- Expired leases are recovered automatically; a worker whose lease has not yet
  expired must wait or claim a different job.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from stellarapi.config.settings import settings
from stellarapi.rpc_client import RpcClient, RpcCommunicationError, RpcResponseError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lease constants
# ---------------------------------------------------------------------------

# Default lease timeout if none configured: 3 minutes (matching
# WORKER_LEASE_TIMEOUT_MINUTES).  The settings value takes precedence.
DEFAULT_LEASE_TIMEOUT_SECONDS = 180  # 3 minutes
POLL_INTERVAL_SECONDS = settings.MONITORING_INTERVAL_SECONDS  # 60 by default


# ---------------------------------------------------------------------------
# Job claiming
# ---------------------------------------------------------------------------


async def claim_job(
    engine=None,
    rpc_client: RpcClient | None = None,
    *,
    worker_id: str | None = None,
    lease_timeout_seconds: int | None = None,
) -> dict | None:
    """Claim a single monitoring job for processing.

    The caller receives a dictionary with enough information to process the
    transaction, and is responsible for releasing the lease (setting
    ``locked_by = NULL``) when done.

    The job is claimed only if:
    - The job's ``monitoring_status`` is ``ACTIVE``.
    - The job's ``locked_until`` is in the past (lease expired) or ``NULL``.

    After a successful claim the returned dictionary contains:
    - ``job_id``: the job's UUID
    - ``transaction_hash``: the 64-char hex hash
    - ``network``: the network identifier
    - ``locked_by``: the assigned worker identifier
    - ``locked_until``: the lease expiry timestamp
    - ``next_check_at``: earliest timestamp for the next poll
    - ``attempt_count``: incremented attempt count

    If no job is available for claiming, ``None`` is returned.

    Raises ``RpcCommunicationError`` if the RPC call to determine the job's
    current status would fail (programming error — we only claim after reading
    the row).

    Args:
        engine: SQLAlchemy async engine (or compatible connection).  If not
            passed, the default global engine from ``stellarapi.config.settings``
            is used.  This allows tests to inject their own engine.
        rpc_client: optional ``RpcClient`` instance; if not passed one is
            created using ``settings.SOROBAN_RPC_URL``.
        worker_id: identifier for the claiming worker (e.g. ``"hostname:pid"``).
            Defaults to a generated value based on the current process.
        lease_timeout_seconds: how long (seconds) the lease should last before
            another worker may claim the job.  Defaults to
            ``settings.WORKER_LEASE_TIMEOUT_MINUTES`` * 60.

    Returns:
        ``dict`` with job information, or ``None`` if no job was available.

    Example
    -------
    .. code-block:: python

        job_info = await claim_job(engine, rpc_client, worker_id=f"worker-{pid}")
        if job_info is None:
            # No job available — wait and try again later.
            return None

        # ... process the transaction ...

        # Release the lease when done.
        await release_job(engine, job_info["job_id"])
    """
    if engine is None:
        from stellarapi.database.engine import engine as async_engine
        engine = async_engine

    if worker_id is None:
        import os
        import uuid
        worker_id = f"stellarscope-worker:{os.getpid()}-{uuid.uuid4().hex[:8]}"

    if lease_timeout_seconds is None:
        lease_timeout_seconds = getattr(
            settings, "WORKER_LEASE_TIMEOUT_MINUTES", 3
        ) * 60

    lease_expiry = datetime.now(timezone.utc) + timedelta(seconds=lease_timeout_seconds)
    next_check = datetime.now(timezone.utc) + timedelta(seconds=POLL_INTERVAL_SECONDS)

    async with engine.begin() as conn:
        try:
            # --- Attempt to claim a job ---
            # We use SELECT ... FOR UPDATE SKIP LOCKED to atomically claim an ACTIVE
            # job whose lease has expired and whose next_check_at has arrived.
            # This ensures jobs are polled at the configured interval, not just when
            # the 3-minute lease runs out.
            row = await conn.execute(
                text(
                    """
                    SELECT id,
                           transaction_hash,
                           network,
                           locked_by,
                           locked_until,
                           next_check_at,
                           attempt_count,
                           raw_rpc_response,
                           rpc_status,
                           monitoring_status,
                           fee_bump,
                           envelope_xdr,
                           result_xdr,
                           result_meta_xdr,
                           ledger,
                           failure_reason,
                           created_at
                    FROM monitoring_jobs
                    WHERE monitoring_status = 'ACTIVE'
                      AND (locked_until IS NULL OR locked_until < now())
                      AND next_check_at <= now()
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                ),
                {}
            )

            fetched = row.first()
            if fetched is None:
                # No job matched the claim criteria — another worker may have
                # just claimed it, or all jobs are locked/terminal.
                logger.debug("No job available for claiming (all ACTIVE jobs locked or terminal).")
                return None

            job = dict(fetched._mapping)

            # The row is already locked by the SELECT above; take ownership of
            # it in the same transaction so competing workers skip it until the
            # lease expires (matching the documented lease-based design).
            await conn.execute(
                text(
                    """
                    UPDATE monitoring_jobs
                    SET locked_by = :worker_id,
                        locked_until = :lease_expiry,
                        next_check_at = :next_check,
                        attempt_count = attempt_count + 1
                    WHERE id = :job_id
                    """
                ),
                {
                    "worker_id": worker_id,
                    "lease_expiry": lease_expiry,
                    "next_check": next_check,
                    "job_id": job["id"],
                },
            )

            # Reflect the claim in the returned payload, including the
            # documented ``job_id`` alias expected by the worker.
            job["job_id"] = job["id"]
            job["locked_by"] = worker_id
            job["locked_until"] = lease_expiry
            job["next_check_at"] = next_check
            job["attempt_count"] = (job.get("attempt_count") or 0) + 1

            logger.info(
                "Job claimed: id=%s hash=%s by %s (lease until %s)",
                job["id"],
                job["transaction_hash"],
                worker_id,
                job["locked_until"],
            )
            return job

        except SQLAlchemyError as exc:
            logger.error("Database error while claiming job: %s", exc, exc_info=True)
            raise
# ---------------------------------------------------------------------------
# Job release
# ---------------------------------------------------------------------------

async def release_job(engine, job_id) -> None:
    """Release the worker lease for a job.

    Sets ``locked_by = NULL`` and retains the observed result data.  The
    job remains in ``ACTIVE`` state (or whatever state it was in) so that
    another worker may claim it on the next cycle.

    Args:
        engine: SQLAlchemy async engine (or compatible connection).
        job_id: the database ``id`` of the monitoring job to release.
    """
    async with engine.begin() as conn:
        try:
            await conn.execute(
                text(
                    """
                    UPDATE monitoring_jobs
                    SET locked_by = NULL,
                        locked_until = NULL
                    WHERE id = :job_id
                    """
                ),
                {"job_id": job_id},
            )
            logger.debug("Lease released for job id=%s", job_id)
        except SQLAlchemyError as exc:
            logger.error("Database error while releasing job id=%s: %s", job_id, exc, exc_info=True)
            raise
