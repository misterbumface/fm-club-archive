"""Align the database with the models.

Three differences were left between what the migrations built and what the
models declare:

* ``world_soccer_articles.image_asset_id`` and
  ``world_soccer_issues.cover_image_asset_id`` pointed at ``archive_images``
  with no foreign key. They get one that clears the column if the image row is
  ever deleted, which is what an optional picture should do.
* ``national_teams.normalized_name`` had a plain index. The model declares it
  unique, and the table holds one row per name.

Revision ID: a1c3e5f7b9d2
Revises: f2a7c9d4e6b1
"""

from alembic import op


revision = "a1c3e5f7b9d2"
down_revision = "f2a7c9d4e6b1"
branch_labels = None
depends_on = None

_ARTICLE_FK = "fk_world_soccer_articles_image_asset_id_archive_images"
_ISSUE_FK = "fk_world_soccer_issues_cover_image_asset_id_archive_images"


def upgrade():
    bind = op.get_bind()
    # A foreign key added to existing rows must not start out broken.
    for table, column in (
        ("world_soccer_articles", "image_asset_id"),
        ("world_soccer_issues", "cover_image_asset_id"),
    ):
        bind.exec_driver_sql(
            f"UPDATE {table} SET {column} = NULL WHERE {column} IS NOT NULL "
            f"AND {column} NOT IN (SELECT id FROM archive_images)"
        )

    with op.batch_alter_table("world_soccer_articles") as batch_op:
        batch_op.create_foreign_key(
            _ARTICLE_FK, "archive_images", ["image_asset_id"], ["id"], ondelete="SET NULL"
        )
    with op.batch_alter_table("world_soccer_issues") as batch_op:
        batch_op.create_foreign_key(
            _ISSUE_FK, "archive_images", ["cover_image_asset_id"], ["id"], ondelete="SET NULL"
        )

    with op.batch_alter_table("national_teams") as batch_op:
        batch_op.drop_index("ix_national_teams_normalized_name")
        batch_op.create_index(
            "ix_national_teams_normalized_name", ["normalized_name"], unique=True
        )


def downgrade():
    with op.batch_alter_table("national_teams") as batch_op:
        batch_op.drop_index("ix_national_teams_normalized_name")
        batch_op.create_index("ix_national_teams_normalized_name", ["normalized_name"])
    with op.batch_alter_table("world_soccer_issues") as batch_op:
        batch_op.drop_constraint(_ISSUE_FK, type_="foreignkey")
    with op.batch_alter_table("world_soccer_articles") as batch_op:
        batch_op.drop_constraint(_ARTICLE_FK, type_="foreignkey")
