"""Browser newsroom and immutable back issues for World Soccer."""

from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import WorldSoccerArticle, WorldSoccerIssue, WorldSoccerStoryThread
from app.services import story_leads, world_soccer

world_soccer_bp = Blueprint(
    "world_soccer", __name__, url_prefix="/world-soccer"
)


@world_soccer_bp.route("/")
def index():
    issue = (
        WorldSoccerIssue.query.filter_by(status="published")
        .order_by(
            WorldSoccerIssue.publication_date.desc(),
            WorldSoccerIssue.id.desc(),
        )
        .first()
    )
    if issue is None:
        issue = (
            WorldSoccerIssue.query.filter_by(status="draft")
            .order_by(
                WorldSoccerIssue.publication_date.desc(),
                WorldSoccerIssue.id.desc(),
            )
            .first()
        )
    if issue is None:
        return render_template("world_soccer/empty.html", game_date=world_soccer._game_date())
    return _render_issue(issue)


@world_soccer_bp.route("/issues")
def issues():
    published = (
        WorldSoccerIssue.query.filter_by(status="published")
        .order_by(
            WorldSoccerIssue.publication_date.desc(),
            WorldSoccerIssue.id.desc(),
        )
        .all()
    )
    drafts = (
        WorldSoccerIssue.query.filter_by(status="draft")
        .order_by(
            WorldSoccerIssue.publication_date.desc(),
            WorldSoccerIssue.id.desc(),
        )
        .all()
    )
    return render_template(
        "world_soccer/issues.html", published=published, drafts=drafts
    )


@world_soccer_bp.route("/issues/<int:issue_id>")
def issue(issue_id: int):
    row = db.session.get(WorldSoccerIssue, issue_id)
    if row is None:
        abort(404)
    return _render_issue(row)


@world_soccer_bp.route("/newsroom")
def newsroom():
    draft_id = request.args.get("issue", type=int)
    draft = db.session.get(WorldSoccerIssue, draft_id) if draft_id else None
    if draft is None or draft.status != "draft":
        draft = (
            WorldSoccerIssue.query.filter_by(status="draft")
            .order_by(
                WorldSoccerIssue.publication_date.desc(),
                WorldSoccerIssue.id.desc(),
            )
            .first()
        )
    return render_template(
        "world_soccer/newsroom.html",
        draft=draft,
        drafts=(
            WorldSoccerIssue.query.filter_by(status="draft")
            .order_by(WorldSoccerIssue.publication_date.desc())
            .all()
        ),
        threads=(
            WorldSoccerStoryThread.query.order_by(
                WorldSoccerStoryThread.status,
                WorldSoccerStoryThread.updated_at.desc(),
            ).all()
        ),
        suggested_date=world_soccer._game_date(),
    )


@world_soccer_bp.route("/issues", methods=["POST"])
def create_issue():
    try:
        result = world_soccer.create_issue(
            publication_date=request.form.get("publication_date"),
            knowledge_cutoff=request.form.get("knowledge_cutoff"),
            edition_name=request.form.get("edition_name") or "World Soccer",
            headline=request.form.get("headline"),
            standfirst=request.form.get("standfirst"),
            cover_image_url=request.form.get("cover_image_url"),
        )
        flash(result["message"], "success")
        return redirect(
            url_for("world_soccer.newsroom", issue=result["id"])
        )
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("world_soccer.newsroom"))


@world_soccer_bp.route(
    "/issues/<int:issue_id>/articles", methods=["POST"]
)
def add_article(issue_id: int):
    return _save_article(issue_id)


@world_soccer_bp.route(
    "/issues/<int:issue_id>/articles/<int:article_id>", methods=["POST"]
)
def edit_article(issue_id: int, article_id: int):
    return _save_article(issue_id, article_id=article_id)


@world_soccer_bp.route(
    "/articles/<int:article_id>/delete", methods=["POST"]
)
def delete_article(article_id: int):
    try:
        article = db.session.get(WorldSoccerArticle, article_id)
        issue_id = article.issue_id if article else None
        world_soccer.delete_article(article_id)
        flash("Story removed from the draft.", "success")
        return redirect(url_for("world_soccer.newsroom", issue=issue_id))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("world_soccer.newsroom"))


@world_soccer_bp.route(
    "/issues/<int:issue_id>/publish", methods=["POST"]
)
def publish(issue_id: int):
    try:
        result = world_soccer.publish_issue(issue_id)
        flash(result["message"], "success")
        return redirect(url_for("world_soccer.issue", issue_id=issue_id))
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("world_soccer.newsroom", issue=issue_id))


