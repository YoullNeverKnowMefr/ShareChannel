
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20240322_01_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("tg_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "security",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "locked_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "shops",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("owner_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_tg_id", "name", name="uq_shops_owner_name"),
    )

    op.create_table(
        "chains",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("shop_id", sa.BigInteger(), sa.ForeignKey("shops.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="paused"),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("sink_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("start_number", sa.Integer(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("next_expected_number", sa.Integer(), nullable=False),
        sa.Column("last_sent_number", sa.Integer(), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )
    op.create_index("ix_chains_status", "chains", ["status"])
    op.create_index("ix_chains_shop_id", "chains", ["shop_id"])

    op.create_table(
        "message_map",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("chain_id", sa.BigInteger(), sa.ForeignKey("chains.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_msg_id", sa.BigInteger(), nullable=False),
        sa.Column("source_msg_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sink_msg_id", sa.BigInteger(), nullable=False),
        sa.Column("sink_msg_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("number_tag", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("chain_id", "source_msg_id", name="uq_message_map_chain_source"),
    )
    op.create_index(
        "ix_message_map_chain_number",
        "message_map",
        ["chain_id", "number_tag"],
    )
    op.create_index(
        "ix_message_map_source",
        "message_map",
        ["source_msg_id"],
    )
    op.create_index(
        "ix_message_map_sink",
        "message_map",
        ["sink_msg_id"],
    )

    op.create_table(
        "rate_limit_events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("scope", sa.String(length=100), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("actor_tg_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("rate_limit_events")
    op.drop_index("ix_message_map_sink", table_name="message_map")
    op.drop_index("ix_message_map_source", table_name="message_map")
    op.drop_index("ix_message_map_chain_number", table_name="message_map")
    op.drop_table("message_map")
    op.drop_index("ix_chains_shop_id", table_name="chains")
    op.drop_index("ix_chains_status", table_name="chains")
    op.drop_table("chains")
    op.drop_table("shops")
    op.drop_table("security")
    op.drop_table("users")
