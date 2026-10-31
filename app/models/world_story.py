"""Stories written about the world, keyed by FM ids (docs/stories-plan.md).

They live in the main database because the world database is rebuilt from the saves.
A season is the year it starts (2062 for 2062/63; a calendar-year league's 2062 is 2062);
an edition year is the year it ends, as in the world database.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import Date, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db

GENERATED = "generated"
CURATOR_EDIT = "curator_edit"
OLD_ARCHIVE = "old_archive"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ClubSeasonStory(db.Model):
    """One feature about a club's season."""

    __tablename__ = "club_season_stories"

    club_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    season: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=GENERATED)
    model: Mapped[str | None] = mapped_column(String(80))
    game_date: Mapped[date | None] = mapped_column(Date)  # the game date of the facts it was written from
    written_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class ClubSeasonChapter(db.Model):
    """An old diary chapter of a club's season, kept as it was written."""

    __tablename__ = "club_season_chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    club_uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    covers_from: Mapped[date | None] = mapped_column(Date)
    covers_to: Mapped[date | None] = mapped_column(Date)
    old_id: Mapped[int | None] = mapped_column(Integer, unique=True)  # season_story_chapters.id it came from


class EditionStory(db.Model):
    """One story about an edition of a competition (a league season or a cup)."""

    __tablename__ = "edition_stories"

    competition_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=GENERATED)
    model: Mapped[str | None] = mapped_column(String(80))
    game_date: Mapped[date | None] = mapped_column(Date)
    written_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class EditionChapter(db.Model):
    """An old diary chapter of an edition, kept as it was written."""

    __tablename__ = "edition_chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    competition_uid: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    covers_from: Mapped[date | None] = mapped_column(Date)
    covers_to: Mapped[date | None] = mapped_column(Date)
    old_id: Mapped[int | None] = mapped_column(Integer, unique=True)  # competition_story_chapters.id it came from


class ClubText(db.Model):
    """A club's "About the club": a short summary and its history, by the curator or written by an AI model."""

    __tablename__ = "club_texts"

    club_uid: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    profile_summary: Mapped[str | None] = mapped_column(Text)
    historical_context: Mapped[str | None] = mapped_column(Text)
    written_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    source: Mapped[str | None] = mapped_column(String(20))  # GENERATED, or None for the curator's own text
    model: Mapped[str | None] = mapped_column(String(80))


class StoryVersion(db.Model):
    """An earlier text of a season or edition story, kept when a new one replaced it, so it can be restored."""

    __tablename__ = "story_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # "season" (a club's), "edition" (a competition's) or "club"
    subject_uid: Mapped[int] = mapped_column(Integer, nullable=False)  # the club or the competition
    year: Mapped[int] = mapped_column(Integer, nullable=False)  # the season start, the edition's year, or 0 (a club's text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str | None] = mapped_column(String(80))
    game_date: Mapped[date | None] = mapped_column(Date)
    written_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    replaced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    __table_args__ = (Index("ix_story_versions_subject", "kind", "subject_uid", "year"),)
