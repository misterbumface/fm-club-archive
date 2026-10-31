"""The social feed: each post's heat, and a fan's predicted score.

Revision ID: e2f4a6b8c0d1
Revises: d1e3f5a7b9c0
"""

import sqlalchemy as sa
from alembic import op


revision = "e2f4a6b8c0d1"
down_revision = "d1e3f5a7b9c0"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("social_posts") as batch:
        batch.add_column(sa.Column("heat", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("prediction", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("social_posts") as batch:
        batch.drop_column("prediction")
        batch.drop_column("heat")
