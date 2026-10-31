"""The media (docs/media-plan.md, step 4): the newsstand (home), the outlets' pages and editions,
and the desk where the curator edits the draft editions and publishes them."""

from __future__ import annotations

from datetime import date

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models.outlet import Outlet
from app.models.world_soccer_article import WorldSoccerArticle
from app.models.world_soccer_issue import WorldSoccerIssue
from app.services import editions, outlets as outlet_service, save_jobs, world_soccer
from app.services.story_writer import StoryError

news_bp = Blueprint("news", __name__)


def _published(outlet: Outlet):
    query = WorldSoccerIssue.query.filter_by(status="published")
    if outlet.slug == "world-soccer":
        query = query.filter((WorldSoccerIssue.outlet_id == outlet.id) | WorldSoccerIssue.outlet_id.is_(None))
    else:
        query = query.filter_by(outlet_id=outlet.id)
    return query.order_by(WorldSoccerIssue.publication_date.desc(), WorldSoccerIssue.id.desc())


def _picture(article: WorldSoccerArticle) -> tuple[str | None, bool]:
    """The article's picture, and whether it is only a crest (shown as a small badge): its own picture,
    the archive's pictures of its subjects (stadium, photograph, match day), a face, a trophy, the club's
    stadium from the game's stadium pack, a crest."""
    from app.routes.world import crest_url, face_url, stadium_url, trophy_url, world_pictures

    if article.image_url:
        return article.image_url, False
    refs = [link.world_ref for link in article.entity_links if link.world_ref]
    for ref in refs:
        found = [image for image in world_pictures(ref, ("photograph", "matchday", "stadium"))
                 if image.is_active and (image.url or "").startswith("/static/")]
        if found:
            return found[article.id % len(found)].url, False  # they rotate by the story's number, as on the site
    for ref in refs:
        kind, _, rest = ref.partition(":")
        uid = int(rest.split(":")[0]) if kind in ("person", "edition", "competition", "club") else None
        picture = face_url(uid) if kind == "person" else trophy_url(uid) if kind in ("edition", "competition") else None
        if picture:
            return picture, False
    for ref in refs:
        if ref.startswith("club:"):
            stadium = stadium_url(int(ref.split(":")[1]))
            if stadium:
                return stadium, False
    for ref in refs:
        if ref.startswith("club:"):
            crest = crest_url({"club_uid": int(ref.split(":")[1])})
            if crest:
                return crest, True
    return None, False


def _articles(issue: WorldSoccerIssue) -> list[dict]:
    """The issue's articles with their linked body, links, picture and thread."""
    people = world_soccer.person_link_targets(issue)
    rows = []
    for article in sorted(issue.articles, key=lambda a: (a.sort_order, a.id)):
        links = [world_soccer.entity_link_context(link) for link in article.entity_links]
        picture, badge = _picture(article)
        story = article.story_thread
        earlier = 0
        if story is not None:
            earlier = (WorldSoccerArticle.query.join(WorldSoccerIssue)
                       .filter(WorldSoccerArticle.story_thread_id == story.id, WorldSoccerIssue.status == "published",
                               WorldSoccerArticle.id != article.id).count())
        rows.append({"row": article, "linked_body": world_soccer.link_person_mentions(article.body, people), "links": links,
                     "picture": picture, "badge": badge, "thread": story if earlier else None})
    return rows


@news_bp.get("/")
def newsstand():
    """Every outlet's latest front page, and the drafts waiting on the desk."""
    stands = []
    for outlet in outlet_service.ensure_outlets():
        issue = _published(outlet).first()
        if outlet.active or issue:
            stands.append({"outlet": outlet, "issue": issue, "articles": _articles(issue)[:4] if issue else []})
    drafts = WorldSoccerIssue.query.filter_by(status="draft").count()
    stands.sort(key=lambda s: (s["issue"] is None, -(s["issue"].publication_date.toordinal() if s["issue"] else 0)))
    return render_template("news/newsstand.html", stands=stands, drafts=drafts, waiting=save_jobs.newest_waiting(),
                           job=save_jobs.job())


@news_bp.get("/news/<slug>")
def outlet_page(slug: str):
    outlet = Outlet.query.filter_by(slug=slug).first_or_404()
    issues = _published(outlet).all()
    if not issues:
        return render_template("news/outlet.html", outlet=outlet, issues=[])
    return _edition_page(outlet, issues[0], back_issues=issues[1:])


