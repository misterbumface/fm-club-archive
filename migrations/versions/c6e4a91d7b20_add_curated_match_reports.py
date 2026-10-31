"""add curated match reports

Revision ID: c6e4a91d7b20
Revises: bb6ec7cfebfe
Create Date: 2026-07-22 14:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "c6e4a91d7b20"
down_revision = "bb6ec7cfebfe"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("report_text", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("report_source", sa.String(length=30), nullable=True))
        batch_op.add_column(
            sa.Column("report_source_filename", sa.String(length=300), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.drop_column("report_source_filename")
        batch_op.drop_column("report_source")
        batch_op.drop_column("report_text")