def _save_article(issue_id: int, *, article_id: int | None = None):
    try:
        result = world_soccer.write_article(
            issue_id,
            article_id=article_id,
            headline=request.form.get("headline") or "",
            standfirst=request.form.get("standfirst"),
            body=request.form.get("body") or "",
            section=request.form.get("section") or "World",
            article_type=request.form.get("article_type") or "report",
            byline=request.form.get("byline") or "World Soccer",
            image_url=request.form.get("image_url"),
            image_caption=request.form.get("image_caption"),
            prominence=request.form.get("prominence") or "standard",
            sort_order=request.form.get("sort_order", type=int) or 0,
            entity_links=_parse_entity_links(request.form.get("entity_links")),
            story_thread_id=request.form.get("story_thread_id", type=int),
            story_thread_title=request.form.get("story_thread_title"),
        )
        flash(result["message"], "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("world_soccer.newsroom", issue=issue_id))


def _parse_entity_links(value: str | None) -> list[dict]:
    links = []
    for line in (value or "").splitlines():
        parts = [part.strip() for part in line.split(":", 2)]
        if len(parts) < 2 or not parts[1].isdigit():
            continue
        links.append(
            {
                "entity_type": parts[0],
                "entity_id": int(parts[1]),
                "label": parts[2] if len(parts) > 2 else None,
            }
        )
    return links


def _render_issue(issue: WorldSoccerIssue):
    people = world_soccer.person_link_targets(issue)
    articles = [
        {
            "row": article,
            "linked_body": world_soccer.link_person_mentions(article.body, people),
            "links": [
                world_soccer.entity_link_context(link)
                for link in article.entity_links
            ],
        }
        for article in issue.articles
    ]
    return render_template(
        "world_soccer/issue.html",
        issue=issue,
        articles=articles,
        threads=(
            WorldSoccerStoryThread.query.filter_by(status="ongoing")
            .order_by(WorldSoccerStoryThread.updated_at.desc())
            .limit(6)
            .all()
        ),
    )



@world_soccer_bp.route("/leads")
def leads():
    """The story finder's leads for one window, ranked: what the outlets would write about."""
    from datetime import date, timedelta

    from app.models import world as world_models
    from app.services import world_queries

    with db.engines[world_models.BIND].connect() as connection:
        today = world_queries.game_date(connection)
        try:
            until = date.fromisoformat(request.args["until"]) if request.args.get("until") else today
            since = date.fromisoformat(request.args["since"]) if request.args.get("since") else None
        except ValueError:
            abort(400)
        if since is None and until is not None:  # no save imported yet: no game date, no leads
            last = (WorldSoccerIssue.query.filter_by(status="published").order_by(WorldSoccerIssue.knowledge_cutoff.desc()).first())
            since = last.knowledge_cutoff if last and last.knowledge_cutoff and last.knowledge_cutoff < until else until - timedelta(days=98)
        found = story_leads.find_leads(connection, since, until) if until else []
        nations = {n["fm_uid"]: n["name"] for n in world_queries._rows(connection, "select fm_uid, name from nations")}
    from app.services import article_writer, outlets as outlet_service

    kind = request.args.get("kind")
    shown = [lead for lead in found if not kind or lead.kind == kind]
    all_outlets = outlet_service.ensure_outlets()
    rows = [{"lead": lead, "outlets": outlet_service.outlets_for(lead, all_outlets), "data": article_writer.lead_to_dict(lead)}
            for lead in shown]
    return render_template("world_soccer/leads.html", rows=rows, leads=shown, all_kinds=sorted({lead.kind for lead in found}),
                           kind=kind, since=since, until=until, today=today, nations=nations, ref_path=_ref_path,
                           prominences=article_writer.PROMINENCE)


@world_soccer_bp.route("/leads/write", methods=["POST"])
def write_lead():
    """Write one lead as an article for one outlet and show the draft (editions keep drafts in step 4)."""
    import json
    from datetime import date

    from app.models.outlet import Outlet
    from app.services import article_writer
    from app.services.story_writer import StoryError

    outlet = db.session.get(Outlet, request.form.get("outlet_id", type=int) or 0)
    try:
        lead = article_writer.lead_from_dict(json.loads(request.form["lead"]))
        published = date.fromisoformat(request.form["published"])
    except (KeyError, ValueError, TypeError):
        abort(400)
    if outlet is None:
        abort(404)
    prominence = request.form.get("prominence") or "standard"
    try:
        article = article_writer.write_article(outlet, lead, published, prominence)
    except StoryError as exc:
        return render_template("world_soccer/draft.html", outlet=outlet, lead=lead, error=str(exc), article=None,
                               published=published, prominence=prominence, lead_json=request.form["lead"],
                               prominences=article_writer.PROMINENCE), 502
    return render_template("world_soccer/draft.html", outlet=outlet, lead=lead, error=None, article=article, published=published,
                           prominence=prominence, lead_json=request.form["lead"], prominences=article_writer.PROMINENCE,
                           notebook=json.dumps(article["notebook"], ensure_ascii=False, indent=2, default=str))


def _ref_path(ref: str) -> str | None:
    kind, *parts = ref.split(":")
    return {"person": f"/people/{parts[0]}", "club": f"/clubs/{parts[0]}", "competition": f"/competitions/{parts[0]}",
            "edition": f"/competitions/{parts[0]}/{parts[-1]}", "nation": f"/nations/{parts[0]}"}.get(kind)