@news_bp.get("/news/<slug>/<int:issue_id>")
def edition(slug: str, issue_id: int):
    outlet = Outlet.query.filter_by(slug=slug).first_or_404()
    issue = db.get_or_404(WorldSoccerIssue, issue_id)
    if editions.outlet_of(issue) is None or editions.outlet_of(issue).id != outlet.id:
        abort(404)
    return _edition_page(outlet, issue, back_issues=[])


def _edition_page(outlet: Outlet, issue: WorldSoccerIssue, back_issues: list[WorldSoccerIssue]):
    return render_template("news/edition.html", outlet=outlet, issue=issue, articles=_articles(issue), back_issues=back_issues)


# -- the desk ---------------------------------------------------------------------------------------------
@news_bp.get("/desk")
def desk():
    """Editions taken back for correction, the latest published edition of each outlet, and "Make editions now"."""
    drafts = (WorldSoccerIssue.query.filter_by(status="draft")
              .order_by(WorldSoccerIssue.publication_date.desc(), WorldSoccerIssue.id).all())
    by_outlet = [{"issue": issue, "outlet": editions.outlet_of(issue), "articles": _articles(issue)} for issue in drafts]
    latest = []
    for outlet in outlet_service.ensure_outlets():
        issue = _published(outlet).first()
        if issue:
            latest.append({"issue": issue, "outlet": outlet, "articles": len(issue.articles)})
    window = editions.cycle_window()
    job = save_jobs.job()
    from app.services import picture_makers

    return render_template("news/desk.html", drafts=by_outlet, latest=latest, window=window, job=job,
                           busy=job["busy"], saves=save_jobs.list_saves()[:15], due=editions.next_cycle_due(),
                           picture_maker=picture_makers.MAKERS[picture_makers.maker()])


@news_bp.get("/app/newest-save")
def newest_save():
    """The archive's own newest save (for the app's menu)."""
    own = [save for save in save_jobs.list_saves() if save_jobs.belongs(save.name)]
    return {"save": own[0].name if own else None}


@news_bp.get("/desk/job")
def job_status():
    """The import or news-cycle job, for the pages' progress panel."""
    return save_jobs.job()


@news_bp.post("/desk/import")
def import_save():
    """Import a save from the game's folder, then make the news cycle, in the background."""
    try:
        save_jobs.start_import(current_app._get_current_object(), request.form.get("save", ""),
                               make_editions=request.form.get("editions") != "no")
    except (ValueError, RuntimeError) as exc:
        flash(str(exc), "error")
    else:
        flash("Importing: about 3 minutes, then a few more for the papers. The panel below shows the progress.", "info")
    back = request.form.get("next", "")
    return redirect(back if back.startswith("/") and not back.startswith("//") else url_for("news.desk"))


@news_bp.post("/desk/saves/hide")
def hide_save():
    """Hide a save from the import list (a test save, a backup)."""
    name = request.form.get("save", "")
    if name:
        save_jobs.hide_save(name)
        flash(f"{name} is hidden from the list.", "info")
    return redirect(url_for("news.desk") + "#import")


@news_bp.post("/desk/make")
def make_editions():
    try:
        since = date.fromisoformat(request.form["since"]) if request.form.get("since") else None
        until = date.fromisoformat(request.form["until"]) if request.form.get("until") else None
    except ValueError:
        abort(400)
    try:
        save_jobs.start_editions(current_app._get_current_object(), since, until)
    except RuntimeError as exc:
        flash(str(exc), "error")
    else:
        flash("Making the editions: a few minutes. This page shows the progress.", "info")
    return redirect(url_for("news.desk"))


def _draft_article(article_id: int) -> WorldSoccerArticle:
    article = db.get_or_404(WorldSoccerArticle, article_id)
    if article.issue.status != "draft":
        abort(409)
    return article


@news_bp.post("/desk/articles/<int:article_id>")
def save_article(article_id: int):
    article = _draft_article(article_id)
    headline, body = (request.form.get("headline") or "").strip(), (request.form.get("body") or "").strip()
    if not headline or not body:
        flash("An article needs a headline and a body.", "error")
    else:
        article.headline, article.body = headline[:300], body
        article.standfirst = (request.form.get("standfirst") or "").strip() or None
        editions._sync_issue_headline(article.issue)
        db.session.commit()
        flash("Article saved.", "success")
    return redirect(url_for("news.desk") + f"#article-{article.id}")


