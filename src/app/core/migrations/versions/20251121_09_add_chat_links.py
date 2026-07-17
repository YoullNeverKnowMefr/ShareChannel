
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20251121_09"
down_revision = "20251114_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chains", sa.Column("source_chat_link", sa.String(length=512), nullable=True))
    op.add_column("chains", sa.Column("sink_chat_link", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("chains", "sink_chat_link")
    op.drop_column("chains", "source_chat_link")
