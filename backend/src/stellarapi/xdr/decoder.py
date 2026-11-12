"""XDR decoder for StellarScope Phase 4.

Provides decoding functionality for Soroban transaction results, metadata,
and diagnostic events.  Uses the pinned ``stellar_sdk`` version and is
designed to be best‑effort: if a structure is unexpected the decoder
returns ``None``/empty rather than raising, and the original raw XDR is
always preserved.

Key design points
-----------------
- Decoding is ``best‑effort``: any field that cannot be extracted is
  ``None``/empty, never an exception (except for truly unrecoverable
  cases like invalid base64, which are logged).
- Fee‑bump transactions are handled by inspecting the inner result rather
  than assuming the normal top‑level structure.
- Diagnostic events are decoded where possible; failures on individual
  events do not prevent other events from being decoded.
- All decoding failures are logged; the original raw XDR remains
  available in ``GetTransactionResult.raw_rpc_response`` for evidence.
- ``stellar_sdk.xdr.from_xdr_bytes`` may raise ``ValueError`` for
  ``TransactionResult`` XDR data with extra trailing bytes (common with
  Soroban RPC responses).  The decoder handles this by trimming the last
  4 bytes when encountered, consistent with the SDK's behaviour.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Optional

import stellar_sdk
from stellar_sdk.xdr import TransactionResultCode

from stellarapi.rpc_client import (
    DiagnosticEventInfo,
    GetTransactionResult,
    TransactionMetaInfo,
    TransactionResultInfo,
)

logger = logging.getLogger(__name__)


def _decode_xdr_bytes(result_xdr: str) -> Optional[Any]:
    """Base64‑decode a result XDR string and return the parsed XDR struct.

    Attempts ``stellar_sdk.xdr.TransactionResult.from_xdr_bytes`` first.
    If that raises ``ValueError`` with ``"trailing"``, trims the last 4
    bytes (a common Soroban RPC quirk) and retries.

    Args:
        result_xdr: Base64‑encoded ``TransactionResult`` XDR.

    Returns:
        Parsed XDR struct, or ``None`` if decoding fails entirely.
    """
    try:
        raw = base64.b64decode(result_xdr)
    except Exception as exc:
        logger.warning("Failed to base64-decode result_xdr: %s", exc)
        return None

    # Try full bytes first
    try:
        return stellar_sdk.xdr.TransactionResult.from_xdr_bytes(raw)
    except ValueError as exc:
        if "trailing" in str(exc).lower():
            # Soroban RPC sometimes returns TransactionResult XDR with
            # 4 extra trailing bytes; trim them and retry.
            logger.debug(
                "XDR decode had trailing bytes, trimming last 4 bytes"
            )
            try:
                return stellar_sdk.xdr.TransactionResult.from_xdr_bytes(raw[:-4])
            except Exception:
                logger.warning("XDR decode failed after trimming trailing bytes")
                return None
        else:
            logger.warning("XDR decode ValueError: %s", exc)
            return None
    except Exception as exc:
        logger.warning("XDR decode unexpected error: %s", exc)
        return None


def decode_transaction_result(result_xdr: str) -> Optional[TransactionResultInfo]:
    """Decode a ``TransactionResult`` from its base64 XDR string.

    Args:
        result_xdr: Base64‑encoded ``TransactionResult`` XDR.

    Returns:
        ``TransactionResultInfo`` with the fields we care about, or
        ``None`` if the XDR cannot be decoded or has an unexpected
        structure.
    """
    decoded = _decode_xdr_bytes(result_xdr)
    if decoded is None:
        return None

    # Extract the fields we care about from the decoded XDR struct.
    # ``TransactionResult`` has: result_code, fee_charged, home_domain,
    # thresholds, last_price_ratio, claimable_balance, data_name,
    # last_ledger, signer_domain, signer_weight.
    try:
        result = decoded.result  # TransactionResultResult
        code = result.code if result else None

        info = TransactionResultInfo(
            result_code=str(code) if code is not None else None,
            fee_charged=str(decoded.fee_charged) if decoded.fee_charged else None,
            home_domain=getattr(decoded, "home_domain", None) or None,
            low_threshold=getattr(decoded, "low_threshold", None) or None,
            medium_threshold=getattr(decoded, "medium_threshold", None) or None,
            high_threshold=getattr(decoded, "high_threshold", None) or None,
            last_price_ratio=getattr(decoded, "last_price_ratio", None) or None,
            claimable_balance=getattr(decoded, "claimable_balance", None) or None,
            data_name=getattr(decoded, "data_name", None) or None,
            last_ledger=getattr(decoded, "last_ledger", None) or None,
            signer_domain=getattr(decoded, "signer_domain", None) or None,
            signer_weight=getattr(decoded, "signer_weight", None) or None,
        )
        return info
    except Exception as exc:
        logger.warning("Failed to extract TransactionResult fields: %s", exc)
        return None


def decode_transaction_meta(result_meta_xdr: str) -> Optional[TransactionMetaInfo]:
    """Decode a ``TransactionMeta`` from its base64 XDR string.

    Args:
        result_meta_xdr: Base64‑encoded ``TransactionMeta`` XDR.

    Returns:
        ``TransactionMetaInfo`` with available fields, or ``None`` if
        decoding fails or the structure is unexpected.
    """
    try:
        raw = base64.b64decode(result_meta_xdr)
    except Exception as exc:
        logger.warning("Failed to base64-decode result_meta_xdr: %s", exc)
        return None

    # Try to decode using ``TransactionMeta`` XDR struct.
    if not hasattr(stellar_sdk.xdr, "TransactionMeta"):
        logger.warning("TransactionMeta XDR struct not available in this SDK version")
        return None

    try:
        meta = stellar_sdk.xdr.TransactionMeta.from_xdr_bytes(raw)
    except Exception:
        # Try trimming trailing bytes (same pattern as TransactionResult)
        try:
            meta = stellar_sdk.xdr.TransactionMeta.from_xdr_bytes(raw[:-4])
        except Exception:
            logger.warning("Failed to decode TransactionMeta XDR")
            return None

    if not hasattr(meta, "num_ops"):
        logger.warning("Decoded TransactionMeta missing num_ops field")
        return None

    try:
        return TransactionMetaInfo(
            num_ops=meta.num_ops,
            return_value=str(meta.return_value) if meta.return_value else None,
            fee_meta=str(meta.fee_meta) if meta.fee_meta else None,
            create_fee=str(meta.create_fee) if meta.create_fee else None,
            authorization=(
                meta.authorization
                if hasattr(meta, "authorization")
                else None
            ),
        )
    except Exception as exc:
        logger.warning("Failed to extract TransactionMeta fields: %s", exc)
        return None


def decode_diagnostic_events(
    diagnostic_events_xdr: list[str],
) -> list[DiagnosticEventInfo]:
    """Decode a list of diagnostic event XDR strings.

    Args:
        diagnostic_events_xdr: List of base64‑encoded diagnostic event XDRs.

    Returns:
        List of ``DiagnosticEventInfo``; each entry represents one event.
        If a particular event cannot be decoded, a best‑effort entry with
        whatever fields could be extracted is still included, and the
        error is logged.
    """
    results: list[DiagnosticEventInfo] = []

    for i, evt_xdr in enumerate(diagnostic_events_xdr):
        try:
            raw = base64.b64decode(evt_xdr)
        except Exception:
            logger.warning("Failed to base64-decode diagnostic event %d", i)
            results.append(DiagnosticEventInfo())
            continue

        # Attempt to decode using ``DiagnosticEvent`` XDR struct.
        if not hasattr(stellar_sdk.xdr, "DiagnosticEvent"):
            # If the struct is not available, create a best‑effort entry
            # with whatever minimal info we can gather.
            results.append(DiagnosticEventInfo())
            continue

        try:
            evt = stellar_sdk.xdr.DiagnosticEvent.from_xdr_bytes(raw)
        except Exception:
            # Try trimming trailing bytes
            try:
                evt = stellar_sdk.xdr.DiagnosticEvent.from_xdr_bytes(raw[:-4])
            except Exception:
                logger.warning(
                    "Failed to decode diagnostic event %d XDR", i
                )
                results.append(DiagnosticEventInfo())
                continue

        try:
            results.append(
                DiagnosticEventInfo(
                    type=getattr(evt, "type", None),
                    name=getattr(evt, "name", None),
                    source=getattr(evt, "source", None),
                    attributes=(
                        evt.attributes
                        if hasattr(evt, "attributes")
                        else None
                    ),
                )
            )
        except Exception:
            logger.warning("Failed to extract diagnostic event %d fields", i)
            results.append(DiagnosticEventInfo())

    return results


def _extract_inner_result_info(decoded_outer: Any) -> Optional[TransactionResultInfo]:
    """Extract the inner transaction result from a fee-bump outer result.

    Soroban RPC returns a single outer ``TransactionResult`` XDR for fee-bump
    transactions.  When the outer code is ``txFEE_BUMP_INNER_SUCCESS`` or
    ``txFEE_BUMP_INNER_FAILED``, the actual inner result is embedded in
    ``inner_result_pair.result`` (an ``InnerTransactionResult``).  This helper
    recovers it so callers do not need a separate ``inner_result_xdr`` field
    (which the RPC does not provide).
    """
    try:
        outer_result = getattr(decoded_outer, "result", None)
        if outer_result is None:
            return None

        code = getattr(outer_result, "code", None)
        if code not in (
            TransactionResultCode.txFEE_BUMP_INNER_SUCCESS,
            TransactionResultCode.txFEE_BUMP_INNER_FAILED,
        ):
            return None

        pair = getattr(outer_result, "inner_result_pair", None)
        if pair is None:
            return None

        inner = getattr(pair, "result", None)
        if inner is None:
            return None

        inner_result = getattr(inner, "result", None)
        inner_code = getattr(inner_result, "code", None) if inner_result else None
        fee_charged = getattr(inner, "fee_charged", None)

        return TransactionResultInfo(
            result_code=str(inner_code) if inner_code is not None else None,
            fee_charged=str(fee_charged) if fee_charged else None,
        )
    except Exception as exc:
        logger.warning("Failed to extract fee-bump inner result: %s", exc)
        return None


def decode_fee_bump_result(
    result_xdr: str, inner_result_xdr: Optional[str] = None
) -> Optional[TransactionResultInfo]:
    """Decode a fee‑bump transaction result.

    For fee‑bump transactions the outer ``TransactionResult`` XDR
    indicates the overall outcome, but the actual result of the inner
    transaction is inside a ``FeeBumpTransaction`` structure.  This
    helper inspects the inner result if provided, falling back to the
    outer result.

    Args:
        result_xdr: Base64‑encoded outer ``TransactionResult`` XDR.
        inner_result_xdr: Optional base64‑encoded inner ``TransactionResult``
            XDR.  If ``None`` we decode ``result_xdr`` as a normal (non‑fee‑bump)
            transaction.

    Returns:
        ``TransactionResultInfo`` with the best‑available decoded
        information, or ``None`` if decoding fails entirely.
    """
    # Decode the outer result first
    outer = decode_transaction_result(result_xdr)
    if outer is None:
        return None

    # If an explicit inner result XDR was supplied, try that first;
    # this is the pattern for fee‑bump transactions.
    if inner_result_xdr:
        inner = decode_transaction_result(inner_result_xdr)
        if inner is not None:
            # Merge: prefer inner result codes/fees, but keep outer
            # information that isn't overwritten.
            merged = TransactionResultInfo(
                result_code=inner.result_code or outer.result_code,
                fee_charged=inner.fee_charged or outer.fee_charged,
                home_domain=inner.home_domain or outer.home_domain,
                low_threshold=inner.low_threshold or outer.low_threshold,
                medium_threshold=inner.medium_threshold or outer.medium_threshold,
                high_threshold=inner.high_threshold or outer.high_threshold,
                last_price_ratio=inner.last_price_ratio or outer.last_price_ratio,
                claimable_balance=inner.claimable_balance or outer.claimable_balance,
                data_name=inner.data_name or outer.data_name,
                last_ledger=inner.last_ledger or outer.last_ledger,
                signer_domain=inner.signer_domain or outer.signer_domain,
                signer_weight=inner.signer_weight or outer.signer_weight,
            )
            logger.info("Decoded fee‑bump transaction result using inner XDR")
            return merged

    # No explicit inner XDR was supplied.  Recover the inner result from the
    # outer XDR (the RPC does not return a separate inner_result_xdr field).
    decoded_outer = _decode_xdr_bytes(result_xdr)
    inner = (
        _extract_inner_result_info(decoded_outer)
        if decoded_outer is not None
        else None
    )
    if inner is not None:
        merged = TransactionResultInfo(
            result_code=inner.result_code or outer.result_code,
            fee_charged=inner.fee_charged or outer.fee_charged,
            home_domain=outer.home_domain,
            low_threshold=outer.low_threshold,
            medium_threshold=outer.medium_threshold,
            high_threshold=outer.high_threshold,
            last_price_ratio=outer.last_price_ratio,
            claimable_balance=outer.claimable_balance,
            data_name=outer.data_name,
            last_ledger=outer.last_ledger,
            signer_domain=outer.signer_domain,
            signer_weight=outer.signer_weight,
        )
        logger.info(
            "Decoded fee‑bump transaction result from embedded inner XDR"
        )
        return merged

    # Fall back to treating it as a normal transaction result.
    logger.info("Decoded transaction result (non fee-bump fallback) via outer XDR")
    return outer
