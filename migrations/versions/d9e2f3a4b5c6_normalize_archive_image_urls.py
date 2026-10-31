"""Normalize image URLs produced by the first catalogue backfill.

Revision ID: d9e2f3a4b5c6
Revises: c8f1a2d3b4e5
"""

from alembic import op
import sqlalchemy as sa


revision = "d9e2f3a4b5c6"
down_revision = "c8f1a2d3b4e5"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE archive_images SET url = substr(url, 2) "
            "WHERE url LIKE '//static/%'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE world_soccer_image_uses SET image_url = substr(image_url, 2) "
            "WHERE image_url LIKE '//static/%'"
        )
    )


def downgrade():
    # The normalized form is the canonical public URL; there is no useful
    # downgrade that should reintroduce an invalid protocol-relative path.
    pass

