"""Custom exception types for StellarScope."""

from __future__ import annotations


class RpcClientError(RuntimeError):
    """Base exception for RPC client errors."""


class RpcCommunicationError(RpcClientError):
    """Raised when the RPC call fails due to network issues, timeouts, or
    connection problems.  The original exception is chained via ``__cause__``."""


class RpcResponseError(RpcClientError):
    """Raised when the RPC returns a malformed or unexpected response format."""


class XdrDecodingError(RpcClientError):
    """Raised when XDR decoding fails for a known structure."""


class UnsupportedXdrStructure(RpcClientError):
    """Raised when an XDR structure is recognised but not supported."""