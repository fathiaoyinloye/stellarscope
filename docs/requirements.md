# StellarScope — MVP Requirements

## Scope

### In Scope

- **Transaction registration**: Users can submit Stellar/Soroban transaction hashes for monitoring.
- **Transaction status polling**: Background worker periodically polls `stellar-rpc getTransaction` for registered hashes.
- **Status tracking**: Three terminal states are tracked: `SUCCESS`, `FAILED`, `NOT_FOUND` (per Stellar RPC).
- **Evidence preservation**: Raw RPC response and base64 XDR data (`envelopeXdr`, `resultXdr`, `resultMetaXdr`, `diagnosticEventsXdr`) stored for each transaction.
- **REST API**: FastAPI endpoints to retrieve monitoring information for registered transactions.
- **Database**: PostgreSQL with SQLAlchemy 2.x async, managed via Alembic migrations.
- **Background worker**: Separately executable process that polls and updates transaction statuses.
- **Testnet only**: MVP targets Stellar Testnet only; Mainnet support is out of scope.
- **Manually registered hashes**: No automatic contract discovery or network-wide indexing.

### Out of Scope (Exclusions)

- **Frontend**: No UI or web interface.
- **Redis or Celery**: No Redis, Celery, or other message broker.
- **Mainnet support**: Testnet only.
- **Automatic contract discovery**: No scanning or indexing of the network.
- **Historical transaction indexing beyond RPC retention**: RPC `getTransaction` has a bounded history (~7 days default). Data beyond that must be indexed separately (out of MVP scope).
- **Human-readable failure explanations**: Diagnostic events and contract events may not always be available; the system preserves raw XDR but does not guarantee decoded failure reasons.
- **Real-time streaming**: Poll-based polling only; no WebSocket or streaming subscriptions.
- **Fee-bump inner-transaction result decoding beyond raw XDR**: Inner result XDR is preserved; decoding to human-readable form is a future enhancement.

## Assumptions

1. Stellar RPC `getTransaction` response statuses are exactly `SUCCESS`, `FAILED`, `NOT_FOUND` — no `PENDING` status from RPC.
2. The default RPC history-retention-window is ~7 days (120960 ledgers). Transactions older than this may return `NOT_FOUND` regardless of their actual state.
3. `stellar-sdk` (py-stellar-base) can decode `GetTransactionResponse` and XDR structures sufficiently for the MVP.
4. PostgreSQL 15+ is available with `SELECT ... FOR UPDATE SKIP LOCKED` support for worker coordination.
5. One background worker process will be separately executable (via `pyproject.toml` console_scripts or Docker entrypoint).
6. Transaction hashes submitted by users are valid Stellar transaction hashes (64-character hex strings).
7. Soroban RPC endpoint URL is configurable via environment variable (`STELLAR_RPC_URL`).
8. Database connection URL is configurable via environment variable (`DATABASE_URL`).

## Definitions

- **Observation deadline**: The point at which StellarScope stops monitoring a specific transaction. This does not mean the Stellar transaction has expired or been confirmed; it means the monitoring process has moved on.
- **Terminal state**: A transaction status of `SUCCESS` or `FAILED`. Once a transaction reaches a terminal state, the monitoring worker records it and does not re-poll.
- **Evidence**: Raw RPC JSON response and all available base64 XDR fields (`envelopeXdr`, `resultXdr`, `resultMetaXdr`, `diagnosticEventsXdr`) stored in the database for audit and debugging.

## Conflicts & Trade-offs

| Decision | Alternative | Rationale |
|---|---|---|
| Poll-based monitoring over streaming | WebSocket/subscription | Simpler architecture; avoids needing persistent connections; aligned with bounded RPC retention. |
| `NOT_FOUND` does not = pending | Treat `NOT_FOUND` as "unknown" | Prevents incorrect assumptions about transaction liveness; aligns with domain rules. |
| Single worker process for MVP | Multi-worker with queue | Keeps MVP simple; worker coordination via PostgreSQL `FOR UPDATE SKIP LOCKED` can be added later. |
| Preserve raw XDR over decoded diagnostics | Discard raw XDR after decoding | Raw XDR is always available; decoded diagnostics may disappear with RPC retention. |