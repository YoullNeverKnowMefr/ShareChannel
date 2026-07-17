from alembic import op
import sqlalchemy as sa
from sqlalchemy import DateTime

revision = '20251114_08'
down_revision = '20251114_07'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'categories_new',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('shop_id', sa.BigInteger(), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('created_at', DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_id'], ['categories_new.id'], ondelete='CASCADE')
    )
    
    op.execute("""
        INSERT INTO categories_new (id, shop_id, parent_id, name, created_at)
        SELECT id, shop_id, parent_id, name, created_at FROM categories
    """)
    
    op.drop_table('categories')
    
    op.rename_table('categories_new', 'categories')
    
    op.create_index('ix_categories_shop_id', 'categories', ['shop_id'])
    op.create_index('ix_categories_parent_id', 'categories', ['parent_id'])


def downgrade() -> None:
    op.create_table(
        'categories_new',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('shop_id', sa.BigInteger(), nullable=False),
        sa.Column('parent_id', sa.BigInteger(), nullable=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('created_at', DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['shop_id'], ['shops.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_id'], ['categories_new.id'], ondelete='CASCADE')
    )
    
    op.execute("""
        INSERT INTO categories_new (id, shop_id, parent_id, name, created_at)
        SELECT id, shop_id, parent_id, name, created_at FROM categories
    """)
    
    op.drop_table('categories')
    op.rename_table('categories_new', 'categories')
    op.create_index('ix_categories_shop_id', 'categories', ['shop_id'])
    op.create_index('ix_categories_parent_id', 'categories', ['parent_id'])
