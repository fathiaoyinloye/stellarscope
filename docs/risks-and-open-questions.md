# StellarScope — Risks and Open Questions

## 1. RPC Retention Window Uncertainty

### Description
The default Stellar RPC history-retention-window is ~7 days (120960 ledgers). Transactions older than this may return `NOT_FOUND` from `getTransaction`, even if they were originally `SUCCESS` or `FAILED`. The exact window depends on the RPC operator's configuration.

### Impact
- `NOT_FOUND` may be mistaken for "transaction no longer exists" when it is actually "outside retention window."
- Operators may configure shorter windows; users on such instances will see `NOT_FOUND` sooner.

### Mitigation
- Call `getHealth()` periodically to check `oldest_ledger` and `ledger_retention_window`.
- Document the retention window as a known limitation in the UI/API.
- For production, operators should configure a private Soroban RPC with a larger retention window or integrate with Hubble (SDF's BigQuery dataset).

### Status: **Verified** — RPC retention is bounded and configurable. Must check `getHealth()`.

---

## 2. `NOT_FOUND` Semantics Ambiguity

### Description
`NOT_FOUND` from `getTransaction` does not, by itself, prove that:
- The transaction is pending (still being processed).
- The transaction never existed.
- The transaction has aged out of the network.

It may simply mean the transaction is outside the RPC's retention window, or the RPC node does not have it in its in-memory store.

### Impact
- Incorrect assumptions about transaction liveness could lead to wrong monitoring behavior.
- If StellarScope treats `NOT_FOUND` as "pending," it may poll indefinitely. If it treats it as "failed," it may incorrectly record a failure.

### Mitigation
- Per domain rules: `NOT_FOUND` does not prove pending/never existed/aged out.
- Do not overwrite a previously recorded terminal result (`SUCCESS`/`FAILED`) with `NOT_FOUND`.
- Preserve the raw RPC response and note `latestLedger`/`latestLedgerCloseTime` for context.
- Use `getHealth()` to determine if the transaction's ledger is outside the retention window.

### Status: **Verified** — Per domain rules and Stellar RPC spec. Explicitly documented in requirements and monitoring design.

---

## 3. Diagnostic Events Availability

### Description
`diagnosticEventsXdr` is only present in `getTransaction` response when the RPC server has `ENABLE_SOROBAN_DIAGNOSTIC_EVENTS` enabled. Even when present, the events may not cover all failure modes, and human-readable failure explanations are not guaranteed.

### Impact
- Failure reasons may be unavailable or incomplete.
- Debugging failed transactions may be harder without diagnostic events.

### Mitigation
- Always preserve raw `diagnosticEventsXdr` and `resultXdr` in the database.
- Do not guarantee that diagnostic events or contract events will be available for any given transaction.
- Log a warning when `diagnosticEventsXdr` is absent from the RPC response.
- Consider diagnostic events as supplementary evidence, not definitive failure explanations.

### Status: **Verified** — Per Stellar RPC documentation. Documented in technical-research.md and monitoring-design.md.

---

## 4. Fee-Bump Inner Result Decoding

### Description
For fee-bump transactions, the `resultXdr` contains an `InnerTransactionResultPair` with codes `txFEE_BUMP_INNER_SUCCESS` or `txFEE_BUMP_INNER_FAILED`. Decoding this requires XDR parsing with `stellar-sdk`. If the SDK version or XDR structure changes, decoding may fail.

### Impact
- Inability to determine whether a fee-bump transaction's inner transaction succeeded or failed.
- May result in incomplete status tracking for fee-bump transactions.

### Mitigation
- Decode `resultXdr` using `stellar-sdk` XDR support at registration time; preserve raw XDR as fallback.
- If XDR decoding fails, log the raw base64 and mark the fee-bump outcome as "unknown."
- Test XDR decoding against known fee-bump transactions on Testnet during development.

### Status: **Verified** — XDR structure is well-defined; decoding via stellar-sdk is the recommended approach. Documented in technical-research.md.

---

## 5. Worker Coordination with `FOR UPDATE SKIP LOCKED`

### Description
The `SELECT ... FOR UPDATE SKIP LOCKED` pattern for worker job claiming is well-understood in PostgreSQL, but several operational edges exist:

- **Row bloat**: Repeated `UPDATE` operations (pending → processing → done) cause MVCC row bloat. Vacuum settings may need tuning.
- **Long-running transactions**: If a worker's transaction holds locks for too long, other workers may be starved of jobs.
- **NOWAIT vs SKIP LOCKED**: `NOWAIT` raises an error if a row is locked; `SKIP LOCKED` skips it. The choice affects worker contention.
- **Deterministic ordering**: `ORDER BY` with `FOR UPDATE SKIP LOCKED` is not guaranteed deterministic if ties exist; use additional tie-breakers (e.g., `id`).

### Impact
- Lost jobs or duplicate processing if the pattern is misapplied.
- Stale jobs left `processing` after worker crashes (mitigated by lease timeout).
- Unpredictable job ordering under contention.

### Mitigation
- Use `WITH ... FOR UPDATE SKIP LOCKED` + `UPDATE ... FROM candidate` pattern for atomic claim + status update.
- Set a lease timeout (default 3 minutes) for stale lock recovery.
- Always use `ORDER BY created_at, id` for deterministic claiming.
- Tune PostgreSQL `autovacuum` for the `monitoring_jobs` table.
- Test with concurrent workers before production deployment.

### Status: **Verified** — Pattern is well-documented; operational edges are documented and mitigated.

---

## 6. Observation Deadline =/= Transaction Expiry

### Description
The observation deadline is a StellarScope-internal timeout; it does not mean the Stellar transaction has expired, been confirmed, or aged out. This semantic is subtle and may be misunderstood by operators.

### Impact
- Operators may incorrectly assume a transaction is "no longer monitored = confirmed/expired."
- May lead to premature un-registration or misinterpretation of `observed` status.

### Mitigation
- Clearly document in the API and CLI that `observed` means "StellarScope stopped monitoring," not "transaction expired."
- Include `observation_deadline` and `last_poll_at` in API responses for transparency.
- Do not allow the API to be used as a "transaction expiry service."

### Status: **Verified** — Domain rule explicitly stated. Documented in monitoring-design.md.

---

## 7. RPC Error vs Transaction Failure

### Description
RPC errors (network failures, `-32600`, `-32003`, etc.) must not be treated as transaction failures (`FAILED`). This is a common source of bugs in transaction polling systems.

### Impact
- If RPC errors are recorded as `FAILED`, legitimate transactions may be incorrectly marked as failed.
- Network blips could cause false failure reports.

### Mitigation
- Distinguish RPC errors from `getTransaction` response statuses:
  - RPC error (exception from HTTP client) → retry with backoff; do not update status.
  - `getTransaction` returns `SUCCESS` → record terminal result.
  - `getTransaction` returns `FAILED` → record terminal result.
  - `getTransaction` returns `NOT_FOUND` → continue polling (unless observation deadline).
- Implement a `is_rpc_error()` check in the polling logic; only update status on valid `SUCCESS`/`FAILED`/`NOT_FOUND` responses.

### Status: **Verified** — Explicit domain rule. Will be implemented in the polling logic.

---

## 8. `NOT_FOUND` After Terminal Result

### Description
If a transaction has been recorded as `SUCCESS` or `FAILED`, and a subsequent `getTransaction` call returns `NOT_FOUND`, the system must not overwrite the existing terminal result.

### Impact
- Data corruption if later `NOT_FOUND` overwrites a valid `SUCCESS`/`FAILED`.
- Loss of recorded evidence.

### Mitigation
- Enforce "do not overwrite" rule in the polling logic: if current status is `done`, skip polling and keep `done`.
- Log when `NOT_FOUND` is received for a `done` transaction (informational only; no state change).
- Preserve the original `done` result's raw RPC response and XDR as historical evidence.

### Status: **Verified** — Explicit domain rule. Will be enforced in software.

---

## 9. Horizon vs RPC Data Consistency

### Description
The MVP uses Soroban RPC `getTransaction`, not Horizon. Horizon provides classic transaction data; Soroban RPC provides smart contract transaction data. The data models differ, and some fields available in Horizon may not be available in RPC vice versa.

### Impact
- Certain diagnostic or metadata fields from Horizon may be absent in RPC responses.
- Assumptions about field parity may lead to missing data.

### Mitigation
- Use RPC `getTransaction` as the primary data source for Soroban transactions.
- Do not assume Horizon-like fields are present in RPC responses.
- If Horizon data is needed for any reason, query it separately (out of MVP scope).
- Document the data model difference in the technical research.

### Status: **Verified** — MVP uses RPC exclusively for Soroban transactions. Documented in technical-research.md.

---

## 10. Open Questions (Requiring Testnet Verification)

| # | Question | Why it matters | Proposed verification |
|---|---|---|---|
| 1 | How long does `NOT_FOUND` persist after a transaction's ledger closes? | Determines how long we should keep polling before observing expiry. | Poll a known `SUCCESS` transaction over several hours/days on Testnet; record when `NOT_FOUND` first appears. |
| 2 | Does `diagnosticEventsXdr` presence correlate with `ENABLE_SOROBAN_DIAGNOSTIC_EVENTS` server config? | Helps predict when diagnostic events will be available. | Check multiple RPC nodes with different configs; observe `diagnosticEventsXdr` presence/absence. |
| 3 | What is the typical ledger cadence on Testnet, and how does it map to the 7-day retention window? | Helps estimate how many ledgers ~7 days corresponds to. | Measure ledger intervals on soroban-testnet.stellar.org; compute ledgers per hour/day. |
| 4 | Does the `events` field in `getTransaction` contain the same data as `contractEventsXdr`? | Avoids duplicate parsing and incorrect assumptions. | Compare `events` and `contractEventsXdr` on several SUCCESS/FAILED transactions. |
| 5 | How does the `feeBump` field behave for fee-bump transactions in `getTransaction`? | Needed to identify and correctly decode fee-bump transactions. | Submit fee-bump transactions on Testnet; observe `feeBump` field and `resultXdr` structure. |
| 6 | What happens at the exact boundary of the retention window? | Determines if `NOT_FOUND` is immediate or gradual at the window edge. | Monitor a known transaction as it ages past the retention window; observe `getTransaction` behavior. |
| 7 | Does `getHealth()` `oldest_ledger` stay constant, or does it advance? | Affects how we compute "expired at" timestamps. | Poll `getHealth()` at intervals over several hours/days; track `oldest_ledger` changes. |
| 8 | Are `resultMetaXdr` and `diagnosticEventsXdr` both present only when `ENABLE_SOROBAN_DIAGNOSTIC_EVENTS` is on, or independently? | Impacts what we expect to find in a given RPC response. | Query `getTransaction` on nodes with/without diagnostic events enabled; compare responses. |

### Status: **Open** — These require live Testnet verification. Documented here for tracking; will be addressed during Phase 2 implementation and testing.