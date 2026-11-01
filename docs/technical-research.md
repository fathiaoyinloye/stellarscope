# StellarScope — Technical Research

## 1. Stellar RPC `getTransaction` Response

The Stellar RPC `getTransaction` endpoint returns a response with a `status` field that is one of three values:

- `SUCCESS` — transaction was included in a ledger and executed without errors.
- `FAILED` — transaction was included in a ledger but execution errors occurred.
- `NOT_FOUND` — transaction was not found in Soroban-RPC's transaction store.

### Fields present when `status` is `SUCCESS` or `FAILED`

| Field | Type | Description |
|---|---|---|
| `ledger` | `number` | Ledger sequence that included the transaction. |
| `createdAt` | `string` (UNIX timestamp) | When the transaction was included in the ledger. |
| `applicationOrder` | `number` | Index of the transaction among all transactions in the ledger. |
| `feeBump` | `boolean` | Whether the transaction was fee-bumped. |
| `envelopeXdr` | `string` (base64) | Raw `TransactionEnvelope` XDR. |
| `resultXdr` | `string` (base64) | Raw `TransactionResult` XDR. |
| `resultMetaXdr` | `string` (base64) | Raw `TransactionMeta` XDR. |
| `diagnosticEventsXdr` | `array[string]` (base64) | Diagnostic events — only present if `ENABLE_SOROBAN_DIAGNOSTIC_EVENTS` is enabled on the RPC server. |
| `events` | `object` | All events emitted during transaction execution. May be incomplete or absent. |
| `contractEventsXdr` | `array(array[string])` | Contract events — each inner array is base64-encoded `xdr.ContractEvent`. |

### `NOT_FOUND` response

When status is `NOT_FOUND`, the response contains only:

- `status`: `"NOT_FOUND"`
- `latestLedger`: latest known ledger number.
- `latestLedgerCloseTime`: latest ledger close timestamp.

No `ledger`, `createdAt`, `applicationOrder`, `feeBump`, `envelopeXdr`, `resultXdr`, `resultMetaXdr`, `diagnosticEventsXdr`, or `events` fields are present.

### Key implications

- `NOT_FOUND` does **not** prove the transaction is pending, never existed, or has aged out of the network. It may simply be outside the RPC's retention window.
- RPC errors (e.g., network failures, `-32600`/`-32003` errors) must **not** be treated as transaction failures. They are infrastructure errors and should trigger retry logic, not status updates.
- The `resultXdr` and `resultMetaXdr` fields are **only** present when `status` is `SUCCESS` or `FAILED`. If they are absent, the transaction is either `NOT_FOUND` or the RPC errored.

## 2. XDR Decoding with `stellar-sdk` (py-stellar-base)

### TransactionResult XDR structure

The `resultXdr` field contains a base64-encoded `TransactionResult` XDR struct. Using `stellar-sdk` (py-stellar-base), it can be decoded as:

```python
from stellar_sdk import xdr
import base64

result = xdr.TransactionResult.from_xdr(base64.b64decode(envelope_xdr_bytes))
```

The `TransactionResult` XDR has a union switch on `TransactionResultCode`:

| Code | Meaning |
|---|---|
| `txFEE_BUMP_INNER_SUCCESS` (1) | Fee-bump inner transaction succeeded. |
| `txSUCCESS` (0) | All operations succeeded. |
| `txFAILED` (-1) | One or more operations failed (none were applied). |
| `txFEE_BUMP_INNER_FAILED` (-13) | Fee-bump inner transaction failed. |
| `txTOO_EARLY` (-2), `txTOO_LATE` (-3), etc. | Precondition violations. |

### Decoding `feeCharged`

`TransactionResult.feeCharged` is an `int64` indicating the actual fee charged for the transaction (in stroops). This is useful for verifying that the fee paid matches expectations.

### Fee-bump transaction handling

