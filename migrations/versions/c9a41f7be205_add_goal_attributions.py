"""add goal attributions

Revision ID: c9a41f7be205
Revises: 314b8679a870
Create Date: 2026-07-24 18:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "c9a41f7be205"
down_revision = "314b8679a870"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "goal_attributions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("goal_id", sa.Integer(), nullable=False),
        sa.Column("previous_player_id", sa.Integer(), nullable=True),
        sa.Column("new_player_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("undone_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("goal_attributions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_goal_attributions_goal_id"), ["goal_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_goal_attributions_previous_player_id"),
            ["previous_player_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_goal_attributions_new_player_id"),
            ["new_player_id"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("goal_attributions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_goal_attributions_new_player_id"))
        batch_op.drop_index(batch_op.f("ix_goal_attributions_previous_player_id"))
        batch_op.drop_index(batch_op.f("ix_goal_attributions_goal_id"))
    op.drop_table("goal_attributions")
