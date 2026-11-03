"""SQLAlchemy 2.x async database models for StellarScope.

This module defines the ``monitoring_jobs`` table and related models that support
the transaction registration, monitoring, evidence preservation, and worker
coordination requirements of the StellarScope MVP.

The schema is designed around a single ``monitoring_jobs`` table that captures
all required data while keeping the API surface minimal and the implementation
straightforward.  All columns use PostgreSQL‑specific types where they add value
(e.g. ``JSONB`` for raw RPC payloads, ``TIMESTAMP`` with ``timezone=True`` for
aware timestamps) and the model uses SQLAlchemy 2.x ``async`` features
``async_engine_from_config``, ``async_sessionmaker``) that are already
configured elsewhere in the package.
"""

from __future__ import annotations

import uuid
import sqlalchemy as sa

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Column,
    Integer,
    String,
    Text,
    TIMESTAMP,
    event,
)
from sqlalchemy.dialects.postgresql import (
    UUID as PostgreSQLUUID,
    JSONB,
    TIMESTAMP as PG_TIMESTAMP,
)
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from src.stellarapi.config.settings import settings

# ---------------------------------------------------------------------------
# Base model
# ---------------------------------------------------------------------------

Base = declarative_base()


# ---------------------------------------------------------------------------
# monitoring_jobs table model
# ---------------------------------------------------------------------------