For fee-bump transactions, the `TransactionResult` contains an `InnerTransactionResultPair` when the code is `txFEE_BUMP_INNER_SUCCESS` or `txFEE_BUMP_INNER_FAILED`. The inner result is exactly what would have been produced had there been no fee-bump transaction (inner fee is always 0).

**Important**: StellarScope should inspect the inner transaction result by decoding the `resultXdr` XDR rather than assuming the normal `TransactionResult` structure. The inner result can be accessed via the XDR union.

### `diagnosticEventsXdr`

- Decoded from base64 into `xdr.DiagnosticEvent` structs.
- Only present if the RPC server has `ENABLE_SOROBAN_DIAGNOSTIC_EVENTS` enabled.
- Even when present, the number and content of diagnostic events are not guaranteed to cover all failure modes.
- Failure explanations in human-readable form may not be available; preserve raw XDR as evidence.

### `contractEventsXdr`

- Nested array of base64-encoded `xdr.ContractEvent`.
- Each inner array represents contract events from a single operation.
- May be useful for post-mortem analysis but is not guaranteed to be present.

## 3. Transaction Result Handling

### Normal transactions

- `SUCCESS`: All operations applied. `resultXdr` decodes to `txSUCCESS`. `feeCharged` is present.
- `FAILED`: At least one operation failed. `resultXdr` decodes to `txFAILED`. `feeCharged` is present (may be higher than base fee due to failed operations).

### Fee-bump transactions

- The outer `TransactionResult` code will be `txFEE_BUMP_INNER_SUCCESS` or `txFEE_BUMP_INNER_FAILED`.
- The inner transaction result is contained within the same `TransactionResult` XDR union.
- **Do not assume the normal `TransactionResult` structure** for fee-bump transactions; inspect the inner result via XDR decoding.
- The inner result's `feeCharged` is always 0.
- The inner result's `txSUCCESS`/`txFAILED` code indicates the outcome of the inner transaction.

### `NOT_FOUND` handling

- When `getTransaction` returns `NOT_FOUND`, StellarScope must:
  1. Not overwrite a previously recorded terminal result (`SUCCESS` or `FAILED`) with `NOT_FOUND`.
  2. Not assume the transaction is pending or never existed.
  3. Preserve the `NOT_FOUND` response as a diagnostic event, noting the ledger range and retention window.
  4. Record the `latestLedger` and `latestLedgerCloseTime` from the `NOT_FOUND` response for context.

## 4. PostgreSQL Worker Coordination with `SELECT ... FOR UPDATE SKIP LOCKED`

### Rationale

The background monitoring worker needs to claim transactions for polling without race conditions when multiple worker instances are running. The pattern uses PostgreSQL row-level locks for exactly-once claim semantics.

### Schema design (proposed)

A `monitoring_jobs` table (or similar) tracks transactions eligible for polling:

