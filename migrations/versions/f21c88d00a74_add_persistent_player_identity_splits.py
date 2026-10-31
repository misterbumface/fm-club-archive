"""add persistent player identity splits

Revision ID: f21c88d00a74
Revises: d8f5a2c94e31
Create Date: 2026-07-23 02:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "f21c88d00a74"
down_revision = "d8f5a2c94e31"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "player_identity_splits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_player_id", sa.Integer(), nullable=False),
        sa.Column("target_player_id", sa.Integer(), nullable=False),
        sa.Column("created_target", sa.Boolean(), nullable=False),
        sa.Column("moved_rows_json", sa.JSON(), nullable=False),
        sa.Column("source_profile_before_json", sa.JSON(), nullable=False),
        sa.Column("source_profile_after_json", sa.JSON(), nullable=False),
        sa.Column("target_profile_before_json", sa.JSON(), nullable=False),
        sa.Column("target_profile_after_json", sa.JSON(), nullable=False),
        sa.Column("undone_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
        ),
        sa.ForeignKeyConstraint(["source_player_id"], ["players.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("player_identity_splits", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_player_identity_splits_source_player_id"),
            ["source_player_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_player_identity_splits_target_player_id"),
            ["target_player_id"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("player_identity_splits", schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_player_identity_splits_target_player_id")
        )
        batch_op.drop_index(
            batch_op.f("ix_player_identity_splits_source_player_id")
        )
    op.drop_table("player_identity_splits")
