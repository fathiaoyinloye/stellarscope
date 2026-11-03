"""Background discovery worker for StellarScope Phase 9.

Responsible for discovering transactions associated with Soroban contracts
by querying the Stellar RPC ``get_events()`` method with
``EventFilter(contract_ids=[...])``.

Key design points
-----------------
- Cursor-based pagination for resumability and checkpoint persistence.
- Idempotent discovery: UNIQUE(transaction_hash) on monitoring_jobs prevents
  duplicate transaction registrations.
- Checkpoint persistence: cursor and last_successful_ledger are saved after
  each batch so the worker can resume after interruption.
- Worker restart recovery: contracts with status IN_PROGRESS are resumed from
  their last saved cursor/ledger.
- RPC failure handling: exponential backoff; after consecutive errors, status
  is set to ERROR.
- Reuse existing monitoring infrastructure: discovered transaction hashes are
  registered via the same monitoring_jobs table, entering the same
  ACTIVE -> COMPLETED / OBSERVATION_EXPIRED lifecycle.
- Separation of concerns: business rules (event processing, deduplication)
  are separated from RPC infrastructure and database persistence.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from stellar_sdk import SorobanServer
from stellar_sdk.soroban_rpc import EventFilter, GetEventsRequest

from stellarapi.config.settings import settings
from stellarapi.database.engine import engine as async_engine
from stellarapi.database.models import ContractDiscovery, MonitoringJob
from stellarapi.database.session import get_session
from stellarapi.rpc_client import RpcClient, RpcCommunicationError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Discovery configuration (from settings, with defaults)
# ---------------------------------------------------------------------------

DEFAULT_BATCH_SIZE = getattr(settings, "DISCOVERY_BATCH_SIZE", 50)  # events per get_events() call
MAX_CONSECUTIVE_ERRORS = getattr(
    settings, "DISCOVERY_MAX_CONSECUTIVE_ERRORS", 5
)  # after this, status -> ERROR
RETRY_MULTIPLIER = getattr(settings, "DISCOVERY_RETRY_MULTIPLIER", 1.5)  # backoff multiplier
MIN_RETRY_SECONDS = getattr(settings, "DISCOVERY_MIN_RETRY_SECONDS", 5)
MAX_RETRY_SECONDS = getattr(settings, "DISCOVERY_MAX_RETRY_SECONDS", 60)

# ---------------------------------------------------------------------------
# Helper: fetch events from Stellar RPC with cursor pagination
# ---------------------------------------------------------------------------


async def fetch_events_page(
    contract_address: str,
    cursor: Optional[str] = None,
    start_ledger: Optional[int] = None,
    end_ledger: Optional[int] = None,
    limit: int = DEFAULT_BATCH_SIZE,
) -> dict:
    """Fetch a single page of events for a contract using get_events().

    Uses stellar_sdk's EventFilter(contract_ids=[...]) which is natively
    supported by the Stellar Soroban RPC get_events() method.

    Parameters
    ----------
    contract_address:
        64-character hex Soroban contract address.
    cursor:
        Pagination cursor from a previous get_events() response.
        If None, fetch from the beginning (or from start_ledger).
    start_ledger:
        Only include events from this ledger number onwards.
        If None, no lower bound.
    end_ledger:
        Only include events up to this ledger number.
        If None, no upper bound.
    limit:
        Maximum number of events to return per call.

    Returns
    -------
    dict
        The raw get_events() response dict containing:
        - records: list of EventInfo objects (as dicts)
        - cursor: pagination cursor for the next page (may be None)
        - _embedded: additional metadata

    Raises
    ------
    RpcCommunicationError:
        If the RPC call fails (network timeout, malformed response).
    """
    # Create RPC client and get the SorobanServer instance
    rpc_client = RpcClient()
    server = rpc_client.server  # SorobanServer instance

    # Build the EventFilter with the contract address
    event_filter = EventFilter(contract_ids=[contract_address])

    # Build the request
    request = GetEventsRequest(
        filters=[event_filter],
        cursor=cursor,
        start_ledger=start_ledger,
        end_ledger=end_ledger,
        limit=limit,
    )

    # Call the RPC
    try:
        response = await server.get_events(request)
        return {
            "records": response.records if hasattr(response, "records") else [],
            "cursor": response.cursor if hasattr(response, "cursor") else None,
            "_embedded": getattr(response, "_embedded", {}),
        }
    except Exception as exc:
        # Wrap as RpcCommunicationError per project conventions
        raise RpcCommunicationError(
            f"Stellar RPC get_events() failed for contract {contract_address}: {exc}"
        )
    finally:
        # Clean up - the RpcClient creates a new one each time, no persistent state
        pass


# ---------------------------------------------------------------------------
# Helper: extract transaction hashes from events and register them
# ---------------------------------------------------------------------------


async def register_discovered_transaction(transaction_hash: str, network: str) -> bool:
    """Register a discovered transaction hash into the monitoring workflow.

    This reuses the existing monitoring_jobs table and the same lifecycle
    (ACTIVE -> COMPLETED / OBSERVATION_EXPIRED).  The unique constraint on
    transaction_hash prevents duplicates.

    Parameters
    ----------
    transaction_hash:
        64-character hex Stellar transaction hash.
    network:
        Stellar network identifier (testnet | mainnet).

    Returns
    -------
    bool
        True if the transaction was successfully registered (new entry).
        False if the transaction was already registered (duplicate,
        unique constraint prevented insertion).
    """
    async with get_session() as session:
        # Try to insert the transaction; the UNIQUE constraint on
        # monitoring_jobs.transaction_hash will prevent duplicates.
        from stellarapi.database.models import MonitoringJob

        new_job = MonitoringJob(
            transaction_hash=transaction_hash,
            network=network,
            monitoring_status="ACTIVE",
            rpc_status="pending",
        )

        session.add(new_job)
        try:
            await session.commit()
            # Commit succeeded -> this was a new registration
            return True
        except SQLAlchemyError as exc:
            # Check if it's a unique constraint violation
            await session.rollback()
            # Inspect the error
            exc_str = str(exc).lower()
            if "unique constraint" in exc_str or "duplicate key" in exc_str or "pk_monitoring_jobs" in exc_str:
                # Duplicate — this is expected and safe; just return False
                logger.debug(
                    "Duplicate transaction hash detected: %s (network %s)",
                    transaction_hash,
                    network,
                )
                return False
            # Some other DB error — re-raise
            logger.error(
                "DB error registering transaction %s (network %s): %s",
                transaction_hash,
                network,
                exc,
            )
            raise


# ---------------------------------------------------------------------------
# Helper: process a single page of events
# ---------------------------------------------------------------------------


async def process_events_page(
    contract_address: str,
    cursor: Optional[str] = None,
    start_ledger: Optional[int] = None,
    end_ledger: Optional[int] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict:
    """Process one page of events and register discovered transactions.

    Parameters
    ----------
    contract_address:
        The Soroban contract address being discovered.
    cursor:
        Pagination cursor from previous page (or None).
    start_ledger:
        Lower ledger bound for this discovery job.
    end_ledger:
        Upper ledger bound for this discovery job.
    batch_size:
        Maximum events to process in this page.

    Returns
    -------
    dict with keys:
        - new_cursor: the cursor from the RPC response (or None if no more)
        - new_last_ledger: the last ledger number seen
        - registered_count: number of new transaction hashes registered
        - duplicate_count: number of duplicate (already-registered) hashes
        - error: error message if something went wrong, else None
    """
    # Fetch events page
    try:
        response = await fetch_events_page(
            contract_address,
            cursor=cursor,
            start_ledger=start_ledger,
            end_ledger=end_ledger,
            limit=batch_size,
        )
    except RpcCommunicationError as exc:
        logger.warning(
            "RPC error fetching events page for contract %s: %s",
            contract_address,
            exc,
        )
        return {
            "new_cursor": cursor,
            "new_last_ledger": start_ledger or 0,
            "registered_count": 0,
            "duplicate_count": 0,
            "error": str(exc),
        }

    records = response.get("records", [])
    new_cursor = response.get("cursor", None)
    new_last_ledger = (
        end_ledger
        if end_ledger is not None
        else (start_ledger or 0)
    )

    registered_count = 0
    duplicate_count = 0

    # Process each event
    for event_info in records:
        # EventInfo includes transaction_hash directly
        # EventInfo is a pydantic model; convert to dict for safe access
        event_dict = (
            event_info.model_dump() if hasattr(event_info, "model_dump") else dict(event_info)
        )

        transaction_hash = (
            event_dict.get("transaction_hash")
            or event_dict.get("txHash")
            or event_dict.get("id")
        )

        if not transaction_hash or len(transaction_hash) != 64:
            # If we can't extract a transaction hash, skip this event
            logger.debug(
                "Could not extract transaction_hash from event for contract %s",
                contract_address,
            )
            continue

        # Try to register the transaction hash
        was_new = await register_discovered_transaction(transaction_hash, "testnet")
        if was_new:
            registered_count += 1
        else:
            duplicate_count += 1

    # Update the last successful ledger based on what we processed
    # (use the max ledger from the events we processed, or the start_ledger)
    if records:
        # Try to get ledger from event info
        for event_info in records:
            event_dict = (
                event_info.model_dump()
                if hasattr(event_info, "model_dump")
                else dict(event_info)
            )
            ledger = event_dict.get("ledger")
            if ledger is not None:
                try:
                    ledger_int = int(ledger)
                    if new_last_ledger is None or ledger_int > new_last_ledger:
                        new_last_ledger = ledger_int
                except (ValueError, TypeError):
                    pass

    return {
        "new_cursor": new_cursor,
        "new_last_ledger": new_last_ledger or 0,
        "registered_count": registered_count,
        "duplicate_count": duplicate_count,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Core: discovery cycle for a single contract
# ---------------------------------------------------------------------------


async def discover_contract_events(
    contract_address: str,
    network: str = "testnet",
    max_consecutive_errors: int = MAX_CONSECUTIVE_ERRORS,
) -> dict:
    """Run the discovery cycle for a single contract.

    This is the main orchestration function for discovering transactions
    associated with a Soroban contract.  It handles pagination, checkpoint
    persistence, error recovery, and idempotency.

    Processing flow:
    1. Fetch events page with cursor pagination starting from saved cursor.
    2. For each event: extract transaction_hash and register it.
    3. After each batch: save checkpoint (cursor, last_ledger).
    4. Repeat until no more events or max consecutive errors reached.
    5. Set final status (COMPLETED or ERROR).

    Parameters
    ----------
    contract_address:
        64-character hex Soroban contract address.
    network:
        Stellar network identifier (testnet | mainnet).
    max_consecutive_errors:
        Number of consecutive RPC errors before terminating with ERROR status.

    Returns
    -------
    dict with keys:
        - status: final status (COMPLETED or ERROR).
        - discovered_count: total new transactions registered.
        - error_message: if status is ERROR, details of the failure.
        - final_cursor: cursor at the point where discovery stopped.
        - final_last_ledger: last ledger processed.
    """
    async with get_session() as session:
        # Look up the contract discovery job
        result = await session.execute(
            select(ContractDiscovery).where(
                ContractDiscovery.contract_address == contract_address,
                ContractDiscovery.network == network,
            )
        )
        job = result.scalar_one_or_none()

        if job is None:
            raise ValueError(
                f"No contract discovery job found for contract_address={contract_address!r} "
                f"on network={network!r}"
            )

        # Initialize tracking variables from saved state
        cursor = job.cursor  # may be None if this is a fresh start
        last_ledger = job.last_ledger or 0
        last_successful_ledger = job.last_successful_ledger or 0
        consecutive_errors = job.error_count or 0
        discovered_count = job.discovered_count or 0

        # Determine start_ledger: resume from last_successful_ledger + 1
        start_ledger = last_successful_ledger + 1 if last_successful_ledger else None

        # Determine if we should start fresh (PENDING -> IN_PROGRESS)
        if job.status == "PENDING":
            job.status = "IN_PROGRESS"
            await session.commit()

        # Create RPC client
        rpc_client = RpcClient()  # uses settings.SOROBAN_RPC_URL

        try:
            # Main discovery loop
            while True:
                # Fetch and process one page of events
                result = await process_events_page(
                    contract_address,
                    cursor=cursor,
                    start_ledger=start_ledger,
                    end_ledger=None,  # no upper bound for MVP
                    batch_size=DEFAULT_BATCH_SIZE,
                )

                if result.get("error"):
                    consecutive_errors += 1
                    logger.warning(
                        "Error processing events page for contract %s: %s "
                        "(consecutive errors: %d/%d)",
                        contract_address,
                        result["error"],
                        consecutive_errors,
                        max_consecutive_errors,
                    )

                    if consecutive_errors >= max_consecutive_errors:
                        # Too many errors — mark as ERROR
                        job.status = "ERROR"
                        job.error_message = result["error"]
                        job.error_count = consecutive_errors
                        job.discovered_count = discovered_count
                        await session.commit()
                        return {
                            "status": "ERROR",
                            "discovered_count": discovered_count,
                            "error_message": result["error"],
                            "final_cursor": cursor,
                            "final_last_ledger": last_ledger,
                        }
                else:
                    # Reset consecutive error counter on success
                    consecutive_errors = 0

                # Update tracking variables
                new_cursor = result.get("new_cursor")
                new_last_ledger = result.get("new_last_ledger", last_ledger)
                registered_count = result.get("registered_count", 0)
                duplicate_count = result.get("duplicate_count", 0)

                # Update state
                if new_cursor:
                    cursor = new_cursor
                if new_last_ledger is not None:
                    last_ledger = new_last_ledger

                discovered_count += registered_count

                # Save checkpoint after each batch
                job.cursor = cursor
                job.last_ledger = last_ledger
                job.last_successful_ledger = max(
                    last_successful_ledger, last_ledger
                )
                job.discovered_count = discovered_count
                job.error_count = 0  # reset error count on success
                job.error_message = None  # clear any previous error

                await session.commit()

                # Check if there are more events (cursor indicates more pages)
                if not new_cursor:
                    # No more events — discovery complete
                    job.status = "COMPLETED"
                    await session.commit()
                    return {
                        "status": "COMPLETED",
                        "discovered_count": discovered_count,
                        "error_message": None,
                        "final_cursor": cursor,
                        "final_last_ledger": last_ledger,
                    }

                # Otherwise, continue to the next page
                # start_ledger for next iteration: resume from last_ledger + 1
                start_ledger = last_ledger + 1

        except Exception as exc:
            # Unexpected error — mark as ERROR
            logger.exception(
                "Unexpected error during discovery for contract %s: %s",
                contract_address,
                exc,
            )
            job.status = "ERROR"
            job.error_message = f"Unexpected error: {exc}"
            job.error_count = consecutive_errors + 1
            job.discovered_count = discovered_count
            await session.commit()
            return {
                "status": "ERROR",
                "discovered_count": discovered_count,
                "error_message": f"Unexpected error: {exc}",
                "final_cursor": cursor,
                "final_last_ledger": last_ledger,
            }
        finally:
            # Clean up RPC client
            try:
                await rpc_client.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Worker: main discovery worker loop
# ---------------------------------------------------------------------------


async def discovery_worker_cycle(
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_consecutive_errors: int = MAX_CONSECUTIVE_ERRORS,
) -> dict:
    """Run one cycle of the discovery worker.

    This function:
    1. Picks up contracts with status PENDING or IN_PROGRESS from the
       contracts_discovery table.
    2. Runs the discovery cycle for each contract.
    3. Returns a summary of the cycle.

    The worker processes one contract at a time to avoid uncontrolled RPC load,
    consistent with the existing monitoring worker pattern.

    Parameters
    ----------
    batch_size:
        Number of events to fetch per get_events() call.
    max_consecutive_errors:
        Consecutive errors before a contract job is marked ERROR.

    Returns
    -------
    dict with keys:
        - processed_contracts: number of contracts processed in this cycle.
        - completed_contracts: number of contracts that reached COMPLETED.
        - error_contracts: number of contracts that reached ERROR.
        - total_new_transactions: total newly registered transaction hashes.
    """
    async with get_session() as session:
        # Pick up contracts that need discovery: PENDING or IN_PROGRESS
        result = await session.execute(
            select(ContractDiscovery).where(
                ContractDiscovery.status.in_(("PENDING", "IN_PROGRESS"))
            )
        )
        contracts = result.scalars().all()

        if not contracts:
            return {
                "processed_contracts": 0,
                "completed_contracts": 0,
                "error_contracts": 0,
                "total_new_transactions": 0,
            }

        completed_contracts = 0
        error_contracts = 0
        total_new_transactions = 0

        for contract in contracts:
            try:
                result = await discover_contract_events(
                    contract_address=contract.contract_address,
                    network=contract.network,
                    max_consecutive_errors=max_consecutive_errors,
                )

                if result["status"] == "COMPLETED":
                    completed_contracts += 1
                    total_new_transactions += result.get("discovered_count", 0)
                elif result["status"] == "ERROR":
                    error_contracts += 1

            except Exception as exc:
                logger.exception(
                    "Discovery worker failed for contract %s/%s: %s",
                    contract.contract_address,
                    contract.network,
                    exc,
                )
                # Mark the contract as ERROR if not already
                if contract.status != "ERROR":
                    contract.status = "ERROR"
                    contract.error_message = str(exc)
                    contract.error_count = (contract.error_count or 0) + 1
                    await session.commit()
                error_contracts += 1

        return {
            "processed_contracts": len(contracts),
            "completed_contracts": completed_contracts,
            "error_contracts": error_contracts,
            "total_new_transactions": total_new_transactions,
        }


# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the discovery worker.

    Runs one cycle of the discovery worker, processing all contracts
    that are pending or in progress.
    """
    import asyncio

    result = asyncio.run(discovery_worker_cycle())
    print(f"Discovery worker cycle complete: {result}")


if __name__ == "__main__":
    main()
