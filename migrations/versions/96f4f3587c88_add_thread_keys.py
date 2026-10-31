"""A subject key on story threads, so the outlets' articles about one player or club join one
thread across editions (docs/media-plan.md, step 5).

Revision ID: 96f4f3587c88
Revises: aeb5fb1008a3
"""

import sqlalchemy as sa
from alembic import op


revision = "96f4f3587c88"
down_revision = "aeb5fb1008a3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("world_soccer_story_threads") as batch:
        batch.add_column(sa.Column("key", sa.String(length=80), nullable=True))
        batch.create_index("ix_world_soccer_story_threads_key", ["key"])


def downgrade():
    with op.batch_alter_table("world_soccer_story_threads") as batch:
        batch.drop_index("ix_world_soccer_story_threads_key")
        batch.drop_column("key")
