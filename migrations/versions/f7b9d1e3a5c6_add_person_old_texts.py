"""Add person_old_texts: the old archive's bios by FM id.

Revision ID: f7b9d1e3a5c6
Revises: e6a8c0d2f4b5
"""

import sqlalchemy as sa
from alembic import op


revision = "f7b9d1e3a5c6"
down_revision = "e6a8c0d2f4b5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "person_old_texts",
        sa.Column("fm_uid", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("raw_source", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint("fm_uid"),
    )


def downgrade():
    op.drop_table("person_old_texts")
