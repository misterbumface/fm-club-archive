"""add accent-folded player and alias search keys

Revision ID: a3d5f7c9e1b2
Revises: f8c4a2d91e70
Create Date: 2026-08-01 00:00:00
"""

from __future__ import annotations

import re
import unicodedata

from alembic import op
import sqlalchemy as sa


revision = "a3d5f7c9e1b2"
down_revision = "f8c4a2d91e70"
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
    rows = connection.execute(
        sa.select(table.c[id_name], table.c[source_name])
    ).all()
    for row_id, source_value in rows:
        connection.execute(
            table.update()
            .where(table.c[id_name] == row_id)
            .values(**{target_name: _fold_name(source_value)})
        )


def upgrade():
    # Add as nullable while existing rows are backfilled; make the keys
    # required only after every legacy record has a deterministic value.
    with op.batch_alter_table("players") as batch_op:
        batch_op.add_column(
            sa.Column("normalized_name_folded", sa.String(length=200), nullable=True)
        )
    with op.batch_alter_table("player_aliases") as batch_op:
        batch_op.add_column(
            sa.Column("normalized_alias_folded", sa.String(length=200), nullable=True)
        )

    _backfill("players", "id", "full_name", "normalized_name_folded")
    _backfill("player_aliases", "id", "alias", "normalized_alias_folded")

    with op.batch_alter_table("players") as batch_op:
        batch_op.alter_column(
            "normalized_name_folded",
            existing_type=sa.String(length=200),
            nullable=False,
        )
        batch_op.create_index(
            "ix_players_normalized_name_folded",
            ["normalized_name_folded"],
            unique=False,
        )
    with op.batch_alter_table("player_aliases") as batch_op:
        batch_op.alter_column(
            "normalized_alias_folded",
            existing_type=sa.String(length=200),
            nullable=False,
        )
        batch_op.create_index(
            "ix_player_aliases_normalized_alias_folded",
            ["normalized_alias_folded"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("player_aliases") as batch_op:
        batch_op.drop_index("ix_player_aliases_normalized_alias_folded")
        batch_op.drop_column("normalized_alias_folded")
    with op.batch_alter_table("players") as batch_op:
        batch_op.drop_index("ix_players_normalized_name_folded")
        batch_op.drop_column("normalized_name_folded")
