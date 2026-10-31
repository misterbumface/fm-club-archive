"""Add world_ref to archive_image_links: the image's subject by FM id.

Revision ID: e6a8c0d2f4b5
Revises: d5f7b9c1e3a4
"""

import sqlalchemy as sa
from alembic import op


revision = "e6a8c0d2f4b5"
down_revision = "d5f7b9c1e3a4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("archive_image_links") as batch:
        batch.add_column(sa.Column("world_ref", sa.String(length=80), nullable=True))
        batch.create_index("ix_archive_image_links_world_ref", ["world_ref"])


def downgrade():
    with op.batch_alter_table("archive_image_links") as batch:
        batch.drop_index("ix_archive_image_links_world_ref")
        batch.drop_column("world_ref")
