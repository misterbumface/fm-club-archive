"""The social feed: accounts (clubs now; fans, players and journalists later) and their posts (docs/social-plan.md).

It lives in the main database, keyed by FM ids, because the world database is rebuilt from the saves. Each post
comes from one event of the save (a result, a transfer, a title, a manager's change), named by ``event_key``, so a
second import of the same days posts nothing twice.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db

CLUB = "club"  # account kinds
FAN = "fan"
PLAYER = "player"
JOURNALIST = "journalist"


class SocialAccount(db.Model):
    __tablename__ = "social_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    handle: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    club_uid: Mapped[int | None] = mapped_column(Integer, index=True)  # the club it belongs to or supports
    person_uid: Mapped[int | None] = mapped_column(Integer, index=True)
    persona: Mapped[dict | None] = mapped_column(JSON)  # a fan's character, for the writers
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))


class SocialPost(db.Model):
    __tablename__ = "social_posts"
    __table_args__ = (UniqueConstraint("account_id", "event_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("social_accounts.id"), nullable=False, index=True)
    posted_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)  # game date
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # result, signing, sale, loan_in, loan_out, title, manager_out, manager_in, reply
    event_key: Mapped[str] = mapped_column(String(80), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    links: Mapped[list | None] = mapped_column(JSON)  # [[name, "club"|"person"|"match", id], ...] linked in the text
    reply_to_id: Mapped[int | None] = mapped_column(ForeignKey("social_posts.id"), index=True)
    heat: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0-100: how much it stirs (a derby, an upset, a sale to a rival)
    prediction: Mapped[dict | None] = mapped_column(JSON)  # a fan's predicted score: {"match": "date:home:away", "home": 3, "away": 1}
    shown_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)  # UTC; a drip-fed reply is hidden until then
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reposts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    account: Mapped[SocialAccount] = relationship(lazy="joined")
