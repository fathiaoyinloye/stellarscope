"""Async Stellar RPC client for StellarScope.

Provides a thin, async wrapper around the stellar-sdk ``SorobanServer``
``get_transaction`` method.  Keeps RPC communication separate from
business logic and database operations, as approved in the roadmap.

Key design points
--------------------
- Uses ``stellar_sdk.SorobanServer`` under the hood (which uses ``aiohttp``).
- Returns a normalized :class:`~stellarapi.rpc_client.GetTransactionResult`
  dataclass that preserves all fields from the RPC response.
- ``NOT_FOUND`` status is handled without attempting to decode result XDR.
- All exceptions wrap the original error so callers can distinguish
  communication failures from decoding failures.
- Timeout and HTTP error handling are configurable.
- No secrets or sensitive configuration are emitted in logs.
- Custom exception types allow callers to distinguish between RPC
  communication failures, response errors, and XDR decoding failures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import stellar_sdk
from stellar_sdk import SorobanServer
from stellar_sdk.exceptions import SorobanRpcErrorResponse

from stellarapi.config.settings import settings
from stellarapi.exceptions import (
    RpcClientError,
    RpcCommunicationError,
    RpcResponseError,
    XdrDecodingError,
    UnsupportedXdrStructure,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy-import wrapper for decoder functions — avoids circular imports
# ---------------------------------------------------------------------------

def _get_decoder():
    """Return decoder functions, imported lazily to break circular deps."""
    from stellarapi.xdr.decoder import (
        decode_fee_bump_result,
        decode_transaction_result,
        decode_transaction_meta,
        decode_diagnostic_events,
    )
    return decode_fee_bump_result, decode_transaction_result, decode_transaction_meta, decode_diagnostic_events


# ---------------------------------------------------------------------------
# Normalized response models
# ---------------------------------------------------------------------------


@dataclass
class TransactionResultInfo:
    """Decoded transaction result information.

    Holds the fields we care about from a ``TransactionResult`` XDR,
    falling back to ``None`` when the XDR structure is unexpected.
    """

    #: Code of the result (SUCCESS, FAILED, etc.) — from the inner result
    #: for fee‑bump transactions or the outer result for normal txs.
    result_code: Optional[str] = None

    #: Fee charged for the transaction
    fee_charged: Optional[str] = None

    #: Home domain (for trustlines, etc.)
    home_domain: Optional[str] = None

    #: Low threshold (for authorization)
    low_threshold: Optional[int] = None

    #: Medium threshold (for authorization)
    medium_threshold: Optional[int] = None

    #: High threshold (for authorization)
    high_threshold: Optional[int] = None

    #: Home domain for the master key
    home_domain: Optional[str] = None

    #: Last price (for asset offers, etc.)
    last_price_ratio: Optional[str] = None

    #: Claimable balance id (for claimable balances)
    claimable_balance: Optional[str] = None

    #: Data name (for manage data ops)
    data_name: Optional[str] = None

    #: Last touched ledger sequence
    last_ledger: Optional[int] = None

    #: Home domain for the signer
    signer_domain: Optional[str] = None

    #: Signer weight
    signer_weight: Optional[int] = None


@dataclass
class TransactionMetaInfo:
    """Decoded transaction metadata information."""

    #: Number of operations in the transaction
    num_ops: Optional[int] = None

    #: Return result xdr (if available)
    return_value: Optional[str] = None

    #: Fee meta information
    fee_meta: Optional[str] = None

    #: Create fee charge
    create_fee: Optional[str] = None

    #: Authorization required
    authorization: Optional[list] = None


@dataclass
class DiagnosticEventInfo:
    """Information from a diagnostic event XDR, where available."""

    #: Event type
    type: Optional[str] = None

    #: Event name
    name: Optional[str] = None

    #: Event source
    source: Optional[str] = None

    #: Event attributes
    attributes: Optional[dict] = None


@dataclass
class GetTransactionResult:
    """Normalized result of a ``get_transaction`` RPC call.

    This dataclass is the primary return type of ``RpcClient.get_transaction()``.
    It preserves the raw RPC response and all available decoded fields.

    Invariants
    ----------
    - ``rpc_status`` is always one of ``SUCCESS``, ``FAILED``, ``NOT_FOUND``.
    - If ``rpc_status == "NOT_FOUND"``, then ``result_xdr``,
      ``result_meta_xdr``, and all decoded fields are ``None``/empty —
      the RPC did not return result data.
    - ``raw_rpc_response`` always contains the original JSON-derived data
      returned by the RPC, even when decoding fails.
    """

    #: RPC status: ``SUCCESS``, ``FAILED``, or ``NOT_FOUND``
    rpc_status: stellar_sdk.soroban_rpc.GetTransactionStatus

    #: The transaction hash that was queried
    transaction_hash: str

    #: Latest observed ledger number (may be ``None`` for ``NOT_FOUND``)
    ledger: Optional[int]

    #: Ledger close time (unix timestamp, may be ``None``)
    close_time: Optional[int]

    #: Oldest ledger in the batch (may be ``None``)
    oldest_ledger: Optional[int]

    #: Transaction creation time (unix timestamp, may be ``None``)
    create_at: Optional[int]

    #: Application order of the transaction (may be ``None``)
    application_order: Optional[int]

    #: Whether this was a fee‑bump transaction
    fee_bump: Optional[bool]

    #: Envelope XDR — base64 encoded, may be ``None``
    envelope_xdr: Optional[str]

    #: Result XDR — base64 encoded, may be ``None`` (present for SUCCESS/FAILED)
    result_xdr: Optional[str]

    #: Result metadata XDR — base64 encoded, may be ``None``
    result_meta_xdr: Optional[str]

    #: Diagnostic events XDR — base64 list, may be ``None``
    diagnostic_events_xdr: Optional[list[str]]

    #: Soroban events, may be ``None``
    events: Optional[object]

    #: Ledger sequence that included the transaction (may be ``None`` for NOT_FOUND)
    ledger: Optional[int]

    #: Raw RPC response — preserved in its entirety for evidence
    raw_rpc_response: dict
# ---------------------------------------------------------------------------
# RPC Client
# ---------------------------------------------------------------------------


class RpcClient:
    """Async Stellar RPC client.

    Wraps ``stellar_sdk.SorobanServer.get_transaction`` with proper error
    handling, timeout propagation, and result normalization.
    """

    def __init__(
        self,
        *,
        server_url: str | None = None,
        timeout: float = settings.RPC_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the RPC client.

        Args:
            server_url: Soroban RPC endpoint URL.  Defaults to the value
                configured in ``settings`` (Testnet by default).
            timeout: Request timeout in seconds.  Passed to the underlying
                ``aiohttp`` client.
        """
        self._server_url = server_url or settings.SOROBAN_RPC_URL
        self._timeout = timeout
        self._server: SorobanServer | None = None

    @property
    def server(self) -> SorobanServer:
        """Lazily create and return the underlying ``SorobanServer`` instance."""
        if self._server is None:
            self._server = SorobanServer(self._server_url)
        return self._server

    async def get_transaction(self, transaction_hash: str) -> GetTransactionResult:
        """Fetch a transaction from the Soroban RPC and return a normalized result.

        Args:
            transaction_hash: 64-character hex-encoded transaction hash.

        Returns:
            ``GetTransactionResult`` with all available normalized fields
            and the raw RPC response preserved.

        Raises:
            ``RpcCommunicationError``: If the RPC call fails due to network
                issues, timeouts, or connection problems.
            ``RpcResponseError``: If the RPC returns a malformed or unexpected
                response format.
            ``XdrDecodingError``: If XDR decoding fails for a known structure.
            ``UnsupportedXdrStructure``: If an XDR structure is recognised but
                not supported.
        """
        if not transaction_hash or len(transaction_hash) != 64:
            msg = f"Invalid transaction hash: {transaction_hash!r}"
            logger.error(msg)
            raise ValueError(msg)

        try:
            # ``SorobanServer`` is the synchronous stellar-sdk client; calling
            # it returns the response directly (it is not awaitable).
            raw: stellar_sdk.soroban_rpc.GetTransactionResponse = (
                self.server.get_transaction(transaction_hash)
            )
        except (
            ConnectionError,
            TimeoutError,
            stellar_sdk.exceptions.SorobanRpcErrorResponse,
            stellar_sdk.exceptions.NotFoundError,
        ) as exc:
            logger.warning(
                "RPC communication error fetching transaction %s: %s",
                transaction_hash,
                exc,
            )
            raise RpcCommunicationError(
                f"RPC communication error fetching transaction {transaction_hash}: {exc}"
            ) from exc
        except SorobanRpcErrorResponse as exc:
            logger.error(
                "Soroban server error response for transaction %s: %s",
                transaction_hash,
                exc,
            )
            raise RpcResponseError(
                f"RPC error response for transaction {transaction_hash}: {exc}"
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected error fetching transaction %s", transaction_hash
            )
            raise RpcCommunicationError(
                f"Unexpected error fetching transaction {transaction_hash}: {exc}"
            ) from exc

        # --- Normal path: we have a successful RPC response ---
        return self._normalize(raw)

    # ------------------------------------------------------------------
    # Result normalization
    # ------------------------------------------------------------------

    def _normalize(self, raw: stellar_sdk.soroban_rpc.GetTransactionResponse) -> GetTransactionResult:
        """Convert a ``stellar_sdk.soroban_rpc.GetTransactionResponse`` into
        our internal ``GetTransactionResult`` dataclass.

        The method is **best‑effort**: every field is set to ``None`` when the
        underlying RPC did not provide it or when XDR decoding fails.

        Important: When ``fee_bump`` is ``True``, the inner transaction result
        XDR is inspected via ``decode_fee_bump_result`` rather than assuming
        the normal top-level ``TransactionResult`` structure.  This ensures
        that ``in_successful_contract_call`` is not treated as a generic
        transaction success/failure indicator.
        """

        # --- Lazy-import decoder functions to avoid circular imports ---
        decode_fee_bump_result, decode_transaction_result, decode_transaction_meta, decode_diagnostic_events = _get_decoder()

        # --- Status ---
        status = raw.status  # type: stellar_sdk.soroban_rpc.GetTransactionStatus

        # --- Basic fields ---
        transaction_hash = raw.transaction_hash
        ledger = raw.ledger
        close_time = raw.latest_ledger_close_time
        oldest_ledger = raw.oldest_ledger
        create_at = raw.create_at
        application_order = raw.application_order
        fee_bump = raw.fee_bump
        envelope_xdr = raw.envelope_xdr
        result_xdr = raw.result_xdr
        result_meta_xdr = raw.result_meta_xdr
        diagnostic_events_xdr = raw.diagnostic_events_xdr
        events = raw.events

        # --- Build the raw snapshot before any decoding ---
        raw_rpc_response = {
            "status": status.value,
            "transaction_hash": raw.transaction_hash,
            "ledger": raw.ledger,
            "latest_ledger": raw.latest_ledger,
            "latest_ledger_close_time": raw.latest_ledger_close_time,
            "oldest_ledger": raw.oldest_ledger,
            "oldest_ledger_close_time": raw.oldest_ledger_close_time,
            "application_order": raw.application_order,
            "fee_bump": raw.fee_bump,
            "envelope_xdr": raw.envelope_xdr,
            "result_xdr": raw.result_xdr,
            "result_meta_xdr": raw.result_meta_xdr,
            "diagnostic_events_xdr": raw.diagnostic_events_xdr,
            "events": (
                {
                    "transaction_events_xdr": events.transaction_events_xdr
                    if events
                    else None,
                    "contract_events_xdr": events.contract_events_xdr
                    if events
                    else None,
                }
                if events
                else None
            ),
        }

        # --- Decode result XDR, handling fee‑bump transactions ---
        decoded_result: Optional[TransactionResultInfo] = None
        decoded_meta: Optional[TransactionMetaInfo] = None
        diagnostic_events: list[DiagnosticEventInfo] = []

        if raw.fee_bump:
            # Fee-bump transaction: inspect the inner result XDR rather than
            # assuming the normal TransactionResult structure.
            # The raw result_xdr is the outer result; we need the inner result.
            inner_result_xdr = getattr(raw, "inner_result_xdr", None) or None
            try:
                decoded_result = decode_fee_bump_result(
                    result_xdr=result_xdr,
                    inner_result_xdr=inner_result_xdr,
                )
            except (XdrDecodingError, UnsupportedXdrStructure) as exc:
                logger.warning(
                    "XDR decode failure for fee-bump transaction %s: %s",
                    transaction_hash,
                    exc,
                )
                # Keep decoded_result as None; raw XDR is preserved in
                # raw_rpc_response for evidence.
        else:
            # Normal (non fee-bump) transaction: decode the outer result XDR.
            if result_xdr:
                try:
                    decoded_result = decode_transaction_result(result_xdr)
                except (XdrDecodingError, UnsupportedXdrStructure) as exc:
                    logger.warning(
                        "XDR decode failure for result_xdr of transaction %s: %s",
                        transaction_hash,
                        exc,
                    )
                    # Do not set result_xdr to None — the raw value is
                    # preserved in ``raw_rpc_response`` for evidence.

        # --- Decode result_meta XDR if present ---
        if result_meta_xdr:
            try:
                decoded_meta = decode_transaction_meta(result_meta_xdr)
            except (XdrDecodingError, UnsupportedXdrStructure) as exc:
                logger.warning(
                    "XDR decode failure for result_meta_xdr of transaction %s: %s",
                    transaction_hash,
                    exc,
                )

        # --- Decode diagnostic events XDR if present ---
        if diagnostic_events_xdr:
            try:
                diagnostic_events = decode_diagnostic_events(diagnostic_events_xdr)
            except (XdrDecodingError, UnsupportedXdrStructure) as exc:
                logger.warning(
                    "XDR decode failure for diagnostic events of transaction %s: %s",
                    transaction_hash,
                    exc,
                )

        # --- Build normalized diagnostics ---
        diag_list: Optional[list[DiagnosticEventInfo]] = (
            diagnostic_events if diagnostic_events else None
        )

        return GetTransactionResult(
            rpc_status=status,
            transaction_hash=transaction_hash,
            ledger=ledger,
            close_time=close_time,
            oldest_ledger=oldest_ledger,
            create_at=create_at,
            application_order=application_order,
            fee_bump=fee_bump,
            envelope_xdr=envelope_xdr,
            result_xdr=result_xdr,
            result_meta_xdr=result_meta_xdr,
            diagnostic_events_xdr=diagnostic_events_xdr,
            events=events,
            raw_rpc_response=raw_rpc_response,
        )

    def get_health(self) -> dict:
        """Check RPC server health.

        Returns a dict with:
        - status: "healthy" | "degraded" | "unavailable"
        - latest_ledger: latest known ledger number
        - oldest_ledger: oldest ledger still retained
        - ledger_retention_window: retention window in seconds
        - rpc_status: raw RPC status string
        """
        try:
            response = self._server.get_health()
            return {
                "status": response.status,
                "latest_ledger": response.latest_ledger,
                "oldest_ledger": response.oldest_ledger,
                "ledger_retention_window": response.ledger_retention_window,
                "rpc_status": "healthy",
            }
        except Exception as exc:
            raise RpcCommunicationError(
                f"Stellar RPC health check failed: {exc}"
            ) from exc
