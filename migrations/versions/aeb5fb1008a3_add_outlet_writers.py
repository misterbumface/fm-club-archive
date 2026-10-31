"""The writers: fictional journalists on the outlets' staffs (docs/media-plan.md, step 4).

Revision ID: aeb5fb1008a3
Revises: 1851ada61841
"""

import sqlalchemy as sa
from alembic import op


revision = "aeb5fb1008a3"
down_revision = "1851ada61841"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "writers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("outlet_id", sa.Integer(), sa.ForeignKey("outlets.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("persona", sa.Text(), nullable=False),
        sa.Column("style", sa.Text(), nullable=False),
        sa.Column("quirks", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_writers_outlet_id", "writers", ["outlet_id"])
    with op.batch_alter_table("world_soccer_articles") as batch:
        batch.add_column(sa.Column("writer_id", sa.Integer(), nullable=True))
        batch.create_index("ix_world_soccer_articles_writer_id", ["writer_id"])
        batch.create_foreign_key("fk_world_soccer_articles_writer_id", "writers", ["writer_id"], ["id"], ondelete="SET NULL")


def downgrade():
    with op.batch_alter_table("world_soccer_articles") as batch:
        batch.drop_constraint("fk_world_soccer_articles_writer_id", type_="foreignkey")
        batch.drop_index("ix_world_soccer_articles_writer_id")
        batch.drop_column("writer_id")
    op.drop_index("ix_writers_outlet_id", "writers")
    op.drop_table("writers")