```sql
CREATE TABLE monitoring_jobs (
    id BIGSERIAL PRIMARY KEY,
    transaction_hash CHAR(64) NOT NULL UNIQUE,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',  -- 'pending', 'processing', 'done'
    observed_at TIMESTAMPTZ,
    locked_at TIMESTAMPTZ,
    locked_by VARCHAR(128),  -- worker identifier (e.g., hostname or PID)
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Claim pattern (atomic claim + status update)

```sql
WITH candidate AS (
    SELECT id, transaction_hash
    FROM monitoring_jobs
    WHERE status = 'pending'
    ORDER BY created_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE monitoring_jobs j
SET
    status = 'processing',
    locked_at = now(),
    locked_by = $1,
    attempts = attempts + 1
FROM candidate
WHERE j.id = candidate.id
RETURNING j.transaction_hash, j.status;
```

### Lease-based crash recovery

To handle worker crashes without losing work:

- `locked_at` records when the job was claimed.
- `locked_by` identifies the worker (hostname + PID).
- A separate cleanup cycle periodically releases stale leases:

```sql
UPDATE monitoring_jobs
SET status = 'pending', locked_at = NULL, locked_by = NULL
WHERE status = 'processing'
  AND locked_at < now() - interval '1 minute'  -- configurable lease timeout
  AND locked_by = $1;  -- only release our own leases
```

### Idempotency

- The `transaction_hash UNIQUE` constraint ensures duplicate registrations are rejected.
- If a worker processes a transaction and the RPC later returns a different terminal status, the earlier terminal result must **not** be overwritten (per domain rule: "Do not overwrite a previously recorded terminal transaction result with a later `NOT_FOUND` response").
- All RPC responses (including raw XDR) are preserved in the database for audit.

### Batch claiming (performance optimization)

Instead of claiming one row at a time, the worker can claim a batch:

```sql
WITH candidates AS (
    SELECT id, transaction_hash
    FROM monitoring_jobs
    WHERE status = 'pending'
    ORDER BY created_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 20
)
UPDATE monitoring_jobs j
SET
    status = 'processing',
    locked_at = now(),
    locked_by = $1
FROM candidates
WHERE j.id = candidates.id;
```

## 5. RPC Retention Behavior

### Default retention window

- Stellar RPC maintains a bounded history of transactions and events.
- Default `history-retention-window`: 120960 ledgers (~7 days, depending on ledger cadence).
- Configurable via `HISTORY_RETENTION_WINDOW` on the RPC server.
- Can be checked via `getHealth()` endpoint, which returns `oldest_ledger`, `latest_ledger`, and `ledger_retention_window`.

### Implications for StellarScope

- Transactions older than the retention window will return `NOT_FOUND` from `getTransaction`, **even if** they were originally `SUCCESS` or `FAILED`.
- StellarScope must not interpret `NOT_FOUND` as "transaction failed/predicted" when it may be purely a retention-window issue.
- For production deployments requiring longer history, operators should configure a private Soroban RPC with a larger retention window or integrate with Hubble (SDF's BigQuery dataset).
- **MVP design**: Assume a ~7-day monitoring window per transaction. Beyond that, `NOT_FOUND` is expected and does not require alerting.

### `getHealth()` usage

- Periodically call `getHealth()` to check `oldest_ledger` and `ledger_retention_window`.
- Use this information to compute a per-transaction "expired at" timestamp.
- If a transaction's ledger is older than `oldest_ledger - ledger_retention_window`, mark it as expired (not failed, just expired).

## 6. Monitoring Lifecycle & Polling Policy

### Transaction monitoring lifecycle

1. **Register**: User submits a transaction hash via API; row created in `monitoring_jobs` with status `pending`.
2. **Claim**: Worker claims a pending job via `SELECT ... FOR UPDATE SKIP LOCKED`, sets status to `processing`.
3. **Poll**: Worker calls `getTransaction(tx_hash)`.
4. **Update**:
   - If `SUCCESS` or `FAILED`: Record terminal status, preserve raw RPC response and XDR, set status to `done`. Do not re-poll.
   - If `NOT_FOUND`: Leave status as `processing`; continue polling (unless observation deadline reached).
   - If RPC error: Retry; do not set status to `FAILED`.
5. **Release**: Worker commits; job remains `processing`. Next poll cycle claims it again.
6. **Observation deadline**: After N polling attempts or M minutes of monitoring, the worker stops polling this transaction. Status remains `processing` or is set to `observed_expiry`. This does NOT mean the transaction expired on-chain.

### Polling schedule

| Phase | Interval | Max Attempts |
|---|---|---|
| Initial detection | 1 second (first 30 seconds) | 30 attempts |
| Normal polling | 5 seconds | 100 attempts (approx 8 minutes) |
| Long-interval after expiry | 30 seconds | Until observation deadline |

### Retry behavior

- **RPC error (network, `-32600`, `-32003`)**: Retry with exponential backoff. Start at 1s, cap at 30s. Do not update transaction status on retry.
- **`NOT_FOUND`**: Not an error; continue polling. After observation deadline, stop and record `observed_expiry`.
- **`SUCCESS` / `FAILED`**: Terminal; stop polling, record result, do not re-poll.
- **Consecutive `NOT_FOUND` after previous SUCCESS/FAILED**: Do not overwrite the terminal result. Log and preserve evidence.

### Observation deadline

- Defined as: `first_poll_at + max_monitoring_minutes`.
- After the observation deadline, the worker must stop polling this specific transaction.
- The observation deadline is **not** a guarantee about the transaction's on-chain state. It merely indicates StellarScope has ceased monitoring.
- After observation expiry, the transaction status in the database should be set to `observed` (or remain `processing` with a note that the observation deadline was reached).
- The transaction can be re-registered later if monitoring is desired beyond the deadline.

## 7. Technical Risks & Unresolved Questions

| Risk | Impact | Mitigation |
|---|---|---|
| `NOT_FOUND` may be due to retention window, not actual absence | Incorrect assumptions about transaction liveness | Preserve raw RPC response; do not overwrite terminal results; log `latestLedger`/`latestLedgerCloseTime` for context. |
| Diagnostic events may be absent (diagnostic events disabled on RPC) | Missing failure explanation | Preserve raw `diagnosticEventsXdr` and `resultXdr`; do not guarantee human-readable failure reasons. |
| RPC retention window may be shorter than expected (e.g., < 7 days on some providers) | Transactions become `NOT_FOUND` sooner | Call `getHealth()` to dynamically check retention; configure private RPC with larger window for production. |
| Fee-bump inner result decoding may be incomplete without SDK XDR parsing | Difficulty determining fee-bump outcome | Decode `resultXdr` using `stellar-sdk` XDR support; preserve raw XDR as fallback. |
| Multiple worker processes racing on `FOR UPDATE SKIP LOCKED` | Lost jobs or duplicate processing | Test with concurrent workers; verify lease-based recovery pattern. |
| Worker crash during `processing` leaves stale lock | Job appears permanently claimed | Lease expiration + cleanup cycle; `locked_at` timeout mechanism. |
| `events` field may be incomplete or absent in `getTransaction` response | Missing event data for debugging | Preserve full RPC response; do not discard any field. |
| Soroban RPC URL availability and performance under load | Polling may be delayed or fail | Use high-quality RPC provider; implement retry with backoff; connection pooling. |
| Observing transactions beyond 7-day retention window | `NOT_FOUND` returned even for successful transactions | Out of MVP scope; document as known limitation; consider Hubble integration for future. |

## 8. Confirmed Technical Decisions

| Decision | Reason |
|---|---|
| `getTransaction` statuses: `SUCCESS`, `FAILED`, `NOT_FOUND` only | Per Stellar RPC spec; no `PENDING` status from RPC. |
| Keep RPC status separate from monitoring lifecycle | Monitoring state (pending/processing/done) is independent of RPC terminal status. |
| `SUCCESS` and `FAILED` are terminal | Once recorded, do not re-poll. |
| `NOT_FOUND` does not prove pending/never existed/aged out | Preserve raw response; do not overwrite terminal results. |
| Preserve raw RPC response and base64 XDR as evidence | XDR may become unavailable after retention window; raw JSON is always retained. |
| RPC errors ≠ transaction failures | Retry on RPC errors; do not set status to `FAILED`. |
| Handle fee-bump by inspecting inner result XDR | Do not assume normal `TransactionResult` structure. |
| Do not overwrite terminal result with later `NOT_FOUND` | Preservation of evidence and correctness. |
| PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` for worker coordination | No Redis/Celery; Postgres is already the data store. |
| Lease-based crash recovery with `locked_at` timeout | Handle worker crashes without losing jobs. |
| Observation deadline = StellarScope stopped monitoring, not transaction expired | Clear semantics for operators. |
| Testnet only for MVP | Aligns with project scope; Mainnet later. |