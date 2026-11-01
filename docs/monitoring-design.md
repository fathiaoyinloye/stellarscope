# StellarScope — Monitoring Design

## 1. Monitoring Lifecycle

Each registered transaction follows this lifecycle:

```text
[registered] --(worker claims)--> [processing] --(poll getTransaction)-->
  ├── [SUCCESS] --(record terminal)--> [done]
  ├── [FAILED] --(record terminal)--> [done]
  ├── [NOT_FOUND] --(continue polling)--> ... (repeat) --(observation deadline)--> [observed]
  └── [RPC error] --(retry)--> [processing] --(poll)--> ...
```

### States

| State | Meaning |
|---|---|
| `pending` | Transaction hash registered; not yet claimed by a worker. |
| `processing` | Worker has claimed the job and is polling `getTransaction`. |
| `done` | Terminal state recorded: `SUCCESS` or `FAILED`. Raw RPC response and XDR preserved. |
| `observed` | Observation deadline reached; monitoring stopped. Status may be unknown, `pending`, `processing`, or a previously recorded terminal state that was not overwritten. |

### Transitions

- `pending` → `processing`: Worker claims job via `SELECT ... FOR UPDATE SKIP LOCKED`.
- `processing` → `done`: `getTransaction` returns `SUCCESS` or `FAILED`.
- `processing` → `observed`: Observation deadline reached without terminal state.
- `done` → (no outgoing transitions): Terminal state; never overwritten.
- `observed` → (no outgoing transitions): Final state; monitoring ceased.

### Do not overwrite rule

> **Once a transaction has a terminal result (`SUCCESS` or `FAILED`), do not overwrite it with a later `NOT_FOUND` response.**

If a transaction is `done` and a subsequent `getTransaction` returns `NOT_FOUND`, the existing `done` status must remain unchanged. Only new registrations start from `pending`.

## 2. Polling Schedule & Retry Policy

### Polling interval schedule

| Attempt | Interval | Cumulative time |
|---|---|---|
| 1 | 1 second | 1s |
| 2 | 1 second | 2s |
| 3 | 1 second | 3s |
| ... | ... | ... |
| 30 | 1 second | 30s |
| 31 | 5 seconds | 35s + 5s |
| 32 | 5 seconds | 40s + 5s |
| ... | ... | ... |
| 60 | 5 seconds | ~30s + 30*5s = 180s |
| 61 | 10 seconds | ~180s + 10s |
| ... | ... | ... |
| 100 | 10 seconds | ~180s + 40*10s = 580s (~10 min) |

**Rationale**: Aggressive initial polling (1s for 30 attempts) detects quickly rising transactions. After 30s, interval steps up to 5s, then 10s, reducing RPC load while still detecting terminal states in reasonable time.

### Exponential backoff on RPC errors

On `stellar-rpc` errors (network failures, `-32600`, `-32003`):

- Initial retry interval: 1 second
- Backoff factor: 2x
- Maximum interval: 30 seconds
- Jitter: ±25% of calculated interval (to avoid thundering herd if multiple workers restart simultaneously)

Example sequence: 1s, 2s, 4s, 8s, 16s, 30s, 30s, 30s, ...

**Do not** update the transaction status on RPC errors. Retry until a valid response (`SUCCESS`, `FAILED`, or `NOT_FOUND`) is received.

### Retry limits

- Maximum retry attempts: 20 consecutive RPC errors.
- After 20 errors, log warning and stop polling this transaction temporarily.
- The transaction remains in `processing` status; a later manual or scheduled recovery can re-attempt.
- This limit prevents infinite loops on persistently broken RPC endpoints.

## 3. Observation Deadline

### Definition

The **observation deadline** is a per-transaction timeout after which the background worker stops polling.

- Computed as: `first_poll_timestamp + max_monitoring_minutes`
- Default `max_monitoring_minutes`: 60 minutes (configurable via `MONITORING_MAX_MINUTES` env var).
- After the deadline, the worker must **stop polling** this specific transaction.

### What observation deadline does NOT mean

- It does **not** mean the Stellar transaction has expired or been confirmed.
- It does **not** mean the transaction is pending, never existed, or aged out.
- It does **not** override a previously recorded terminal result (`SUCCESS`/`FAILED`).

### Behavior after observation deadline

1. Worker stops polling this transaction.
2. If the transaction has a terminal result (`SUCCESS`/`FAILED`), leave it as `done`.
3. If the terminal state has not yet been observed, set status to `observed` with a note that the observation deadline was reached.
4. The transaction can be re-registered later if monitoring beyond the original deadline is desired.

### Per-transaction vs global deadline

- **Per-transaction**: Each transaction has its own observation deadline counted from its first poll.
- **Global watchdog**: A separate periodic task can scan for transactions stuck in `processing` for too long (e.g., > 65 minutes) and force-set them to `observed`. This is a safety net, not the primary mechanism.

