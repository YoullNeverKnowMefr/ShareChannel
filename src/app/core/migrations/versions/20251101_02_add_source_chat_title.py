
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20251101_02"
down_revision = "20240322_01_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chains", sa.Column("source_chat_title", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("chains", "source_chat_title")
