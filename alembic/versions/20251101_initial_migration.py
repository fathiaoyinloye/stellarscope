"""initial migration for StellarScope monitoring_jobs table"""

from alembic import op
import sqlalchemy as sa
import sqlalchemy.dialects.postgresql as pg
from sqlalchemy.sql import text

# revision identifiers, used by Alembic.
revision = "20251101_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitoring_jobs",
        sa.Column(
            "id",
            pg.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            comment="Primary key",
        ),
        sa.Column(
            "transaction_hash",
            CHAR(64),
            nullable=False,
            unique=True,
            index=True,
            comment="64-char hex Stellar transaction hash",
        ),
        sa.Column(
            "network",
            String(16),
            nullable=False,
            server_default="testnet",
            comment="Stellar network (testnet | mainnet)",
        ),
        sa.Column(
            "rpc_status",
            String(16),
            nullable=False,
            server_default="pending",
            comment="RPC getTransaction status: SUCCESS | FAILED | NOT_FOUND",
        ),
        sa.Column(
            "monitoring_status",
            String(16),
            nullable=False,
            server_default="ACTIVE",
            comment="Monitoring lifecycle: ACTIVE | COMPLETED | OBSERVATION_EXPIRED",
        ),
        sa.Column(
            "raw_rpc_response",
            pg.JSONB(),
            nullable=False,
            comment="Full preserved RPC response JSON",
        ),
        sa.Column(
            "envelope_xdr",
            Text,
            nullable=True,
            comment="base64 TransactionEnvelope XDR",
        ),
        sa.Column(
            "result_xdr",
            Text,
            nullable=True,
            comment="base64 TransactionResult XDR (SUCCESS/FAILED only)",
        ),
        sa.Column(
            "result_meta_xdr",
            Text,
            nullable=True,
            comment="base64 TransactionMeta XDR (SUCCESS/FAILED only)",
        ),
        sa.Column(
            "diagnostic_events_xdr",
            pg.array(Text),
            nullable=False,
            server_default=sa.text("'{}'"),
            comment="base64 DiagnosticEvent XDRs",
        ),
        sa.Column(
            "ledger",
            Integer,
            nullable=True,
            comment="Ledger number from RPC",
        ),
        sa.Column(
            "created_at_ledger",
            Integer,
            nullable=True,
            comment="Ledger when first registered",
        ),
        sa.Column(
            "fee_bump",
            Boolean,
            nullable=False,
            server_default=sa.text("FALSE"),
            comment="True when fee-bumped",
        ),
        sa.Column(
            "failure_reason",
            Text,
            nullable=True,
            comment="Optional human-readable failure reason",
        ),
        sa.Column(
            "locked_by",
            String(128),
            nullable=True,
            comment="Worker identifier (hostname:pid)",
        ),
        sa.Column(
            "locked_until",
            pg.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Lease expiry time",
        ),
        sa.Column(
            "next_check_at",
            pg.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Earliest next poll timestamp",
        ),
        sa.Column(
            "attempt_count",
            Integer,
            nullable=False,
            server_default=sa.text("0"),
            comment="Retry attempt counter",
        ),
        sa.Column(
            "created_at",
            pg.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Row creation time",
        ),
        sa.Column(
            "updated_at",
            pg.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            onupdate=sa.text("now()"),
            comment="Row last-update time",
        ),
        sa.CheckConstraint(
            "monitoring_status IN ('ACTIVE', 'COMPLETED', 'OBSERVATION_EXPIRED')",
            name="ck_monitoring_status_valid",
        ),
        sa.CheckConstraint(
            "rpc_status IN ('SUCCESS', 'FAILED', 'NOT_FOUND')",
            name="ck_rpc_status_valid",
        ),
        sa.UniqueConstraint("transaction_hash", name="uq_monitoring_jobs_hash"),
    )
    # Partial indexes for worker coordination
    op.create_index(
        "ix_monitoring_jobs_pending",
        "monitoring_jobs",
        ["status", "created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_monitoring_jobs_processing",
        "monitoring_jobs",
        ["status", "locked_at"],
        unique=False,
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.create_index(
        "ix_monitoring_jobs_network",
        "monitoring_jobs",
        ["network"],
        unique=False,
    )
    op.create_index(
        "ix_monitoring_jobs_rpc_status",
        "monitoring_jobs",
        ["rpc_status"],
        unique=False,
    )
    op.create_index(
        "ix_monitoring_jobs_monitoring_status",
        "monitoring_jobs",
        ["monitoring_status"],
        unique=False,
    )
    op.create_index(
        "ix_monitoring_jobs_locked_until",
        "monitoring_jobs",
        ["locked_until"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_monitoring_jobs_locked_until", table_name="monitoring_jobs")
    op.drop_index("ix_monitoring_jobs_monitoring_status", table_name="monitoring_jobs")
    op.drop_index("ix_monitoring_jobs_rpc_status", table_name="monitoring_jobs")
    op.drop_index("ix_monitoring_jobs_network", table_name="monitoring_jobs")
    op.drop_index("ix_monitoring_jobs_processing", table_name="monitoring_jobs")
    op.drop_index("ix_monitoring_jobs_pending", table_name="monitoring_jobs")
    op.drop_table("monitoring_jobs")
