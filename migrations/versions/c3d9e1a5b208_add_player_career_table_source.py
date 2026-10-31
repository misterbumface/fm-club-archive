"""Add player.career_table_raw_source

Revision ID: c3d9e1a5b208
Revises: b1f4d8c93a20
Create Date: 2026-07-20

Stores the raw FM history-table HTML export per player, the authoritative
year->club map used to correct spell attribution.
"""

from alembic import op
import sqlalchemy as sa

revision = "c3d9e1a5b208"
down_revision = "b1f4d8c93a20"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("players") as batch:
        batch.add_column(sa.Column("career_table_raw_source", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("players") as batch:
        batch.drop_column("career_table_raw_source")
