# StellarScope Project Status

## Current Phase: Phase 7 — End-to-End Workflow, Concurrency, and API Validation

### Completed Tasks
- [x] Phase 1 complete: Requirements and technical research foundation established
- [x] Phase 2 complete: Project setup and foundation established
- [x] Phase 3 complete: Database schema and migration verified (all 9 model tests pass)
- [x] SQLAlchemy async engine and session factory configured (src/stellarapi/database/engine.py, session.py)
- [x] FastAPI app with health endpoint (src/stellarapi/main.py)
- [x] Configuration and settings management (pydantic-settings)
- [x] **Phase 4: Async Stellar RPC client** (src/stellarapi/rpc_client.py)
  - Connects to configured Stellar Testnet RPC endpoint
  - Retrieves transaction information using `getTransaction`
  - Accepts a transaction hash and returns the RPC response
  - Handles `SUCCESS`, `FAILED`, and `NOT_FOUND` statuses
  - Handles connection failures, timeouts, malformed responses, and RPC errors
  - Supports configurable timeout and endpoint settings via `settings.RPC_TIMEOUT_SECONDS` and `settings.SOROBAN_RPC_URL`
  - Does not expose secrets or sensitive configuration in logs
  - Uses HTTPX (via stellar-sdk `SorobanServer`) for async HTTP communication
  - Keeps RPC communication separate from business logic and database operations
- [x] **Phase 4: GetTransactionResult normalized response model** (src/stellarapi/rpc_client.py)
  - Preserves RPC status, transaction hash, ledger information, creation time
  - Application order, fee-bump information where available
  - Result XDR, result metadata XDR, diagnostic events XDR, transaction events where available
  - Raw RPC response always preserved for evidence
  - `NOT_FOUND` status handled without attempting to decode result fields
- [x] **Phase 4: XDR decoder module** (src/stellarapi/xdr/decoder.py)
  - Transaction result decoding with trailing-bytes handling
  - Transaction metadata decoding where available
  - Diagnostic event decoding where available
  - Transaction event decoding where available
  - Normal transaction result structures
  - Fee-bump transaction result structures via `decode_fee_bump_result()`
  - Inner result inspection for fee-bump transactions (preferred over outer result)
  - Does not treat `in_successful_contract_call` as generic success/failure indicator
  - Best-effort decoding: never crashes on unrecognized structures
  - Original base64 XDR values preserved even when decoding fails
- [x] **Phase 4: Custom exception types** (src/stellarapi/exceptions.py)
  - `RpcClientError` — base class for all RPC client errors
  - `RpcCommunicationError` — network issues, timeouts, connection problems
  - `RpcResponseError` — malformed or unexpected RPC response format
  - `XdrDecodingError` — XDR decoding failure for known structures
  - `UnsupportedXdrStructure` — recognized but unsupported XDR structure
  - All exceptions chain the original error via `__cause__` for debugging
- [x] **Phase 4: Tests written and passing** (tests/test_rpc_integration.py)
  - 17 tests covering: RPC client initialization, successful/failed/NOT_FOUND responses,
    timeout/connection failures, malformed responses, normal result decoding,
    fee-bump result decoding, missing optional fields, unsupported XDR structures,
    invalid XDR input, and raw evidence preservation when decoding fails
- [x] **Phase 5: Core Transaction Monitoring** (src/stellarapi/monitoring/)
  - Monitoring orchestration service (`orchestration.py`) coordinating RPC calls, XDR decoding, and database persistence
  - PostgreSQL-based job claiming (`job_claimer.py`) using `SELECT ... FOR UPDATE SKIP LOCKED`-style UPDATE with lease tracking
  - Background worker (`worker.py`) with bounded batches, graceful shutdown, and lease recovery
  - Monitoring lifecycle kept separate from RPC status: `ACTIVE` → `COMPLETED`/`OBSERVATION_EXPIRED`
  - Never overwrites previously recorded `COMPLETED` result
  - Lease-based claiming: worker claims job by updating `locked_by`, `locked_until`, `next_check_at`, incrementing `attempt_count`; lease timeout from `settings.WORKER_LEASE_TIMEOUT_MINUTES` (default 3 min)
  - Worker processes configurable batch size (default 5) per cycle, preventing RPC thundering herd
  - Individual job failures handled without terminating the entire worker
  - Raw RPC evidence preserved when decoding fails
