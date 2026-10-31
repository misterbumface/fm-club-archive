"""Add competition story chapters and authoritative table date.

Revision ID: c3e78b4a9012
Revises: b7d3e5a1c904
"""

from alembic import op
import sqlalchemy as sa

revision = "c3e78b4a9012"
down_revision = "b7d3e5a1c904"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("competition_editions") as batch_op:
        batch_op.add_column(sa.Column("standings_as_of_date", sa.Date(), nullable=True))

    op.create_table(
        "competition_story_chapters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("competition_edition_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("phase_key", sa.String(length=60), nullable=True),
        sa.Column("covers_from_date", sa.Date(), nullable=True),
        sa.Column("covers_to_date", sa.Date(), nullable=True),
        sa.Column("match_ids_json", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["competition_edition_id"], ["competition_editions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_competition_story_chapters_competition_edition_id",
        "competition_story_chapters",
        ["competition_edition_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_competition_story_chapters_competition_edition_id",
        table_name="competition_story_chapters",
    )
    op.drop_table("competition_story_chapters")
    with op.batch_alter_table("competition_editions") as batch_op:
        batch_op.drop_column("standings_as_of_date")
