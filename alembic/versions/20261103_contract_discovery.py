"""contracts_discovery table for Phase 9

Revision ID: 20261103_contract_discovery
Revises: 20251101_initial
Create Date: 2026-11-03 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlalchemy.dialects.postgresql as pg

# revision identifiers, used by Alembic.
revision = "20261103_contract_discovery"
down_revision = "20251101_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contracts_discovery",
        sa.Column(
            "id",
            pg.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            comment="Primary key",
        ),
        sa.Column(
            "contract_address",
            sa.CHAR(64),
            nullable=False,
            index=True,
            comment="64-char hex Soroban contract address",
        ),
        sa.Column(
            "network",
            sa.String(16),
            nullable=False,
            server_default="testnet",
            comment="Stellar network identifier (testnet | mainnet)",
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="PENDING",
            comment="Discovery status: PENDING | IN_PROGRESS | COMPLETED | ERROR",
        ),
        sa.Column(
            "cursor",
            sa.Text,
            nullable=True,
            comment="Pagination cursor from last get_events() call",
        ),
        sa.Column(
            "last_ledger",
            sa.Integer,
            nullable=True,
            comment="Last ledger processed",
        ),
        sa.Column(
            "last_successful_ledger",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
            comment="Last ledger where transactions were safely discovered",
        ),
        sa.Column(
            "error_message",
            sa.Text,
            nullable=True,
            comment="Last error message (if any)",
        ),
        sa.Column(
            "error_count",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
            comment="Consecutive error count",
        ),
        sa.Column(
            "discovered_count",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
            comment="Total transactions discovered and registered",
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
            onupdate=sa.func.now(),
            comment="Row last-update time",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'IN_PROGRESS', 'COMPLETED', 'ERROR')",
            name="ck_contracts_discovery_status_valid",
        ),
        sa.UniqueConstraint("contract_address", "network", name="uq_contracts_discovery_address_network"),
    )
    # Index for cursor-based pagination queries
    op.create_index(
        "ix_contracts_discovery_cursor",
        "contracts_discovery",
        ["cursor"],
        unique=False,
    )
    op.create_index(
        "ix_contracts_discovery_network",
        "contracts_discovery",
        ["network"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_contracts_discovery_cursor", table_name="contracts_discovery")
    op.drop_index("ix_contracts_discovery_network", table_name="contracts_discovery")
    op.drop_table("contracts_discovery")
