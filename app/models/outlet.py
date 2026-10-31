"""A news outlet: a voice and a beat that writes editions from the story finder's leads
(docs/media-plan.md). The curator renames outlets and changes their voices on Settings › Outlets.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.mixins import TimestampMixin


class Outlet(TimestampMixin, db.Model):
    __tablename__ = "outlets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    tagline: Mapped[str | None] = mapped_column(String(300))
    # How the outlet writes; goes into the writer's prompt.
    voice: Mapped[str] = mapped_column(Text, nullable=False)
    # The leads it takes, by story_leads beat: "world", "nation:765", "transfers", "young", "history", "numbers", "home".
    beats: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    byline: Mapped[str] = mapped_column(String(160), nullable=False)  # for articles without a writer
    articles_per_edition: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    min_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)  # leads below this are not news for it
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    writers: Mapped[list["Writer"]] = relationship(back_populates="outlet", order_by="Writer.sort_order, Writer.id")

    @property
    def staff(self) -> list["Writer"]:
        """The writers who write for it now."""
        return [w for w in self.writers if w.active]

    def takes(self, beats: set[str]) -> bool:
        """True when a lead with these beats is on this outlet's beat."""
        return bool(set(self.beats) & set(beats))


class Writer(TimestampMixin, db.Model):
    """A journalist: a fictional person with a persona and a style, on one outlet's staff."""

    __tablename__ = "writers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    outlet_id: Mapped[int] = mapped_column(ForeignKey("outlets.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    persona: Mapped[str] = mapped_column(Text, nullable=False)  # who they are: age, background, loyalties, grudges
    style: Mapped[str] = mapped_column(Text, nullable=False)  # how they write
    quirks: Mapped[str | None] = mapped_column(Text)  # habits, pet subjects, favourite words
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    outlet: Mapped[Outlet] = relationship(back_populates="writers")

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "persona": self.persona, "style": self.style, "quirks": self.quirks or ""}
