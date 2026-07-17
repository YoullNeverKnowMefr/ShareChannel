from alembic import op
import sqlalchemy as sa
from sqlalchemy import DateTime

revision = "20251213_11"
down_revision = "20251212_10"
branch_labels = None
depends_on = None

ID_TYPE = sa.BigInteger().with_variant(sa.Integer, "sqlite")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return

    op.drop_table("login_attempts")
    op.drop_table("authorized_users")
    op.drop_table("blocked_users")

    op.create_table(
        "login_attempts",
        sa.Column("id", ID_TYPE, autoincrement=True, nullable=False),
        sa.Column("user_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("ip_address", sa.String(length=100), nullable=True),
        sa.Column("created_at", DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_login_attempts_user_tg_id", "login_attempts", ["user_tg_id"])
    op.create_index("ix_login_attempts_created_at", "login_attempts", ["created_at"])

    op.create_table(
        "authorized_users",
        sa.Column("id", ID_TYPE, autoincrement=True, nullable=False),
        sa.Column("user_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("last_name", sa.String(length=255), nullable=True),
        sa.Column("first_login_at", DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_tg_id"),
    )
    op.create_index("ix_authorized_users_user_tg_id", "authorized_users", ["user_tg_id"])

    op.create_table(
        "blocked_users",
        sa.Column("id", ID_TYPE, autoincrement=True, nullable=False),
        sa.Column("user_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("blocked_at", DateTime(timezone=True), nullable=False),
        sa.Column("blocked_by_tg_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_tg_id"),
    )
    op.create_index("ix_blocked_users_user_tg_id", "blocked_users", ["user_tg_id"])


def downgrade() -> None:
    pass
