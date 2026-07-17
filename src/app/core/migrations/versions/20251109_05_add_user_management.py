from alembic import op
import sqlalchemy as sa
from sqlalchemy import DateTime

revision = '20251109_05'
down_revision = '20251108_04'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'authorized_users',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer, "sqlite"), autoincrement=True, nullable=False),
        sa.Column('user_tg_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(length=255), nullable=True),
        sa.Column('first_name', sa.String(length=255), nullable=True),
        sa.Column('last_name', sa.String(length=255), nullable=True),
        sa.Column('first_login_at', DateTime(timezone=True), nullable=False),
        sa.Column('last_login_at', DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_tg_id')
    )
    op.create_index('ix_authorized_users_user_tg_id', 'authorized_users', ['user_tg_id'])
    
    op.create_table(
        'blocked_users',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer, "sqlite"), autoincrement=True, nullable=False),
        sa.Column('user_tg_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(length=255), nullable=True),
        sa.Column('reason', sa.String(length=500), nullable=True),
        sa.Column('blocked_at', DateTime(timezone=True), nullable=False),
        sa.Column('blocked_by_tg_id', sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_tg_id')
    )
    op.create_index('ix_blocked_users_user_tg_id', 'blocked_users', ['user_tg_id'])


def downgrade() -> None:
    op.drop_index('ix_blocked_users_user_tg_id', table_name='blocked_users')
    op.drop_table('blocked_users')
    op.drop_index('ix_authorized_users_user_tg_id', table_name='authorized_users')
    op.drop_table('authorized_users')

