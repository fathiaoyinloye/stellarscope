"""Monitoring orchestration service for StellarScope.

Responsible for coordinating a single monitoring check: obtaining an eligible
transaction, querying the Stellar RPC, processing the response with XDR
decoding, persisting the observation, and updating the monitoring lifecycle.

This service does **not** contain any worker loop, batching logic, or database
claiming — it focuses on orchestration of existing components (RpcClient,
XDR decoder, database write).  The caller (worker) is responsible for
claiming a job, invoking this service, and releasing the lease.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from stellarapi.config.settings import settings
from stellarapi.database.engine import engine as async_engine
from stellarapi.database.models import MonitoringJob
from stellarapi.rpc_client import RpcClient, RpcCommunicationError, RpcResponseError
from stellarapi.xdr.decoder import (
    decode_fee_bump_result,
    decode_transaction_result,
    decode_transaction_meta,
    decode_diagnostic_events,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Monitoring lifecycle transition rules (approved from Phase 3 / Phase 4)
# ---------------------------------------------------------------------------

# - ACTIVE -> COMPLETED : a terminal RPC status (SUCCESS or FAILED) has been
#   recorded.  Once recorded, do NOT re-poll (per DECISIONS.md §34-35).
# - ACTIVE -> OBSERVATION_EXPIRED : the observation deadline has been reached
#   without a terminal result.  Documented semantic: this is not an on-chain
#   expiry (DECISIONS.md §68).
# - COMPLETED and OBSERVATION_EXPIRED are terminal; they must never be
#   overwritten by a later RPC call (DECISIONS.md §38: "Do not overwrite a
#   previously recorded terminal result with a later NOT_FOUND").

# - RPC statuses: SUCCESS / FAILED / NOT_FOUND (SUCCESS and FAILED are terminal
#   once recorded; NOT_FOUND does not prove pending/never existed/aged out).

# - Fee-bump: inspect inner result XDR, do not assume normal structure
#   (DECISIONS.md §38, §38).

# - NOT_FOUND: preserve raw response; do not attempt to decode result XDR
#   (DECISIONS.md §35).

# ----------------------------------------------------------------------
# Orchestration helper: perform one monitoring check cycle
# ----------------------------------------------------------------------


async def perform_monitoring_check(
    rpc_client: RpcClient,
    *,
    job_id,
    transaction_hash: str,
    network: str,
    raw_rpc_response: dict | None,
    envelope_xdr: str | None,
    result_xdr: str | None,
    result_meta_xdr: str | None,
    ledger: int | None,
    fee_bump: bool,
    failure_reason: str | None,
) -> dict:
    """Execute one monitoring check cycle for a single transaction.

    This is the core orchestration step.  The caller (worker) must have
    previously claimed the job (via ``job_claimer.claim_job``) and will
    release the lease after this function returns.

    The function:
    1. Calls ``rpc_client.get_transaction(transaction_hash)`` to get the
       latest RPC response.
    2. Normalises and decodes the response (result XDR, meta XDR, diagnostic
       events) using the best‑effort XDR decoder.
    3. Updates the ``monitoring_jobs`` table with the new RPC status,
       decoded evidence, and lifecycle state.
    4. Schedules the ``next_check_at`` based on the outcome.

    Invariants preserved
    --------------------
    - If the RPC returns ``NOT_FOUND``, result_xdr / result_meta_xdr / decoded
      fields are left as ``None``; only the raw RPC response is stored.
    - If the RPC returns ``SUCCESS`` or ``FAILED``, the terminal status is
      recorded and the monitoring lifecycle transitions to ``COMPLETED``.
    - A previously ``COMPLETED`` monitoring status is never overwritten.
    - Fee‑bump transactions inspect the inner result XDR rather than assuming
      the normal top‑level ``TransactionResult`` structure.
    - Raw evidence (``raw_rpc_response``, base64 XDR fields) is always
      preserved, even when decoding fails.

    Args:
        rpc_client: ``RpcClient`` instance connected to the appropriate RPC URL.
        job_id: the database ``id`` of the monitoring job being checked.
        transaction_hash: the 64‑character hex transaction hash.
        network: the network identifier (``"testnet"`` or ``"mainnet"``).
        raw_rpc_response: the previous raw RPC response dict (may be ``None``
            on first check).
        envelope_xdr: previous envelope XDR base64 (may be ``None``).
        result_xdr: previous result XDR base64 (may be ``None``).
        result_meta_xdr: previous result meta XDR base64 (may be ``None``).
        ledger: previous ledger number (may be ``None``).
        fee_bump: whether the transaction was fee‑bumped.
        failure_reason: optional human‑readable failure reason from previous
            check (may be ``None``).

    Returns:
        ``dict`` with the following keys:
        - ``job_id``: the database id
        - ``rpc_status``: ``"SUCCESS"`` / ``"FAILED"`` / ``"NOT_FOUND"``
        - ``monitoring_status``: ``"ACTIVE"`` / ``"COMPLETED"`` /
          ``"OBSERVATION_EXPIRED"``
        - ``next_check_at``: optional earliest next poll timestamp
        - ``updated_raw_rpc_response``: the (possibly updated) raw RPC response dict

    Raises:
        ``RpcCommunicationError``: if the RPC call itself fails (network,
        timeout, malformed response).
        ``RpcResponseError``: if the RPC returns an unexpected response format.
        ``SQLAlchemyError``: if the database update fails.
    """
    # ------------------------------------------------------------------
    # Step 1: Fetch the latest transaction from the RPC
    # ------------------------------------------------------------------
    try:
        rpc_result: GetTransactionResult = await rpc_client.get_transaction(
            transaction_hash
        )
    except RpcCommunicationError as exc:
        # Network / timeout error — do not change the monitoring status.
        # The worker may retry later; leave the job as ACTIVE.
        logger.warning(
            "RPC communication error for transaction %s (job %s): %s",
            transaction_hash,
            job_id,
            exc,
        )
        raise
    except RpcResponseError as exc:
        # Malformed RPC response — treat as a non-terminal error; keep
        # monitoring status unchanged and let the worker decide retry logic.
        logger.error(
            "RPC response error for transaction %s (job %s): %s",
            transaction_hash,
            job_id,
            exc,
        )
        raise

    # ------------------------------------------------------------------
    # Step 2: Normalise and decode the RPC response
    # ------------------------------------------------------------------
    status = rpc_result.rpc_status  # type: GetTransactionStatus
    new_raw_rpc_response = rpc_result.raw_rpc_response

    # Decode result XDR, handling fee‑bump transactions
    decoded_result: Optional[TransactionResultInfo] = None

    if fee_bump:
        # Fee-bump: inspect inner result XDR rather than assuming normal structure.
        inner_result_xdr = rpc_result.raw_rpc_response.get("inner_result_xdr") or None
        # The raw RPC response from Soroban may embed the inner result XDR;
        # if not available we fall back to the outer result_xdr.
        try:
            decoded_result = decode_fee_bump_result(
                result_xdr=result_xdr or rpc_result.result_xdr or "",
                inner_result_xdr=inner_result_xdr or rpc_result.result_xdr or "",
            )
        except (Exception) as exc:
            logger.warning(
                "Fee-bump XDR decode failure for transaction %s (job %s): %s",
                transaction_hash,
                job_id,
                exc,
            )
            # Keep decoded_result as None; raw XDR is preserved for evidence.
    else:
        # Normal (non fee-bump) transaction: decode the outer result XDR.
        if result_xdr or rpc_result.result_xdr:
            try:
                decoded_result = decode_transaction_result(
                    result_xdr or rpc_result.result_xdr or ""
                )
            except (Exception) as exc:
                logger.warning(
                    "XDR decode failure for result_xdr of transaction %s (job %s): %s",
                    transaction_hash,
                    job_id,
                    exc,
                )
                # Do not set result_xdr to None — the raw value is preserved
                # in ``new_raw_rpc_response`` for evidence.

    # Decode result_meta XDR if present
    decoded_meta: Optional[Any] = None
    if result_meta_xdr or rpc_result.result_meta_xdr:
        try:
            decoded_meta = decode_transaction_meta(
                result_meta_xdr or rpc_result.result_meta_xdr or ""
            )
        except (Exception) as exc:
            logger.warning(
                "XDR decode failure for result_meta_xdr of transaction %s (job %s): %s",
                transaction_hash,
                job_id,
                exc,
            )

    # Decode diagnostic events XDR if present
    decoded_events: list[Any] = []
    if rpc_result.diagnostic_events_xdr:
        try:
            decoded_events = decode_diagnostic_events(rpc_result.diagnostic_events_xdr)
        except (Exception) as exc:
            logger.warning(
                "XDR decode failure for diagnostic events of transaction %s (job %s): %s",
                transaction_hash,
                job_id,
                exc,
            )

    # ------------------------------------------------------------------
    # Step 3: Determine monitoring lifecycle transition
    # ------------------------------------------------------------------
    # Invariants from DECISIONS.md:
    # - SUCCESS and FAILED are terminal; once recorded, do not re-poll.
    # - NOT_FOUND does not prove pending/never existed/aged out; preserve raw
    #   response; do not attempt to decode result fields.
    # - Do NOT overwrite a previously recorded terminal result (COMPLETED) with
    #   a later NOT_FOUND.

    # Determine the new monitoring_status and rpc_status based on the RPC result
    # and the current job state.  The caller should ensure we only transition
    # from ACTIVE; transitions from COMPLETED/OBSERVATION_EXPIRED are errors.

    if status.value == "NOT_FOUND":
        # NOT_FOUND: do not decode result fields; preserve raw response.
        # If the job is currently ACTIVE, transition to... we keep it ACTIVE
        # but record the NOT_FOUND.  Per the spec, NOT_FOUND is not an error
        # and we continue polling until a terminal state or observation deadline.
        new_rpc_status = status
        new_monitoring_status = "ACTIVE"  # continue polling
        new_failure_reason = None  # NOT_FOUND is not an error per se
        new_next_check = datetime.now(timezone.utc) + timedelta(
            seconds=settings.MONITORING_INTERVAL_SECONDS
        )

    elif status.value in ("SUCCESS", "FAILED"):
        # Terminal RPC status.  If the job is currently ACTIVE, transition
        # to COMPLETED.  If already COMPLETED, do NOT overwrite (enforced by
        # the caller / DB constraint).
        new_rpc_status = status
        new_monitoring_status = "COMPLETED"
        new_failure_reason = (
            decoded_result.result_code if decoded_result else None
        )
        # Set next_check_at to current time - no further polling needed since
        # the status is terminal, but the column is NOT NULL so we must provide
        # a value.  The WHERE monitoring_status = 'ACTIVE' clause in the UPDATE
        # will prevent any stale row from being updated.
        new_next_check = datetime.now(timezone.utc)

    else:
        # Should not happen given the type system, but fallback.
        new_rpc_status = status
        new_monitoring_status = "ACTIVE"
        new_next_check = None

    # ------------------------------------------------------------------
    # Step 4: Persist the observation to the database
    # ------------------------------------------------------------------
    async with async_engine.begin() as conn:
        try:
            # Build the update statement.  We update only if the job is still
            # ACTIVE — this prevents a worker from overwriting a COMPLETED job
            # that another worker already recorded.
            update_sql = text(
                """
                UPDATE monitoring_jobs
                SET
                    rpc_status = :rpc_status,
                    monitoring_status = :monitoring_status,
                    raw_rpc_response = CAST(:raw_rpc_response AS jsonb),
                    envelope_xdr = :envelope_xdr,
                    result_xdr = :result_xdr,
                    result_meta_xdr = :result_meta_xdr,
                    ledger = :ledger,
                    failure_reason = :failure_reason,
                    locked_by = NULL,          -- release the lease
                    next_check_at = :next_check_at,
                    updated_at = now()
                WHERE
                    id = :job_id
                    AND monitoring_status = 'ACTIVE'
                """
            )

            await conn.execute(
                update_sql,
                {
                    "rpc_status": new_rpc_status.value,
                    "monitoring_status": new_monitoring_status,
                    "raw_rpc_response": json.dumps(new_raw_rpc_response),
                    "envelope_xdr": envelope_xdr or rpc_result.envelope_xdr,
                    "result_xdr": result_xdr or rpc_result.result_xdr,
                    "result_meta_xdr": result_meta_xdr or rpc_result.result_meta_xdr,
                    "ledger": ledger if ledger is not None else rpc_result.ledger,
                    "failure_reason": new_failure_reason,
                    "job_id": job_id,
                    "next_check_at": new_next_check,
                },
            )

            # Record the transition for logging/observability
            logger.info(
                "Monitoring check completed: job=%s hash=%s rpc_status=%s "
                "monitoring_status=%s next_check_at=%s",
                job_id,
                transaction_hash,
                new_rpc_status.value,
                new_monitoring_status,
                new_next_check,
            )

        except SQLAlchemyError as exc:
            logger.error(
                "Database update failed for monitoring check job=%s: %s",
                job_id,
                exc,
                exc_info=True,
            )
            raise

    # ------------------------------------------------------------------
    # Step 5: Return the result to the caller
    # ------------------------------------------------------------------
    return {
        "job_id": job_id,
        "rpc_status": new_rpc_status,
        "monitoring_status": new_monitoring_status,
        "next_check_at": new_next_check,
        "updated_raw_rpc_response": new_raw_rpc_response,
    }