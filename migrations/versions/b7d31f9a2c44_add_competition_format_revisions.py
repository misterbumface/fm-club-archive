"""add competition format revisions and artwork

Revision ID: b7d31f9a2c44
Revises: e9c4b7a21d60
Create Date: 2026-07-28
"""

from alembic import op
import sqlalchemy as sa


revision = "b7d31f9a2c44"
down_revision = "e9c4b7a21d60"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("competitions") as batch_op:
        batch_op.add_column(sa.Column("trophy_image_path", sa.String(300)))
        batch_op.add_column(sa.Column("badge_image_path", sa.String(300)))
        batch_op.add_column(sa.Column("banner_image_path", sa.String(300)))

    op.create_table(
        "competition_format_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "competition_id",
            sa.Integer(),
            sa.ForeignKey("competitions.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_start_year", sa.Integer()),
        sa.Column("effective_end_year", sa.Integer()),
        sa.Column("source_xml", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("note", sa.String(300)),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "competition_id",
            "version",
            name="uq_competition_format_revision_version",
        ),
    )
    op.create_index(
        "ix_competition_format_revisions_competition_id",
        "competition_format_revisions",
        ["competition_id"],
    )
    with op.batch_alter_table("competition_editions") as batch_op:
        batch_op.add_column(sa.Column("format_revision_id", sa.Integer()))
        batch_op.create_foreign_key(
            "fk_competition_editions_format_revision",
            "competition_format_revisions",
            ["format_revision_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("competition_editions") as batch_op:
        batch_op.drop_constraint(
            "fk_competition_editions_format_revision", type_="foreignkey"
        )
        batch_op.drop_column("format_revision_id")
    op.drop_index(
        "ix_competition_format_revisions_competition_id",
        table_name="competition_format_revisions",
    )
    op.drop_table("competition_format_revisions")
    with op.batch_alter_table("competitions") as batch_op:
        batch_op.drop_column("banner_image_path")
        batch_op.drop_column("badge_image_path")
        batch_op.drop_column("trophy_image_path")