@news_bp.post("/desk/articles/<int:article_id>/rewrite")
def rewrite_article(article_id: int):
    article = _draft_article(article_id)
    try:
        editions.rewrite_article(article)
        flash("Article written again.", "success")
    except StoryError as exc:
        flash(str(exc), "error")
    return redirect(url_for("news.desk") + f"#article-{article.id}")


@news_bp.post("/desk/articles/<int:article_id>/picture")
def new_picture(article_id: int):
    """Generate a new illustration for an article with Draw Things."""
    from app.services import article_pictures, picture_makers

    article = _draft_article(article_id)
    try:
        article_pictures.illustrate(article)
        flash("New picture made.", "success")
    except picture_makers.PictureError as exc:
        flash(f"No picture: {exc}", "error")
    return redirect(url_for("news.desk") + f"#article-{article.id}")


@news_bp.post("/desk/pictures")
def draw_front_pages():
    """Draw Things pictures for the lead stories of each outlet's newest edition (the ones without one), or
    a new picture for one edition."""
    one = request.form.get("issue", type=int)
    issue_ids = [one] if one else [issue.id for outlet in outlet_service.ensure_outlets() if (issue := _published(outlet).first())]
    try:
        save_jobs.start_pictures(current_app._get_current_object(), issue_ids, again=bool(one))
    except RuntimeError as exc:
        flash(str(exc), "error")
    else:
        flash("Drawing the pictures (about half a minute each). The panel below shows the progress.", "info")
    return redirect(url_for("news.desk"))


@news_bp.get("/desk/gallery")
def gallery():
    """Every picture the archive made or keeps: the library (pools, uploads, portraits) and the stories' drawn pictures,
    to delete the bad ones."""
    from app.services import article_pictures, world_images
    from app.services.world_soccer import world_ref_context

    labels: dict[str, str] = {}

    def label(ref: str | None) -> str:
        if not ref:
            return ""
        if ref not in labels:
            try:
                labels[ref] = world_ref_context(ref).get("label") or ref
            except Exception:  # noqa: BLE001 - a reference the world no longer has
                labels[ref] = ref
        return labels[ref]

    images = [{"image": image, "label": ", ".join(label(link.world_ref) for link in image.links) or "—"} for image in world_images.library()]
    stories = article_pictures.illustrated_articles()
    return render_template("news/gallery.html", images=images, stories=stories, kinds=sorted({i["image"].kind for i in images}))


@news_bp.post("/desk/gallery/delete")
def gallery_delete():
    """Delete the chosen pictures: library pictures for good, stories' pictures back to the fallbacks."""
    from app.models.world_soccer_article import WorldSoccerArticle
    from app.services import article_pictures, world_images

    images = request.form.getlist("image", type=int)
    articles = request.form.getlist("article", type=int)
    for image_id in images:
        world_images.delete_image(image_id)
    for article_id in articles:
        article = db.session.get(WorldSoccerArticle, article_id)
        if article is not None:
            article_pictures.remove_illustration(article)
    if images or articles:
        from app.routes.world import _forget_pictures

        _forget_pictures()
    flash(f"Deleted {len(images) + len(articles)} picture{'s' if len(images) + len(articles) != 1 else ''}.", "success")
    return redirect(url_for("news.gallery"))


@news_bp.post("/desk/test-picture")
def test_picture():
    """One picture with the model and settings Draw Things has now, shown on the Desk: to judge before drawing many."""
    from app.services import article_pictures, picture_makers

    try:
        url, prompt = article_pictures.test_picture()
    except picture_makers.PictureError as exc:
        flash(f"No picture: {exc}", "error")
        return redirect(url_for("news.desk"))
    return redirect(url_for("news.desk", test_picture=url, test_prompt=prompt) + "#test-picture")


@news_bp.post("/desk/club-pictures")
def draw_club_pictures():
    """Pictures for the pools of the followed clubs and the clubs in the news: generic scenes in the club's colours."""
    from app.services import article_pictures

    count = max(1, min(request.form.get("count", article_pictures.CLUB_POOL, type=int), 24))
    try:
        save_jobs.start_club_pictures(current_app._get_current_object(), count)
    except RuntimeError as exc:
        flash(str(exc), "error")
    else:
        flash(f"Drawing up to {count} pictures for each followed club and club in the news (about half a minute each). The panel below shows the progress.", "info")
    return redirect(url_for("news.desk"))


