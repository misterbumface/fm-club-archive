"""Who wrote a club's text: the curator (no source) or an AI model, so a new text keeps the old one.

Revision ID: c4e6a8b0d2f3
Revises: b7e2c4d6f8a1
"""

import sqlalchemy as sa
from alembic import op


revision = "c4e6a8b0d2f3"
down_revision = "b7e2c4d6f8a1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("club_texts") as batch:
        batch.add_column(sa.Column("source", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("model", sa.String(length=80), nullable=True))


def downgrade():
    with op.batch_alter_table("club_texts") as batch:
        batch.drop_column("model")
        batch.drop_column("source")
