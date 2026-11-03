"""Background worker for StellarScope transaction monitoring.

A separately executable asynchronous worker that continuously processes eligible
monitoring records.  The worker is thin — business decisions (RPC calls, XDR
decoding, lifecycle transitions) are delegated to the orchestration service
and jobclaimer.

Key design points
-----------------
- Process records in bounded batches to avoid uncontrolled RPC load.
- Respect the configured polling schedule (``MONITORING_INTERVAL_SECONDS``).
- Support graceful shutdown (``KeyboardInterrupt`` / ``asyncio`` cancellation).
- Avoid uncontrolled concurrency: only one job is "in flight" per worker
  instance at a time, claimed via the jobclaimer.
- Recover jobs whose worker leases have expired (``locked_until < now()``).
- Log meaningful operational information without exposing secrets.
- Handle individual job failures without terminating the entire worker.
- Exit cleanly when no eligible jobs remain and a shutdown has been requested.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from stellarapi.config.settings import settings
from stellarapi.database.engine import engine as async_engine
from stellarapi.monitoring.job_claimer import claim_job, release_job
from stellarapi.monitoring.orchestration import perform_monitoring_check
from stellarapi.rpc_client import RpcClient, RpcCommunicationError, RpcResponseError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Worker configuration (from settings, with defaults)
# ---------------------------------------------------------------------------

DEFAULT_BATCH_SIZE = getattr(settings, "MONITORING_BATCH_SIZE", 5)  # jobs per cycle
SHUTDOWN_TIMEOUT_SECONDS = 15  # graceful shutdown window

# ---------------------------------------------------------------------------
# Worker state
# ---------------------------------------------------------------------------

_shutdown_requested = False


def _setup_signal_handlers() -> None:
    """Install SIGINT / SIGTERM handlers to request graceful shutdown."""

    def _handler(signum: int, frame) -> None:
        global _shutdown_requested
        logger.info("Received signal %s — initiating graceful shutdown", signum)
        _shutdown_requested = True

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


# ---------------------------------------------------------------------------
# Core worker loop
# ---------------------------------------------------------------------------


async def run_worker(
    *,
    rpc_client: RpcClient | None = None,
    batch_size: int | None = None,
) -> None:
    """Run the background monitoring worker.

    The worker runs until a graceful shutdown is requested (SIGINT/SIGTERM).

    Processing flow per cycle:
    1. Claim a single job via ``job_claimer.claim_job``.
    2. If no job is available, wait ``MONITORING_INTERVAL_SECONDS`` and try again.
    3. If a job is claimed, invoke ``orchestration.perform_monitoring_check``.
    4. Release the job lease (``job_claimer.release_job``) regardless of success/failure.
    5. Repeat from step 1.

    The worker respects a graceful shutdown flag; in-flight job processing
    will complete (or be cut short) within ``SHUTDOWN_TIMEOUT_SECONDS``.

    Args:
        rpc_client: optional ``RpcClient`` instance.  If not passed one is
            created using ``settings.SOROBAN_RPC_URL``.
        batch_size: not used in the single-job-per-cycle design; kept for
            API compatibility with potential future batch extensions.
    """
    global _shutdown_requested

    _setup_signal_handlers()

    if rpc_client is None:
        rpc_client = RpcClient()

    if batch_size is None:
        batch_size = DEFAULT_BATCH_SIZE

    logger.info("StellarScope monitoring worker starting (batch_size=%s)", batch_size)
    logger.info("  RPC URL: %s", rpc_client._server_url)
    logger.info("  Poll interval: %s seconds", settings.MONITORING_INTERVAL_SECONDS)
    logger.info("  Lease timeout: %s minutes", getattr(settings, "WORKER_LEASE_TIMEOUT_MINUTES", 3))

    while not _shutdown_requested:
        # --- Step 1: Claim a job ---
        job_info = await claim_job(
            async_engine,
            rpc_client,
            worker_id=f"worker-{asyncio.current_task().get_name() or 'unknown'}",
        )

        if job_info is None:
            # No job available — wait before the next scan cycle.
            # Respect the shutdown flag during the wait.
            wait_until = datetime.now(timezone.utc) + timedelta(
                seconds=settings.MONITORING_INTERVAL_SECONDS
            )
            while _shutdown_requested is False and datetime.now(timezone.utc) < wait_until:
                await asyncio.sleep(0.5)
            continue

        # --- Step 2: Process the job via orchestration ---
        try:
            result = await perform_monitoring_check(
                rpc_client,
                job_id=job_info["job_id"],
                transaction_hash=job_info["transaction_hash"],
                network=job_info["network"],
                raw_rpc_response=job_info.get("raw_rpc_response"),
                envelope_xdr=job_info.get("envelope_xdr"),
                result_xdr=job_info.get("result_xdr"),
                result_meta_xdr=job_info.get("result_meta_xdr"),
                ledger=job_info.get("ledger"),
                fee_bump=job_info.get("fee_bump", False),
                failure_reason=job_info.get("failure_reason"),
            )
            logger.info(
                "Job processed: id=%s hash=%s rpc_status=%s monitoring_status=%s",
                job_info["job_id"],
                job_info["transaction_hash"],
                result["rpc_status"].value if hasattr(result["rpc_status"], "value") else result["rpc_status"],
                result["monitoring_status"],
            )
        except (RpcCommunicationError, RpcResponseError) as exc:
            # RPC-level error — do not mark the job as COMPLETED.
            # Leave it ACTIVE; the worker will retry later.
            # Increment attempt_count was already done by claim_job.
            logger.warning(
                "RPC error processing job id=%s hash=%s: %s",
                job_info["job_id"],
                job_info["transaction_hash"],
                exc,
            )
        except Exception as exc:
            # Unexpected error — log and continue. We do NOT set the
            # job to COMPLETED so it can be retried.
            logger.exception(
                "Unexpected error processing job id=%s hash=%s: %s",
                job_info["job_id"],
                job_info["transaction_hash"],
                exc,
            )

        # --- Step 3: Release the lease ---
        # Always release the lease when done, regardless of outcome.
        # The DB row's locked_by is set to NULL; monitoring_status
        # remains ACTIVE (or was transitioned to COMPLETED by the
        # orchestration service).
        await release_job(async_engine, job_info["job_id"])
        logger.debug("Lease released for job id=%s", job_info["job_id"])

    # --- Graceful shutdown ---
    logger.info("Shutdown requested — finishing current cycle then exiting.")
    logger.info("StellarScope monitoring worker stopped.")


# ---------------------------------------------------------------------------
# Module entry point (for ``python -m stellarapi.monitoring.worker``)
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the background worker.

    Creates the RPC client, sets up signal handlers, and runs the worker
    until a graceful shutdown is requested.
    """
    _setup_signal_handlers()
    rpc_client = RpcClient()
    asyncio.run(run_worker(rpc_client=rpc_client))


if __name__ == "__main__":
    main()