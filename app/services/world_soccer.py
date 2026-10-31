"""Persistent editorial newsroom for the archive's World Soccer newspaper.

These are ordinary document operations, not repairs. Drafts are directly
editable and published issues are frozen so yesterday's newspaper remains part
of the universe's memory.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from markupsafe import Markup, escape

from app.extensions import db
from app.models import (
    ArchiveImage,
    WorldSoccerArticle,
    WorldSoccerEntityLink,
    WorldSoccerImageUse,
    WorldSoccerIssue,
    WorldSoccerStoryThread,
)
from app.services.image_library import asset_for_url

ISSUE_STATUSES = {"draft", "published"}
ARTICLE_TYPES = {"report", "analysis", "profile", "column", "brief"}
PROMINENCE_VALUES = {"lead", "major", "standard", "brief"}
THREAD_STATUSES = {"ongoing", "concluded"}


def list_issues(*, status: str | None = None, limit: int = 50) -> dict[str, Any]:
    query = WorldSoccerIssue.query
    if status:
        if status not in ISSUE_STATUSES:
            raise ValueError("Issue status must be draft or published.")
        query = query.filter_by(status=status)
    issues = query.order_by(
        WorldSoccerIssue.publication_date.desc(), WorldSoccerIssue.id.desc()
    ).limit(max(1, min(int(limit), 100))).all()
    return {
        "count": len(issues),
        "issues": [serialize_issue(issue, include_articles=False) for issue in issues],
        "path": "/world-soccer/issues",
    }


def get_issue(issue_id: int) -> dict[str, Any]:
    return serialize_issue(_get(WorldSoccerIssue, issue_id, "World Soccer issue"))


def create_issue(
    *,
    publication_date: str | date | None = None,
    knowledge_cutoff: str | date | None = None,
    edition_name: str = "World Soccer",
    headline: str | None = None,
    standfirst: str | None = None,
    cover_image_url: str | None = None,
    cover_image_asset_id: int | None = None,
) -> dict[str, Any]:
    clock_date = _game_date()
    published_on = _as_date(publication_date) or clock_date or date.today()
    cutoff = _as_date(knowledge_cutoff) or published_on
    if clock_date and cutoff > clock_date:
        raise ValueError(
            f"The issue cannot know results beyond the archive date, {clock_date.isoformat()}."
        )
    if cutoff > published_on:
        raise ValueError("Knowledge cutoff cannot be after publication date.")
    clean_edition = _required(edition_name, "Edition name is required.")
    cover_asset = None
    if cover_image_asset_id is not None:
        cover_asset = _get(ArchiveImage, int(cover_image_asset_id), "archive image")
        if not cover_asset.is_active:
            raise ValueError("That archive image has been retired.")
        cover_image_url = cover_asset.url
    elif cover_image_url:
        cover_asset = asset_for_url(cover_image_url)
    existing = (
        WorldSoccerIssue.query.filter_by(
            publication_date=published_on,
            edition_name=clean_edition,
            status="draft",
        )
        .order_by(WorldSoccerIssue.id.desc())
        .first()
    )
    if existing is not None:
        if headline is not None:
            existing.headline = _optional(headline)
        if standfirst is not None:
            existing.standfirst = _optional(standfirst)
        if cover_image_url is not None:
            existing.cover_image_url = _optional(cover_image_url)
            existing.cover_image_asset_id = cover_asset.id if cover_asset else None
        existing.knowledge_cutoff = cutoff
        db.session.flush()
        existing_asset = cover_asset or existing.cover_image_asset or asset_for_url(existing.cover_image_url)
        db.session.query(WorldSoccerImageUse).filter_by(issue_id=existing.id).delete(
            synchronize_session=False
        )
        if existing.cover_image_url:
            db.session.add(
                WorldSoccerImageUse(
                    image_id=existing_asset.id if existing_asset else None,
                    issue_id=existing.id,
                    image_url=existing.cover_image_url,
                    story_type="cover",
                    selection_reason="World Soccer edition cover",
                )
            )
        db.session.commit()
        result = serialize_issue(existing)
        result["created"] = False
        result["message"] = "Updated the existing draft for this date."
        return result

    issue = WorldSoccerIssue(
        publication_date=published_on,
        knowledge_cutoff=cutoff,
        edition_name=clean_edition,
        headline=_optional(headline),
        standfirst=_optional(standfirst),
        cover_image_url=_optional(cover_image_url),
        cover_image_asset_id=cover_asset.id if cover_asset else None,
        status="draft",
    )
    db.session.add(issue)
    db.session.flush()
    if issue.cover_image_url:
        db.session.add(
            WorldSoccerImageUse(
                image_id=cover_asset.id if cover_asset else None,
                issue_id=issue.id,
                image_url=issue.cover_image_url,
                story_type="cover",
                selection_reason="World Soccer edition cover",
            )
        )
    db.session.commit()
    result = serialize_issue(issue)
    result["created"] = True
    result["message"] = "Created a new World Soccer draft."
    return result


def write_article(
    issue_id: int,
    *,
    headline: str,
    body: str,
    article_id: int | None = None,
    section: str = "World",
    article_type: str = "report",
    standfirst: str | None = None,
    byline: str = "World Soccer",
    image_url: str | None = None,
    image_asset_id: int | None = None,
    image_caption: str | None = None,
    prominence: str = "standard",
    sort_order: int = 0,
    entity_links: list[dict[str, Any]] | None = None,
    story_thread_id: int | None = None,
    story_thread_title: str | None = None,
    story_thread_summary: str | None = None,
    story_thread_status: str | None = None,
) -> dict[str, Any]:
    issue = _get(WorldSoccerIssue, issue_id, "World Soccer issue")
    _require_draft(issue)
    if article_type not in ARTICLE_TYPES:
        raise ValueError(f"Article type must be one of {sorted(ARTICLE_TYPES)}.")
    if prominence not in PROMINENCE_VALUES:
        raise ValueError(f"Prominence must be one of {sorted(PROMINENCE_VALUES)}.")
    if article_id is not None:
        article = _get(WorldSoccerArticle, article_id, "World Soccer article")
        if article.issue_id != issue.id:
            raise ValueError("That article belongs to a different issue.")
        created = False
    else:
        article = WorldSoccerArticle(issue=issue)
        db.session.add(article)
        created = True

    # Fill every non-null article field before any thread lookup can trigger
    # SQLAlchemy's query autoflush.
    article.section = _required(section, "Section is required.")
    article.article_type = article_type
    article.headline = _required(headline, "Headline is required.")
    article.standfirst = _optional(standfirst)
    article.body = _required(body, "Article body is required.")
    article.byline = _required(byline, "Byline is required.")
    article_asset = None
    if image_asset_id is not None:
        article_asset = _get(ArchiveImage, int(image_asset_id), "archive image")
        if not article_asset.is_active:
            raise ValueError("That archive image has been retired.")
        image_url = article_asset.url
    elif image_url:
        article_asset = asset_for_url(image_url)
    article.image_url = _optional(image_url)
    article.image_caption = _optional(image_caption)
    article.image_asset_id = article_asset.id if article_asset else None
    article.prominence = prominence
    article.sort_order = int(sort_order or 0)

    thread = None
    if story_thread_id is not None:
        thread = _get(
            WorldSoccerStoryThread, story_thread_id, "World Soccer story thread"
        )
    elif _optional(story_thread_title):
        clean_title = _required(story_thread_title, "Story thread title is required.")
        thread = (
            WorldSoccerStoryThread.query.filter(
                WorldSoccerStoryThread.title.ilike(clean_title)
            ).first()
        )
        if thread is None:
            thread = WorldSoccerStoryThread(
                title=clean_title,
                summary=_optional(story_thread_summary),
                status="ongoing",
                started_on=issue.publication_date,
            )
            db.session.add(thread)
            db.session.flush()
    if story_thread_status is not None:
        if story_thread_status not in THREAD_STATUSES:
            raise ValueError(
                "Story thread status must be ongoing or concluded."
            )
        if thread is None:
            raise ValueError(
                "Choose or create a story thread before changing its status."
            )
        thread.status = story_thread_status
        thread.concluded_on = (
            issue.publication_date
            if story_thread_status == "concluded"
            else None
        )
    article.story_thread = thread
    db.session.flush()

    if entity_links is not None:
        article.entity_links.clear()
        db.session.flush()
        seen: set[tuple[str, int]] = set()
        for raw_link in entity_links:
            entity_type = str(raw_link.get("entity_type") or "").strip()
            try:
                entity_id = int(raw_link.get("entity_id"))
            except (TypeError, ValueError) as exc:
                raise ValueError("Every article entity link needs an integer ID.") from exc
            key = (entity_type, entity_id)
            if key in seen:
                continue
            ref = world_ref_for(entity_type, entity_id, raw_link.get("year"))
            article.entity_links.append(
                WorldSoccerEntityLink(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    world_ref=ref,
                    label=_optional(raw_link.get("label")) or world_ref_context(ref)["label"],
                )
            )
            seen.add(key)
    db.session.query(WorldSoccerImageUse).filter_by(article_id=article.id).delete(
        synchronize_session=False
    )
    if article.image_url:
        db.session.add(
            WorldSoccerImageUse(
                image_id=article_asset.id if article_asset else None,
                article_id=article.id,
                issue_id=issue.id,
                image_url=article.image_url,
                story_type=article.article_type,
                selection_reason="Article image supplied by the newsroom curator",
            )
        )
    db.session.commit()
    return {
        "changed": True,
        "created": created,
        "article": serialize_article(article),
        "message": "Story added to the draft." if created else "Story updated.",
        "path": f"/world-soccer/issues/{issue.id}#article-{article.id}",
    }


def delete_article(article_id: int) -> dict[str, Any]:
    article = _get(WorldSoccerArticle, article_id, "World Soccer article")
    _require_draft(article.issue)
    issue_id = article.issue_id
    headline = article.headline
    db.session.delete(article)
    db.session.commit()
    return {
        "changed": True,
        "deleted_article_id": article_id,
        "headline": headline,
        "path": f"/world-soccer/issues/{issue_id}",
    }


def update_story_thread(
    thread_id: int,
    *,
    title: str | None = None,
    summary: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    thread = _get(WorldSoccerStoryThread, thread_id, "World Soccer story thread")
    if title is not None:
        thread.title = _required(title, "Story thread title is required.")
    if summary is not None:
        thread.summary = _optional(summary)
    if status is not None:
        if status not in THREAD_STATUSES:
            raise ValueError("Story thread status must be ongoing or concluded.")
        thread.status = status
        if status == "concluded":
            thread.concluded_on = _game_date() or date.today()
        else:
            thread.concluded_on = None
    db.session.commit()
    return {"changed": True, "story_thread": serialize_thread(thread)}


def publish_issue(issue_id: int) -> dict[str, Any]:
    issue = _get(WorldSoccerIssue, issue_id, "World Soccer issue")
    if issue.status == "published":
        result = serialize_issue(issue)
        result["changed"] = False
        result["message"] = "This issue is already published."
        return result
    if not issue.articles:
        raise ValueError("Add at least one story before publishing this issue.")
    if not issue.headline:
        issue.headline = next(
            (
                article.headline
                for article in issue.articles
                if article.prominence == "lead"
            ),
            issue.articles[0].headline,
        )
    issue.status = "published"
    issue.published_at = datetime.now()
    db.session.commit()
    result = serialize_issue(issue)
    result["changed"] = True
    result["message"] = "Published. This edition is now part of the archive's memory."
    return result


def serialize_issue(
    issue: WorldSoccerIssue, *, include_articles: bool = True
) -> dict[str, Any]:
    result = {
        "id": issue.id,
        "publication_date": issue.publication_date.isoformat(),
        "knowledge_cutoff": issue.knowledge_cutoff.isoformat(),
        "edition_name": issue.edition_name,
        "headline": issue.headline,
        "standfirst": issue.standfirst,
        "cover_image_url": issue.cover_image_url,
        "cover_image_asset_id": issue.cover_image_asset_id,
        "status": issue.status,
        "published_at": issue.published_at.isoformat() if issue.published_at else None,
        "article_count": len(issue.articles),
        "path": f"/world-soccer/issues/{issue.id}",
    }
    if include_articles:
        result["articles"] = [serialize_article(article) for article in issue.articles]
    return result


def serialize_article(article: WorldSoccerArticle) -> dict[str, Any]:
    return {
        "id": article.id,
        "issue_id": article.issue_id,
        "section": article.section,
        "article_type": article.article_type,
        "headline": article.headline,
        "standfirst": article.standfirst,
        "body": article.body,
        "byline": article.byline,
        "image_url": article.image_url,
        "image_asset_id": article.image_asset_id,
        "image_caption": article.image_caption,
        "prominence": article.prominence,
        "sort_order": article.sort_order,
        "story_thread": (
            serialize_thread(article.story_thread)
            if article.story_thread else None
        ),
        "entity_links": [
            entity_link_context(link) for link in article.entity_links
        ],
        "path": f"/world-soccer/issues/{article.issue_id}#article-{article.id}",
    }


def serialize_thread(thread: WorldSoccerStoryThread) -> dict[str, Any]:
    return {
        "id": thread.id,
        "title": thread.title,
        "summary": thread.summary,
        "status": thread.status,
        "started_on": thread.started_on.isoformat() if thread.started_on else None,
        "concluded_on": (
            thread.concluded_on.isoformat() if thread.concluded_on else None
        ),
        "article_count": len(thread.articles),
    }


WORLD_LINK_TYPES = ("person", "club", "competition", "edition", "club_season", "match", "nation")


def _world_connection():
    from app.models import world as world_models

    return db.engines[world_models.BIND].connect()


def world_ref_for(entity_type: str, entity_id: int, year: int | None = None) -> str:
    """The world reference of a new link: FM ids, an edition or season year, or a match id."""
    from app.services import world_queries

    if entity_type not in WORLD_LINK_TYPES:
        raise ValueError(f"Entity type must be one of {', '.join(WORLD_LINK_TYPES)}.")
    if entity_type in ("edition", "club_season"):
        if year is None:
            raise ValueError(f"A {entity_type} link needs a year: the edition's end year or the season's start year.")
        return f"{entity_type}:{entity_id}:{int(year)}"
    if entity_type == "match":
        with _world_connection() as connection:
            fixture = world_queries._one(connection, "select date, home_uid, away_uid from fixtures where id = :id", id=entity_id)
        if fixture is None:
            raise ValueError(f"No match with id {entity_id}.")
        return f"match:{fixture['date']}:{fixture['home_uid']}:{fixture['away_uid']}"
    return f"{entity_type}:{entity_id}"


def world_ref_context(ref: str, label: str | None = None) -> dict[str, Any]:
    """The page path and a label of a world reference (see WorldSoccerEntityLink.world_ref)."""
    from app.services import world_queries

    kind, *parts = ref.split(":")
    path, name = None, None
    with _world_connection() as connection:
        if kind == "person":
            person = world_queries.person(connection, int(parts[0]))
            path, name = f"/people/{parts[0]}", person and person["name"]
        elif kind in ("club", "club_season"):
            team = world_queries.teams(connection, [int(parts[0])]).get(int(parts[0]))
            path = f"/clubs/{parts[0]}" + (f"/{parts[1]}" if kind == "club_season" else "")
            name = team and team["name"] + (f" {world_queries.season_label(int(parts[1]))}" if kind == "club_season" else "")
        elif kind in ("competition", "edition"):
            comp = world_queries.competition(connection, int(parts[0]))
            path = f"/competitions/{parts[0]}" + (f"/{parts[1]}" if kind == "edition" else "")
            name = comp and comp["name"] + (f" {parts[1]}" if kind == "edition" else "")
        elif kind == "nation":
            nation = world_queries._one(connection, "select name from nations where fm_uid = :uid", uid=int(parts[0]))
            path, name = f"/nations/{parts[0]}", nation and nation["name"]
        elif kind == "match":
            fixture = world_queries._one(connection, "select id, home_uid, away_uid from fixtures where date = :d and home_uid = :h "
                                                     "and away_uid = :a", d=parts[0], h=int(parts[1]), a=int(parts[2]))
            if fixture:
                teams = world_queries.teams(connection, [fixture["home_uid"], fixture["away_uid"]])
                path = f"/matches/{fixture['id']}"
                name = f"{teams.get(fixture['home_uid'], {}).get('name')} v {teams.get(fixture['away_uid'], {}).get('name')}"
    return {"world_ref": ref, "label": label or name or ref, "path": path}


def entity_link_context(link: WorldSoccerEntityLink) -> dict[str, Any]:
    if link.world_ref:
        return {"entity_type": link.entity_type, "entity_id": link.entity_id, **world_ref_context(link.world_ref, link.label)}
    return {  # an old link the cut-over could not tie to the world: a label without a page
        "entity_type": link.entity_type,
        "entity_id": link.entity_id,
        "label": link.label or f"{link.entity_type} {link.entity_id}",
        "path": None,
    }


def _game_date() -> date | None:
    """The game date of the newest imported save."""
    from app.services import world_queries

    with _world_connection() as connection:
        return world_queries.game_date(connection)


def _require_draft(issue: WorldSoccerIssue) -> None:
    if issue.status != "draft":
        raise ValueError(
            "Published editions are preserved as historical memory. Create a new draft instead."
        )


def _get(model, row_id: int, label: str):
    row = db.session.get(model, row_id)
    if row is None:
        raise ValueError(f"No {label} exists with id {row_id}.")
    return row


def _as_date(value: str | date | None) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError("Use an ISO date in YYYY-MM-DD format.") from exc


def _required(value: Any, message: str) -> str:
    text = _optional(value)
    if not text:
        raise ValueError(message)
    return text


def _optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def person_link_targets(issue: WorldSoccerIssue) -> dict[str, str]:
    """Names of the people an issue's articles link to, and their pages."""
    targets: dict[str, str] = {}
    for article in issue.articles:
        for link in article.entity_links:
            if link.world_ref and link.world_ref.startswith("person:"):
                context = world_ref_context(link.world_ref)
                if context["path"] and context["label"]:
                    targets[context["label"]] = context["path"]
                    if link.label:
                        targets.setdefault(link.label, context["path"])
    return targets


def link_person_mentions(text: str | None, targets: dict[str, str]) -> Markup:
    """Escape the prose and link each mention of a linked person's name to the person's page."""
    if not text:
        return Markup("")
    if not targets:
        return escape(text)
    pattern = re.compile(r"(?<!\w)(" + "|".join(re.escape(name) for name in sorted(targets, key=len, reverse=True)) + r")(?!\w)")
    parts, last = [], 0
    for match in pattern.finditer(text):
        parts.append(escape(text[last:match.start()]))
        parts.append(Markup('<a href="{}">{}</a>').format(targets[match.group(1)], match.group(1)))
        last = match.end()
    parts.append(escape(text[last:]))
    return Markup("").join(parts)