class MonitoringJob(AsyncAttrs, Base):
    """Database model for a single Stellar transaction monitoring job.

    This table tracks the monitoring lifecycle of a registered Stellar/Soroban
    transaction hash.  It is designed to be used with an async SQLAlchemy engine
    and ``async_sessionmaker`` so that the background worker and FastAPI routes
    can both interact with the database without blocking.

    The monitoring lifecycle states are::

        ACTIVE   - Worker is polling ``getTransaction`` for this hash.
        COMPLETED - A terminal RPC status (SUCCESS/FAILED) has been recorded.
        OBSERVATION_EXPIRED - StellarScope stopped monitoring; a terminal
            result may or may not have been observed.

    RPC statuses (SUCCESS/FAILED/NOT_FOUND) are stored separately in
    ``rpc_status`` and are **never** used to overwrite a previously recorded
    ``monitoring_status = COMPLETED`` value.

    ``locked_by`` / ``locked_until`` support PostgreSQL‑native worker
    coordination via ``SELECT ... FOR UPDATE SKIP LOCKED`` without needing a
    separate queue service.

    .. warning::
        Do not add columns that duplicate information already present in the
        ``raw_rpc_response`` JSONB field.  If a field is not explicitly defined
        here, query the JSONB payload instead.
    """

    __tablename__ = "monitoring_jobs"

    # --------------------------------------------------------------------- #
    # Primary key
    # --------------------------------------------------------------------- #
    id = Column(
        PostgreSQLUUID(as_uuid=False),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        index=True,
        nullable=False,
    )

    # --------------------------------------------------------------------- #
    # Transaction identity
    # --------------------------------------------------------------------- #
    transaction_hash = Column(
        CHAR(64),
        nullable=False,
        unique=True,
        index=True,
        comment="64-character hex-encoded Stellar transaction hash",
    )

    network = Column(
        String(16),
        nullable=False,
        default="testnet",
        comment="Stellar network identifier (testnet | mainnet)",
    )

    # --------------------------------------------------------------------- #
    # RPC status — separate from monitoring lifecycle
    # --------------------------------------------------------------------- #
    rpc_status = Column(
        String(16),
        nullable=False,
        default="pending",
        comment="Latest Stellar RPC getTransaction status: SUCCESS | FAILED | NOT_FOUND | pending",
    )

    # --------------------------------------------------------------------- #
    # Monitoring lifecycle
    # --------------------------------------------------------------------- #
    monitoring_status = Column(
        String(32),
        nullable=False,
        default="ACTIVE",
        comment="StellarScope monitoring state: ACTIVE | COMPLETED | OBSERVATION_EXPIRED",
    )

    # --------------------------------------------------------------------- #
    # Raw evidence preservation
    # --------------------------------------------------------------------- #
    raw_rpc_response = Column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
        comment="Full preserved JSON‑RPC getTransaction response; all fields kept "
        "for audit and debugging.  Prefer querying this column over adding "
        "individual columns that may become stale.",
    )

    envelope_xdr = Column(
        Text,
        nullable=True,
        comment="base64‑encoded TransactionEnvelope XDR; present when RPC returns "
        "SUCCESS or FAILED.",
    )

    result_xdr = Column(
        Text,
        nullable=True,
        comment="base64‑encoded TransactionResult XDR; present when RPC returns "
        "SUCCESS or FAILED.  Decoded via stellar-sdk if needed.",
    )

    result_meta_xdr = Column(
        Text,
        nullable=True,
        comment="base64‑encoded TransactionMeta XDR; present when RPC returns "
        "SUCCESS or FAILED.",
    )

    # diagnostic_events_xdr removed - use raw_rpc_response JSONB instead

    # --------------------------------------------------------------------- #
    # Ledger / timing information
    # --------------------------------------------------------------------- #
    ledger = Column(
        Integer,
        nullable=True,
        comment="Ledger sequence number that included the transaction; present "
        "when rpc_status is SUCCESS or FAILED.",
    )

    created_at_ledger = Column(
        Integer,
        nullable=True,
        comment="Ledger number when the transaction was first registered/monitored.",
    )

    fee_bump = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="True when the transaction was fee‑bumped; the inner result must "
        "be inspected from result_xdr rather than assuming the normal structure.",
    )

    failure_reason = Column(
        Text,
        nullable=True,
        comment="Optional human‑readable failure explanation; may be populated "
        "from the JSONB payload when available but is not guaranteed.",
    )

    # --------------------------------------------------------------------- #
    # Worker coordination fields
    # --------------------------------------------------------------------- #
    locked_by = Column(
        String(128),
        nullable=True,
        comment="Identifier of the worker currently claiming this job "
        "(e.g. ``hostname:pid``).  ``NULL`` when the job is not locked.",
    )

    locked_until = Column(
        PG_TIMESTAMP(timezone=True),
        nullable=True,
        comment="Lease expiry time.  After this time the lock is considered "
        "stale and can be released by a cleanup cycle; used with "
        "SELECT ... FOR UPDATE SKIP LOCKED coordination.",
    )

    next_check_at = Column(
        PG_TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
        comment="Earliest timestamp at which a worker should next poll "
        "getTransaction for this hash.  The background worker must not poll "
        "before this time.",
    )

    attempt_count = Column(
        Integer,
        nullable=False,
        default=0,
        comment="Number of polling attempts made so far.  Incremented on each "
        "RPC retry (not on NOT_FOUND, which is not an error).",
    )

    # --------------------------------------------------------------------- #
    # Timestamps
    # --------------------------------------------------------------------- #
    created_at = Column(
        PG_TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
        comment="Row creation time (timezone‑aware).",
    )

    updated_at = Column(
        PG_TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
        onupdate=sa.func.now(),
        comment="Row last‑update time (timezone‑aware).",
    )

    # --------------------------------------------------------------------- #
    # Constraints
    # --------------------------------------------------------------------- #
    __table_args__ = (
        # Ensure monitoring_status is always one of the allowed values.
        CheckConstraint(
            "monitoring_status IN ('ACTIVE', 'COMPLETED', 'OBSERVATION_EXPIRED')",
            name="ck_monitoring_status_valid",
        ),
        # Ensure rpc_status is always one of the allowed values.
        # "pending" is included as a transitional state before the first RPC call;
        # the worker subsequently updates it to SUCCESS/FAILED/NOT_FOUND.
        CheckConstraint(
            "rpc_status IN ('SUCCESS', 'FAILED', 'NOT_FOUND', 'pending')",
            name="ck_rpc_status_valid",
        ),
        # Unique constraint on transaction hash
        sa.UniqueConstraint("transaction_hash", name="uq_monitoring_jobs_hash"),
    )

    # ----------------------------------------------------------------- #
    # Helper properties (read‑only, no side‑effects)
    # ----------------------------------------------------------------- #
    @property
    def is_terminal(self) -> bool:
        """Return True when the monitoring lifecycle has reached a terminal state."""
        return self.monitoring_status in ("COMPLETED", "OBSERVATION_EXPIRED")

    @property
    def is_active(self) -> bool:
        """Return True when the job is currently being monitored."""
        return self.monitoring_status == "ACTIVE"

    @property
    def is_completed(self) -> bool:
        """Return True when a terminal RPC result has been recorded."""
        return self.monitoring_status == "COMPLETED"

    @property
    def is_observation_expired(self) -> bool:
        """Return True when the observation deadline has been reached."""
        return self.monitoring_status == "OBSERVATION_EXPIRED"
