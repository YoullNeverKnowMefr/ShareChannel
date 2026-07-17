from alembic import op
import sqlalchemy as sa


revision = "20251212_10"
down_revision = "20251121_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telethon_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False, unique=True),
        sa.Column("session_string", sa.Text(), nullable=False),
        sa.Column("api_id", sa.Integer(), nullable=False),
        sa.Column("api_hash", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("is_primary", sa.Boolean(), default=False, nullable=False),
        sa.Column("priority", sa.Integer(), default=0, nullable=False),
        sa.Column("flood_wait_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_flood_waits", sa.Integer(), default=0, nullable=False),
        sa.Column("last_flood_wait_seconds", sa.Integer(), nullable=True),
        sa.Column("total_requests", sa.BigInteger(), default=0, nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
    )
    
    op.create_index("ix_telethon_accounts_is_active", "telethon_accounts", ["is_active"])
    op.create_index("ix_telethon_accounts_priority", "telethon_accounts", ["priority"])


def downgrade() -> None:
    op.drop_index("ix_telethon_accounts_priority", table_name="telethon_accounts")
    op.drop_index("ix_telethon_accounts_is_active", table_name="telethon_accounts")
    op.drop_table("telethon_accounts")
