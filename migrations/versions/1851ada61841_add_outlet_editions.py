"""Editions per outlet, and the lead behind each article (docs/media-plan.md, step 4).

Revision ID: 1851ada61841
Revises: b2d4f6a8c0e1
"""

import sqlalchemy as sa
from alembic import op


revision = "1851ada61841"
down_revision = "b2d4f6a8c0e1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("world_soccer_issues") as batch:
        batch.add_column(sa.Column("outlet_id", sa.Integer(), nullable=True))
        batch.create_index("ix_world_soccer_issues_outlet_id", ["outlet_id"])
        batch.create_foreign_key("fk_world_soccer_issues_outlet_id", "outlets", ["outlet_id"], ["id"], ondelete="SET NULL")
    with op.batch_alter_table("world_soccer_articles") as batch:
        batch.add_column(sa.Column("lead_key", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("lead", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("model", sa.String(length=80), nullable=True))
        batch.create_index("ix_world_soccer_articles_lead_key", ["lead_key"])


def downgrade():
    with op.batch_alter_table("world_soccer_articles") as batch:
        batch.drop_index("ix_world_soccer_articles_lead_key")
        batch.drop_column("model")
        batch.drop_column("lead")
        batch.drop_column("lead_key")
    with op.batch_alter_table("world_soccer_issues") as batch:
        batch.drop_constraint("fk_world_soccer_issues_outlet_id", type_="foreignkey")
        batch.drop_index("ix_world_soccer_issues_outlet_id")
        batch.drop_column("outlet_id")
