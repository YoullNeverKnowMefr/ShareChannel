from alembic import op
import sqlalchemy as sa
from sqlalchemy import DateTime

revision = '20251113_06'
down_revision = '20251109_05'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'categories',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('shop_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('created_at', DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ondelete='CASCADE')
    )
    op.create_index('ix_categories_shop_id', 'categories', ['shop_id'])
    
    with op.batch_alter_table('chains', schema=None) as batch_op:
        batch_op.add_column(sa.Column('category_id', sa.BigInteger(), nullable=True))
        batch_op.create_index('ix_chains_category_id', ['category_id'])
        batch_op.create_foreign_key(
            'fk_chains_category_id',
            'categories',
            ['category_id'], ['id'],
            ondelete='CASCADE'
        )


def downgrade() -> None:
    with op.batch_alter_table('chains', schema=None) as batch_op:
        batch_op.drop_constraint('fk_chains_category_id', type_='foreignkey')
        batch_op.drop_index('ix_chains_category_id')
        batch_op.drop_column('category_id')
    
    op.drop_index('ix_categories_shop_id', table_name='categories')
    op.drop_table('categories')

