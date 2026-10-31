"""add unresolved scorer stubs

Revision ID: a9c3e7f12b40
Revises: e5b72c1d904f
Create Date: 2026-07-26 20:15:00
"""

from alembic import op
import sqlalchemy as sa


revision = "a9c3e7f12b40"
down_revision = "e5b72c1d904f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scorer_stubs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("club_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "club_id",
            "normalized_name",
            name="uq_scorer_stub_club_name",
        ),
    )
    with op.batch_alter_table("scorer_stubs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_scorer_stubs_normalized_name"),
            ["normalized_name"],
            unique=False,
        )
    with op.batch_alter_table("goals", schema=None) as batch_op:
        batch_op.add_column(sa.Column("scorer_stub_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_goals_scorer_stub_id"),
            ["scorer_stub_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_goals_scorer_stub_id",
            "scorer_stubs",
            ["scorer_stub_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("goals", schema=None) as batch_op:
        batch_op.drop_constraint("fk_goals_scorer_stub_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_goals_scorer_stub_id"))
        batch_op.drop_column("scorer_stub_id")
    with op.batch_alter_table("scorer_stubs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_scorer_stubs_normalized_name"))
    op.drop_table("scorer_stubs")