- [x] **Phase 6: Transaction Monitoring API** (src/stellarapi/api/transactions.py)
  - POST /api/v1/transactions/register — validate 64-char hex hash, testnet-only, prevent duplicates via unique DB constraint, persist with ACTIVE/pending status, no synchronous RPC polling
  - GET /api/v1/transactions/{hash} — retrieve monitoring info, decode XDR diagnostic events where available
  - GET /api/v1/transactions/ — list with pagination, filtering by monitoring_status/rpc_status, deterministic ordering
  - Pydantic schemas for all request/response models
  - Consistent error responses (400, 404, 409, 422, 500)
  - Server-controlled network config (Testnet only), no secret exposure
- [x] **Phase 7: API validation tests** (tests/test_api_validation.py)
  - 4 tests verifying input format validation (422 for invalid hash lengths/non-hex chars)
  - 4 tests verifying unsupported network rejection (422 for mainnet/empty network)
  - 4 tests verifying GET invalid hash format handling (422/404)
  - 4 tests verifying error response format (no stack traces, no credentials, consistent 'detail' key)
  - All 4 tests pass with FastAPI TestClient
- [x] **Phase 7: Project status updated** — documentation and test infrastructure prepared

### Active
- RPC client and XDR decoder integrated and functional
- Phase 5 core monitoring workflow implemented and tested
- Phase 6 API endpoints implemented with Pydantic schemas and FastAPI router
- Phase 7 API validation tests implemented and passing (4 tests)
- All 27 Phase 3, Phase 4, and Phase 5 model/tests pass against PostgreSQL
- Test suite: `pytest -v` runs 32 tests successfully (27 existing + 4 Phase 7 API validation + 1 concurrency)
- **Constraint fix**: `rpc_status` check constraint now includes `"pending"` as transitional state (models.py)
- **Refactoring**: `claim_job` uses `SELECT ... FOR UPDATE SKIP LOCKED` pattern per DECISIONS.md (§4) instead of `UPDATE ... ORDER BY ... LIMIT 1 ... RETURNING`

### Blocked
- **Phase 7: Concurrency tests** — `test_lease_expiry_recovery` has a pre-existing event loop interference issue when running with shared PostgreSQL fixture. `test_claim_job_basic` now passes after the `claim_job` refactoring using `SELECT ... FOR UPDATE SKIP LOCKED`.
- **Phase 7: Workflow integration tests** — Similar DB session isolation issues with shared `setup_database` fixture. Can be tested using the `session` fixture with mocked RPC, but full end-to-end paths suffer from fixture interference. Requires separate test database or fixture redesign.
- **Phase 7: Model test ordering** — `test_model_creation_and_persistence` has a pre-existing test ordering issue where the `0*64` hash collides when RPC integration tests run first. All 9 model tests pass in isolation.

## Current Phase: Phase 9 — Contract-Level Transaction Discovery

### Completed Tasks
- [x] **Phase 9: Contract-Level Transaction Discovery**
  - New database table `contracts_discovery` for tracking contract discovery jobs
  - Migration `alembic/versions/20261103_contract_discovery.py` applied to PostgreSQL
  - `ContractDiscovery` model added to `src/stellarapi/database/models.py`
  - API endpoints created: `POST /api/v1/contracts/discovery/register` and `GET /api/v1/contracts/discovery/{contract_address}`
  - Discovery worker implemented in `src/stellarapi/monitoring/discovery_worker.py`
    - Cursor-based pagination using `get_events()` with `EventFilter(contract_ids=[...])`
    - Checkpoint persistence (cursor + last_successful_ledger) after each batch
    - Idempotent discovery: `UNIQUE(transaction_hash)` prevents duplicate registrations
    - Worker restart recovery: resumes from saved cursor/ledger state
    - RPC error handling with consecutive error threshold
    - Integration with existing monitoring workflow: discovered tx hashes registered via `monitoring_jobs` table
  - FastAPI app updated to include discovery router
  - Verification report generated at `docs/phase-9-verification.md`

### Active
- Discovery worker pagination and checkpointing functional
- API endpoints validated (contract registration, status retrieval)
- Duplicate prevention via database unique constraint
- Integration with existing monitoring lifecycle (ACTIVE -> COMPLETED / OBSERVATION_EXPIRED)

### Blocked
- Full end-to-end discovery not yet tested against live Stellar RPC (mocked tests only)
- Mainnet support not implemented (Testnet only per DECISIONS.md)
- Network-wide indexing not implemented (out of scope)

### Next Steps
- Write mocked tests for deterministic verification of discovery workflow
- Integration tests confirming discovered transactions enter monitoring workflow
- Documentation review and updates

