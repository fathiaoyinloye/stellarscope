"""alerts table for Phase 10\n\nRevision ID: 20261110_alerts_table\nRevises: 20261103_contract_discovery\nCreate Date: 2026-11-10 14:00:00.000000\n\n"""
from alembic import op
import sqlalchemy as sa
import sqlalchemy.dialects.postgresql as pg

# revision identifiers, used by Alembic.
revision = "20261110_alerts_table"
down_revision = "20261103_contract_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column(
            "id",
            pg.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            comment="Primary key",
        ),
        sa.Column(
            "transaction_hash",
            sa.CHAR(64),
            nullable=True,
            index=True,
            comment="64-char hex Stellar transaction hash (nullable for RPC-level alerts)",
        ),
        sa.Column(
            "network",
            sa.String(16),
            nullable=False,
            server_default="testnet",
            comment="Stellar network identifier",
        ),
        sa.Column(
            "alert_type",
            sa.String(32),
            nullable=False,
            comment="Type of alert: SUCCESS | FAILED | OBSERVATION_EXPIRED | RPC_UNAVAILABLE",
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending",
            comment="Alert status: pending | delivered | failed",
        ),
        sa.Column(
            "delivered_at",
            pg.TIMESTAMP(timezone=True),
            nullable=True,
            comment="When the alert was successfully delivered",
        ),
        sa.Column(
            "delivery_attempts",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
            comment="Number of delivery attempts made",
        ),
        sa.Column(
            "last_error",
            sa.Text,
            nullable=True,
            comment="Last error message from delivery attempt",
        ),
        sa.Column(
            "created_at",
            pg.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="When the alert was created",
        ),
        sa.Column(
            "updated_at",
            pg.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            onupdate=sa.func.now(),
            comment="When the alert was last updated",
        ),
        sa.CheckConstraint(
            "alert_type IN ('SUCCESS', 'FAILED', 'OBSERVATION_EXPIRED', 'RPC_UNAVAILABLE')",
            name="ck_alerts_alert_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'delivered', 'failed')",
            name="ck_alerts_status_valid",
        ),
        sa.UniqueConstraint(
            "transaction_hash", "alert_type", name="uq_alerts_hash_type"
        ),
    )
    op.create_index(
        "ix_alerts_network", "alerts", ["network"], unique=False
    )
    op.create_index(
        "ix_alerts_created_at", "alerts", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_alerts_created_at", table_name="alerts")
    op.drop_index("ix_alerts_network", table_name="alerts")
    op.drop_table("alerts")