## 4. Background Worker Coordination

### Claiming model

The background worker uses PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` for exactly-once job claiming.

### Worker main loop (simplified)

```python
while True:
    # Claim a batch of pending jobs
    jobs = claim_pending_jobs(batch_size=10, worker_id=my_id)
    
    for job in jobs:
        poll_and_update(job.id, job.transaction_hash, worker_id)
    
    # Release claims (commit transaction)
    # Jobs remain in 'processing' state; next poll cycle will re-claim
    
    sleep(POLL_INTERVAL)  # e.g., 5 seconds
```

### Claim function (atomic)

```sql
WITH candidate AS (
    SELECT id, transaction_hash
    FROM monitoring_jobs
    WHERE status = 'pending'
    ORDER BY created_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT $1
)
UPDATE monitoring_jobs j
SET status = 'processing',
    locked_at = now(),
    locked_by = $2,
    attempts = attempts + 1
FROM candidate
WHERE j.id = candidate.id
RETURNING id, transaction_hash;
```

### Lease expiration & recovery

- `locked_at` records claim timestamp.
- `locked_by` identifies the worker (e.g., `hostname:pid`).
- A configurable lease timeout (default: 3 minutes) determines when a stale lock should be released:

```sql
UPDATE monitoring_jobs
SET status = 'pending', locked_at = NULL, locked_by = NULL
WHERE status = 'processing'
  AND locked_at < now() - interval '3 minutes'
  AND locked_by = $1;
```

- This cleanup can run as part of each worker's cycle or as a separate scheduled task.

### Crash recovery

- If a worker crashes mid-processing, the transaction remains `processing` with `locked_at` set.
- The lease timeout will eventually expire, releasing the lock.
- The next worker cycle will claim the now-`pending` job.
- No manual intervention required for normal crash recovery.

### Idempotency

- `transaction_hash UNIQUE` constraint prevents duplicate registrations.
- If a worker processes a transaction and the result is `SUCCESS`/`FAILED`, the database already has the terminal state; subsequent claims for the same hash should find it `done` and skip polling.
- All RPC responses are preserved; no data loss on reprocessing.

## 5. Database Schema (minimal)

```sql
CREATE TABLE monitoring_jobs (
    id BIGSERIAL PRIMARY KEY,
    transaction_hash CHAR(64) NOT NULL UNIQUE,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',  -- pending, processing, done, observed
    status_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    observed_at TIMESTAMPTZ,
    locked_at TIMESTAMPTZ,
    locked_by VARCHAR(128),
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Evidence fields (raw RPC response)
    raw_rpc_response JSONB,
    envelope_xdr TEXT,
    result_xdr TEXT,
    result_meta_xdr TEXT,
    diagnostic_events_xdr TEXT[],  -- base64-encoded, may be empty
    ledger INTEGER,
    created_at_ledger TIMESTAMPTZ,
    fee_bump BOOLEAN,
    failure_reason TEXT  -- optional, from diagnostic events or language
);
```

### Indexes for worker performance

```sql
-- Fast claim: find pending jobs, ordered by creation time
CREATE INDEX idx_monitoring_jobs_pending ON monitoring_jobs(status, created_at)
    WHERE status = 'pending';

-- Stale lease recovery
CREATE INDEX idx_monitoring_jobs_stale ON monitoring_jobs(status, locked_at)
    WHERE status = 'processing';

-- Lookup by hash (for API queries)
CREATE INDEX idx_monitoring_jobs_hash ON monitoring_jobs(transaction_hash);
```

## 6. API Endpoints (design summary)

| Endpoint | Method | Description |
|---|---|---|
| `POST /api/v1/register` | Register a transaction hash for monitoring. Creates a `pending` job. |
| `GET /api/v1/transactions/{hash}` | Retrieve monitoring status and evidence for a hash. |
| `GET /api/v1/transactions` | List all transactions with pagination. |
| `POST /api/v1/transactions/{hash}/retry` | Manually re-poll a specific transaction (e.g., after observation deadline). |

(Endpoints will be implemented in Phase 2; this is design summary for MVP.)

## 7. Key Design Principles

1. **Preserve raw evidence**: Always store the full RPC JSON response and all available base64 XDR fields. Do not discard any data.
2. **Never overwrite terminal results**: Once `done`, status stays `done` regardless of later `getTransaction` responses.
3. **Separate RPC status from monitoring state**: `SUCCESS`/`FAILED`/`NOT_FOUND` from RPC are distinct from `pending`/`processing`/`done`/`observed` in the database.
4. **Lease-based crash recovery**: Use `locked_at` + timeout; no separate queue service needed.
5. **Observation deadline is not an on-chain expiry**: The deadline reflects StellarScope's monitoring scope, not the transaction's on-chain state.
6. **Testnet only**: All monitoring, retention windows, and RPC behavior are calibrated for Stellar Testnet.