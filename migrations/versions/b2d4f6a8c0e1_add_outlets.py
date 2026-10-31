"""Add the news outlets (docs/media-plan.md, step 3).

The first set of outlets is made by app.services.outlets.ensure_outlets the first time it is
needed.

Revision ID: b2d4f6a8c0e1
Revises: a8c0e2f4b6d7
"""

import sqlalchemy as sa
from alembic import op


revision = "b2d4f6a8c0e1"
down_revision = "a8c0e2f4b6d7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "outlets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("tagline", sa.String(length=300), nullable=True),
        sa.Column("voice", sa.Text(), nullable=False),
        sa.Column("beats", sa.JSON(), nullable=False),
        sa.Column("byline", sa.String(length=160), nullable=False),
        sa.Column("articles_per_edition", sa.Integer(), nullable=False),
        sa.Column("min_score", sa.Float(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )


def downgrade():
    op.drop_table("outlets")
