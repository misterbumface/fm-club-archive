"""Add stable fixture identities and derived-table metadata.

Revision ID: f2a7c9d4e6b1
Revises: e0f4a7b8c9d1
"""

from alembic import op
import sqlalchemy as sa


revision = "f2a7c9d4e6b1"
down_revision = "e0f4a7b8c9d1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("matches") as batch_op:
        batch_op.add_column(sa.Column("fixture_identity_key", sa.String(length=400)))
        batch_op.create_index("ix_matches_fixture_identity_key", ["fixture_identity_key"])
    with op.batch_alter_table("scheduled_fixtures") as batch_op:
        batch_op.add_column(sa.Column("fixture_identity_key", sa.String(length=400)))
        batch_op.create_index(
            "ix_scheduled_fixtures_fixture_identity_key", ["fixture_identity_key"]
        )
    with op.batch_alter_table("world_matches") as batch_op:
        batch_op.add_column(sa.Column("fixture_identity_key", sa.String(length=400)))
        batch_op.create_index("ix_world_matches_fixture_identity_key", ["fixture_identity_key"])
    with op.batch_alter_table("competition_editions") as batch_op:
        batch_op.add_column(sa.Column("standings_source", sa.String(length=30)))
        batch_op.add_column(sa.Column("standings_derived_through_date", sa.Date()))


def downgrade():
    with op.batch_alter_table("competition_editions") as batch_op:
        batch_op.drop_column("standings_derived_through_date")
        batch_op.drop_column("standings_source")
    with op.batch_alter_table("world_matches") as batch_op:
        batch_op.drop_index("ix_world_matches_fixture_identity_key")
        batch_op.drop_column("fixture_identity_key")
    with op.batch_alter_table("scheduled_fixtures") as batch_op:
        batch_op.drop_index("ix_scheduled_fixtures_fixture_identity_key")
        batch_op.drop_column("fixture_identity_key")
    with op.batch_alter_table("matches") as batch_op:
        batch_op.drop_index("ix_matches_fixture_identity_key")
        batch_op.drop_column("fixture_identity_key")
