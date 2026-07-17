from alembic import op
import sqlalchemy as sa
from sqlalchemy import DateTime

revision = '20251108_04'
down_revision = '20251102_03'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'login_attempts',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer, "sqlite"), autoincrement=True, nullable=False),
        sa.Column('user_tg_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(length=255), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('ip_address', sa.String(length=100), nullable=True),
        sa.Column('created_at', DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_login_attempts_user_tg_id', 'login_attempts', ['user_tg_id'])
    op.create_index('ix_login_attempts_created_at', 'login_attempts', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_login_attempts_created_at', table_name='login_attempts')
    op.drop_index('ix_login_attempts_user_tg_id', table_name='login_attempts')
    op.drop_table('login_attempts')