# ---------------------------------------------------------------------------
# contracts_discovery table model (Phase 9)
# ---------------------------------------------------------------------------

class ContractDiscovery(AsyncAttrs, Base):
    """Database model for a Soroban contract discovery job.

    This table tracks the discovery of transactions associated with a Soroban
    contract.  It uses the stellar_sdk's get_events() method with
    EventFilter(contract_ids=[...]) to find emitted events, extracts the
    transaction hashes, and registers them into the existing monitoring workflow.

    The discovery lifecycle states are::

        PENDING   - Contract registered, not yet started discovery
        IN_PROGRESS - Worker is currently fetching events
        COMPLETED - Discovery finished; all available events processed
        ERROR     - Discovery failed after consecutive errors

    Each discovered transaction hash is registered into the existing
    ``monitoring_jobs`` table via the standard API endpoint, so discovered
    transactions share the same monitoring lifecycle (ACTIVE -> COMPLETED
    / OBSERVATION_EXPIRED).

    ``contract_address`` / ``network`` are unique together — there is one
    discovery job per contract per network.
    """

    __tablename__ = "contracts_discovery"

    id = Column(
        PostgreSQLUUID(as_uuid=False),
        primary_key=True,
        default=uuid.uuid4,
        unique=True,
        index=True,
        nullable=False,
    )

    contract_address = Column(
        CHAR(64),
        nullable=False,
        index=True,
        comment="64-char hex-encoded Soroban contract address",
    )

    network = Column(
        String(16),
        nullable=False,
        default="testnet",
        comment="Stellar network identifier (testnet | mainnet)",
    )

    status = Column(
        String(32),
        nullable=False,
        server_default="PENDING",
        comment="Discovery lifecycle status: PENDING | IN_PROGRESS | COMPLETED | ERROR",
    )

    cursor = Column(
        Text,
        nullable=True,
        comment="Pagination cursor from last get_events() call; used to "
        "resume discovery after interruption or worker restart",
    )

    last_ledger = Column(
        Integer,
        nullable=True,
        comment="Last ledger number processed during discovery",
    )

    last_successful_ledger = Column(
        Integer,
        nullable=False,
        server_default=sa.text("0"),
        comment="Last ledger where transactions were safely discovered and "
        "registered into monitoring_jobs; used to avoid re-processing "
        "already-discovered ledgers",
    )

    error_message = Column(
        Text,
        nullable=True,
        comment="Last error message encountered during discovery; cleared "
        "when status transitions to PENDING or COMPLETED",
    )

    error_count = Column(
        Integer,
        nullable=False,
        server_default=sa.text("0"),
        comment="Consecutive error count; if error_count exceeds "
        "CONSECUTIVE_ERRORS_TO_STOP (configurable), status is set to ERROR",
    )

    discovered_count = Column(
        Integer,
        nullable=False,
        server_default=sa.text("0"),
        comment="Total number of transaction hashes discovered and "
        "successfully registered into monitoring_jobs",
    )

    created_at = Column(
        PG_TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
        comment="Row creation time (timezone-aware).",
    )

    updated_at = Column(
        PG_TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
        onupdate=sa.func.now(),
        comment="Row last-update time (timezone-aware).",
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "contract_address", "network", name="uq_contracts_discovery_address_network"
        ),
    )

    # ----------------------------------------------------------------- #
    # Helper properties (read-only, no side-effects)
    # ----------------------------------------------------------------- #
    @property
    def is_pending(self) -> bool:
        """True when discovery has not yet started."""
        return self.status == "PENDING"

    @property
    def is_in_progress(self) -> bool:
        """True when the worker is currently fetching events."""
        return self.status == "IN_PROGRESS"

    @property
    def is_completed(self) -> bool:
        """True when discovery has finished successfully."""
        return self.status == "COMPLETED"

    @property
    def is_error(self) -> bool:
        """True when discovery has terminated with an error."""
        return self.status == "ERROR"

    @property
    def can_reready(self) -> bool:
        """True when the contract can be re-discovered (PENDING or ERROR)."""
        return self.status in ("PENDING", "ERROR")
