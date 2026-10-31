"""The social feed: when a drip-fed reply is shown.

Revision ID: f4a6b8c0d2e3
Revises: e2f4a6b8c0d1
"""

import sqlalchemy as sa
from alembic import op


revision = "f4a6b8c0d2e3"
down_revision = "e2f4a6b8c0d1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("social_posts") as batch:
        batch.add_column(sa.Column("shown_at", sa.DateTime(), nullable=True))
        batch.create_index("ix_social_posts_shown_at", ["shown_at"])


def downgrade():
    with op.batch_alter_table("social_posts") as batch:
        batch.drop_index("ix_social_posts_shown_at")
        batch.drop_column("shown_at")