@news_bp.post("/desk/competition-pictures")
def draw_competition_pictures():
    """Draw Things pictures for the pools of the competitions in the news: a few each, shared by their stories."""
    count = max(1, min(request.form.get("count", 6, type=int), 24))
    try:
        save_jobs.start_competition_pictures(current_app._get_current_object(), count)
    except RuntimeError as exc:
        flash(str(exc), "error")
    else:
        flash(f"Drawing up to {count} pictures for each competition in the news (about half a minute each). The panel below shows the progress.", "info")
    return redirect(url_for("news.desk"))


@news_bp.post("/desk/articles/<int:article_id>/move")
def move_article(article_id: int):
    article = _draft_article(article_id)
    editions.move_article(article, -1 if request.form.get("direction") == "up" else 1)
    return redirect(url_for("news.desk") + f"#article-{article.id}")


@news_bp.post("/desk/articles/<int:article_id>/drop")
def drop_article(article_id: int):
    article = _draft_article(article_id)
    issue = article.issue
    db.session.delete(article)
    db.session.flush()
    editions._sync_issue_headline(issue)
    db.session.commit()
    flash("Article dropped.", "success")
    return redirect(url_for("news.desk") + f"#issue-{issue.id}")


@news_bp.post("/desk/editions/<int:issue_id>/publish")
def publish_edition(issue_id: int):
    issue = db.get_or_404(WorldSoccerIssue, issue_id)
    if not issue.articles:
        flash("An empty edition cannot be published.", "error")
        return redirect(url_for("news.desk"))
    editions.publish(issue)
    outlet = editions.outlet_of(issue)
    flash(f"{issue.edition_name} published.", "success")
    return redirect(url_for("news.edition", slug=outlet.slug, issue_id=issue.id) if outlet else url_for("news.desk"))


@news_bp.post("/desk/editions/<int:issue_id>/unpublish")
def unpublish_edition(issue_id: int):
    issue = db.get_or_404(WorldSoccerIssue, issue_id)
    editions.unpublish(issue)
    flash(f"{issue.edition_name} is back on the desk: correct it, then publish it again.", "info")
    return redirect(url_for("news.desk") + f"#issue-{issue.id}")


@news_bp.post("/desk/editions/<int:issue_id>/drop")
def drop_edition(issue_id: int):
    issue = db.get_or_404(WorldSoccerIssue, issue_id)
    if issue.status != "draft":
        abort(409)
    db.session.delete(issue)
    db.session.commit()
    flash("Draft edition dropped.", "success")
    return redirect(url_for("news.desk"))


@news_bp.app_template_global()
def news_about(world_ref: str, limit: int = 8) -> list[dict]:
    """Published articles about one person, club or competition, for its archive page."""
    rows = []
    for article in editions.articles_about(world_ref, limit):
        outlet = editions.outlet_of(article.issue)
        if outlet is None:
            continue
        rows.append({"headline": article.headline, "outlet": outlet.name, "date": article.issue.publication_date,
                     "url": url_for("news.edition", slug=outlet.slug, issue_id=article.issue_id) + f"#article-{article.id}"})
    return rows


@news_bp.get("/writers/<int:writer_id>")
def writer(writer_id: int):
    """A journalist: who they are, how they write, and their published articles."""
    from app.models.outlet import Writer

    person = db.get_or_404(Writer, writer_id)
    articles = (WorldSoccerArticle.query.join(WorldSoccerIssue).filter(WorldSoccerArticle.writer_id == person.id,
                                                                       WorldSoccerIssue.status == "published")
                .order_by(WorldSoccerIssue.publication_date.desc(), WorldSoccerArticle.sort_order).limit(60).all())
    return render_template("news/writer.html", writer=person, articles=articles, outlet_of=editions.outlet_of)


@news_bp.get("/threads/<int:thread_id>")
def thread(thread_id: int):
    """The story so far: every published article of one thread, oldest first, from every outlet."""
    from app.models.world_soccer_thread import WorldSoccerStoryThread

    story = db.get_or_404(WorldSoccerStoryThread, thread_id)
    articles = (WorldSoccerArticle.query.join(WorldSoccerIssue).filter(WorldSoccerArticle.story_thread_id == story.id,
                                                                       WorldSoccerIssue.status == "published")
                .order_by(WorldSoccerIssue.publication_date, WorldSoccerArticle.id).all())
    subject = world_soccer.world_ref_context(story.key) if story.key else None
    return render_template("news/thread.html", story=story, articles=articles, subject=subject, outlet_of=editions.outlet_of)
