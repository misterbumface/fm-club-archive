"""Editorial image catalogue used by the World Soccer newsroom.

The catalogue is intentionally small and boring: an image keeps its stable
internal URL, while links describe the people, clubs, competitions, seasons
and matches that make it useful.  This lets the writer choose an image for a
story without confusing a club crest, stadium, and match photograph.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable
from urllib.parse import urlparse

from app.extensions import db
from app.models import ArchiveImage, ArchiveImageLink

ALLOWED_KINDS = {
    "portrait",
    "photograph",
    "matchday",
    "stadium",
    "supporters",
    "training",
    "historical",
    "celebration",
    "manager",
    "crest",
    "flag",
    "trophy",
    "competition_badge",
    "competition_banner",
    "competition_logo",
}
ALLOWED_REPEAT_POLICIES = {"avoid_recent", "unrestricted", "one_time"}


def normalize_archive_url(value: str | None) -> str | None:
    """Return a safe archive URL, preserving old internal URLs verbatim.

    World Soccer pages have historically stored both ``/static/...`` and
    ``/logos/...`` paths.  Absolute localhost URLs are reduced to their path;
    other remote URLs are left out of the catalogue so a CDN change cannot
    silently become a new trusted archive source.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme or parsed.netloc:
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            return None
        text = parsed.path or "/"
    if not text.startswith("/"):
        text = f"/{text}"
    if text.startswith("//"):
        return None
    return text


def register_archive_image(
    url: str | None,
    *,
    kind: str = "photograph",
    caption: str | None = None,
    alt_text: str | None = None,
    source: str = "upload",
    links: Iterable[dict[str, Any] | tuple[str, int, str | None]] = (),
    captured_on: date | str | None = None,
    era_start_year: int | None = None,
    era_end_year: int | None = None,
    tags: Iterable[str] | None = None,
    priority: int = 0,
    repeat_policy: str = "avoid_recent",
    notes: str | None = None,
) -> ArchiveImage | None:
    """Upsert an asset and its contextual links without committing.

    Callers can add the returned row to a larger transaction.  URL uniqueness
    makes repeated imports safe and allows the migration to coexist with
    uploads performed before the catalogue existed.
    """
    normalized = normalize_archive_url(url)
    if not normalized:
        return None
    kind = str(kind or "photograph").strip().lower()
    if kind not in ALLOWED_KINDS:
        kind = "photograph"
    repeat_policy = str(repeat_policy or "avoid_recent").strip().lower()
    if repeat_policy not in ALLOWED_REPEAT_POLICIES:
        repeat_policy = "avoid_recent"
    asset = ArchiveImage.query.filter_by(url=normalized).first()
    if asset is None:
        asset = ArchiveImage(url=normalized, kind=kind, source=source or "upload")
        db.session.add(asset)
    asset.kind = kind
    if caption is not None:
        asset.caption = str(caption).strip()[:500] or None
    if alt_text is not None:
        asset.alt_text = str(alt_text).strip()[:500] or None
    elif asset.alt_text is None and asset.caption:
        asset.alt_text = asset.caption
    if source:
        asset.source = str(source).strip()[:40] or "upload"
    if captured_on:
        if isinstance(captured_on, str):
            try:
                captured_on = date.fromisoformat(captured_on[:10])
            except ValueError:
                captured_on = None
        asset.captured_on = captured_on
    if era_start_year is not None:
        asset.era_start_year = int(era_start_year)
    if era_end_year is not None:
        asset.era_end_year = int(era_end_year)
    if tags is not None:
        clean_tags = []
        for tag in tags:
            clean = str(tag).strip().casefold()
            if clean and clean not in clean_tags:
                clean_tags.append(clean[:50])
        asset.tags_json = clean_tags[:30]
    asset.priority = int(priority or 0)
    asset.repeat_policy = repeat_policy
    if notes is not None:
        asset.notes = str(notes).strip()[:5000] or None
    asset.is_active = True
    db.session.flush()
    for raw_link in links:
        if isinstance(raw_link, dict):
            entity_type = str(raw_link.get("entity_type") or "").strip()
            entity_id = raw_link.get("entity_id")
            relation = str(raw_link.get("relation") or "subject").strip()
            label = raw_link.get("label")
        else:
            entity_type, entity_id, relation = raw_link
            label = None
        try:
            entity_id = int(entity_id)
        except (TypeError, ValueError):
            continue
        if not entity_type or not entity_id:
            continue
        link = ArchiveImageLink.query.filter_by(
            image_id=asset.id,
            entity_type=entity_type,
            entity_id=entity_id,
            relation=relation or "subject",
        ).first()
        if link is None:
            link = ArchiveImageLink(
                image_id=asset.id,
                entity_type=entity_type,
                entity_id=entity_id,
                relation=relation or "subject",
            )
            db.session.add(link)
        if label:
            link.label = str(label).strip()[:240]
    return asset


def asset_for_url(url: str | None) -> ArchiveImage | None:
    normalized = normalize_archive_url(url)
    return ArchiveImage.query.filter_by(url=normalized).first() if normalized else None


def assets_for_entity(entity_type: str, entity_id: int, *, include_inactive: bool = False):
    query = (
        ArchiveImage.query.join(ArchiveImageLink)
        .filter(
            ArchiveImageLink.entity_type == entity_type,
            ArchiveImageLink.entity_id == int(entity_id),
        )
        .order_by(ArchiveImage.priority.desc(), ArchiveImage.id)
    )
    if not include_inactive:
        query = query.filter(ArchiveImage.is_active.is_(True))
    return query.all()


def retire_url(url: str | None) -> None:
    asset = asset_for_url(url)
    if asset is not None:
        asset.is_active = False



def images_of(ref: str, kinds: tuple[str, ...] | None = None) -> list[ArchiveImage]:
    """The active archive images tied to one world reference, best first."""
    query = (db.session.query(ArchiveImage).join(ArchiveImageLink)
             .filter(ArchiveImageLink.world_ref == ref, ArchiveImage.is_active.is_(True)))
    if kinds:
        query = query.filter(ArchiveImage.kind.in_(kinds))
    return query.order_by(ArchiveImage.priority.desc(), ArchiveImage.id.desc()).all()
