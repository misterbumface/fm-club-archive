"""add national match home perspective

Revision ID: d6f2a9c4e813
Revises: c4e8a1b7d205
Create Date: 2026-07-26 20:10:00
"""

from alembic import op
import sqlalchemy as sa


revision = "d6f2a9c4e813"
down_revision = "c4e8a1b7d205"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("national_matches") as batch_op:
        batch_op.add_column(sa.Column("is_home", sa.Boolean()))


def downgrade():
    with op.batch_alter_table("national_matches") as batch_op:
        batch_op.drop_column("is_home")
