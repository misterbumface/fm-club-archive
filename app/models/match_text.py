"""A match report or preview, written for a world fixture (docs/mcp-plan.md).

It lives in the main database, keyed by the match's date and its two teams (FM ids),
because the world database is rebuilt from the saves and its row ids can change.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

REPORT = "report"
PREVIEW = "preview"
GENERATED = "generated"  # sources: written by the archive's AI writer
CURATOR = "curator"  # by the curator, in chat or by hand
GAME = "game"  # Football Manager's own text
OLD_ARCHIVE = "old_archive"  # moved from the old archive


class MatchText(db.Model):
    __tablename__ = "match_texts"
    __table_args__ = (UniqueConstraint("date", "home_uid", "away_uid", "kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    home_uid: Mapped[int] = mapped_column(Integer, nullable=False)
    away_uid: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # report or preview
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=CURATOR)
    written_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))
