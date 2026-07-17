from alembic import op
import sqlalchemy as sa

revision = '20251114_07'
down_revision = '20251113_06'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('categories', schema=None) as batch_op:
        batch_op.add_column(sa.Column('parent_id', sa.BigInteger(), nullable=True))
        batch_op.create_index('ix_categories_parent_id', ['parent_id'])
        batch_op.create_foreign_key(
            'fk_categories_parent_id',
            'categories',
            ['parent_id'], ['id'],
            ondelete='CASCADE'
        )


def downgrade() -> None:
    with op.batch_alter_table('categories', schema=None) as batch_op:
        batch_op.drop_constraint('fk_categories_parent_id', type_='foreignkey')
        batch_op.drop_index('ix_categories_parent_id')
        batch_op.drop_column('parent_id')
