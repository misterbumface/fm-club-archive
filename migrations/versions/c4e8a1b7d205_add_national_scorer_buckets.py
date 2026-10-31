"""allow unresolved scorer buckets for national teams

Revision ID: c4e8a1b7d205
Revises: b1d4f7a93c20
Create Date: 2026-07-26 23:30:00
"""

from alembic import op
import sqlalchemy as sa


revision = "c4e8a1b7d205"
down_revision = "b1d4f7a93c20"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("scorer_stubs") as batch_op:
        batch_op.alter_column("club_id", existing_type=sa.Integer(), nullable=True)
        batch_op.add_column(sa.Column("national_team_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_scorer_stubs_national_team_id",
            "national_teams",
            ["national_team_id"],
            ["id"],
        )
        batch_op.create_unique_constraint(
            "uq_scorer_stub_national_team_name",
            ["national_team_id", "normalized_name"],
        )
        batch_op.create_check_constraint(
            "ck_scorer_stub_exactly_one_team",
            "(club_id IS NOT NULL AND national_team_id IS NULL) OR "
            "(club_id IS NULL AND national_team_id IS NOT NULL)",
        )

    with op.batch_alter_table("national_goals") as batch_op:
        batch_op.add_column(sa.Column("scorer_stub_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_national_goals_scorer_stub_id",
            "scorer_stubs",
            ["scorer_stub_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_national_goals_scorer_stub_id",
            ["scorer_stub_id"],
        )


def downgrade():
    with op.batch_alter_table("national_goals") as batch_op:
        batch_op.drop_index("ix_national_goals_scorer_stub_id")
        batch_op.drop_constraint(
            "fk_national_goals_scorer_stub_id", type_="foreignkey"
        )
        batch_op.drop_column("scorer_stub_id")

    with op.batch_alter_table("scorer_stubs") as batch_op:
        batch_op.drop_constraint(
            "ck_scorer_stub_exactly_one_team", type_="check"
        )
        batch_op.drop_constraint(
            "uq_scorer_stub_national_team_name", type_="unique"
        )
        batch_op.drop_constraint(
            "fk_scorer_stubs_national_team_id", type_="foreignkey"
        )
        batch_op.drop_column("national_team_id")
        batch_op.alter_column("club_id", existing_type=sa.Integer(), nullable=False)
