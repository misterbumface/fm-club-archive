"""A person's bio, written from the save data (docs/player-bios-plan.md).

It lives in the main database, keyed by FM unique id, because the world database is
rebuilt from the saves and a bio is written content.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

GENERATED = "generated"
CURATOR_EDIT = "curator_edit"


class PersonBio(db.Model):
    __tablename__ = "person_bios"

    fm_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)  # about 100 words, under the name
    profile: Mapped[str] = mapped_column(Text, nullable=False)  # 450-900 words with headings, plain text
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=GENERATED)
    model: Mapped[str | None] = mapped_column(String(80))
    game_date: Mapped[date | None] = mapped_column(Date)  # the game date of the facts it was written from
    written_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))


class PersonOldText(db.Model):
    """A bio from the old archive (FM's own text read from a screenshot, or the old AI rewrite),
    kept as colour for the bio writer."""

    __tablename__ = "person_old_texts"

    fm_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)  # the archive's text
    raw_source: Mapped[str | None] = mapped_column(Text)  # FM's own words, when the archive had them
    source: Mapped[str | None] = mapped_column(String(40))  # how the old archive made it
