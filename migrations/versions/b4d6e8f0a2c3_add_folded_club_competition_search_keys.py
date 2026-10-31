"""add accent-folded club and competition search keys

Revision ID: b4d6e8f0a2c3
Revises: a3d5f7c9e1b2
Create Date: 2026-08-01 00:30:00
"""

from __future__ import annotations

import re
import unicodedata

from alembic import op
import sqlalchemy as sa


revision = "b4d6e8f0a2c3"
down_revision = "a3d5f7c9e1b2"
branch_labels = None
depends_on = None


_SPECIAL_TRANSLATIONS = str.maketrans(
    {
        "ß": "ss",
        "ẞ": "ss",
        "æ": "ae",
        "Æ": "ae",
        "œ": "oe",
        "Œ": "oe",
        "ø": "o",
        "Ø": "o",
        "ł": "l",
        "Ł": "l",
        "đ": "d",
        "Đ": "d",
        "ð": "d",
        "Ð": "d",
        "þ": "th",
        "Þ": "th",
        "ħ": "h",
        "Ħ": "h",
        "ı": "i",
        "ĸ": "k",
        "ŋ": "n",
        "Ŧ": "t",
        "ŧ": "t",
    }
)


def _fold_name(value: str | None) -> str:
    text = unicodedata.normalize("NFC", (value or "").strip())
    text = text.translate(_SPECIAL_TRANSLATIONS).casefold().replace(".", "")
    text = re.sub(r"\s+", " ", text)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _backfill(table_name: str, id_name: str, source_name: str, target_name: str) -> None:
    connection = op.get_bind()
    table = sa.table(
        table_name,
        sa.column(id_name, sa.Integer()),
        sa.column(source_name, sa.String(length=200)),
        sa.column(target_name, sa.String(length=200)),
    )
    rows = connection.execute(sa.select(table.c[id_name], table.c[source_name])).all()
    for row_id, source_value in rows:
        connection.execute(
            table.update()
            .where(table.c[id_name] == row_id)
            .values(**{target_name: _fold_name(source_value)})
        )


def upgrade():
    additions = (
        ("clubs", "normalized_name_folded", "name", "ix_clubs_normalized_name_folded"),
        (
            "club_aliases",
            "normalized_alias_folded",
            "alias",
            "ix_club_aliases_normalized_alias_folded",
        ),
        (
            "competitions",
            "normalized_name_folded",
            "name",
            "ix_competitions_normalized_name_folded",
        ),
    )
    for table_name, target_name, _source_name, _index_name in additions:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(
                sa.Column(target_name, sa.String(length=200), nullable=True)
            )

    for table_name, target_name, source_name, _index_name in additions:
        _backfill(table_name, "id", source_name, target_name)

    for table_name, target_name, _source_name, index_name in additions:
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                target_name,
                existing_type=sa.String(length=200),
                nullable=False,
            )
            batch_op.create_index(index_name, [target_name], unique=False)


def downgrade():
    for table_name, target_name, _source_name, index_name in reversed(
        (
            ("clubs", "normalized_name_folded", "name", "ix_clubs_normalized_name_folded"),
            (
                "club_aliases",
                "normalized_alias_folded",
                "alias",
                "ix_club_aliases_normalized_alias_folded",
            ),
            (
                "competitions",
                "normalized_name_folded",
                "name",
                "ix_competitions_normalized_name_folded",
            ),
        )
    ):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_index(index_name)
            batch_op.drop_column(target_name)
