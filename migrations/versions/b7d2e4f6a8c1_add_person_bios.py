"""Add person_bios: bios written from the save data, keyed by FM unique id.

Revision ID: b7d2e4f6a8c1
Revises: a1c3e5f7b9d2
"""

import sqlalchemy as sa
from alembic import op


revision = "b7d2e4f6a8c1"
down_revision = "a1c3e5f7b9d2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "person_bios",
        sa.Column("fm_uid", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("profile", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("game_date", sa.Date(), nullable=True),
        sa.Column("written_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("fm_uid"),
    )


def downgrade():
    op.drop_table("person_bios")
