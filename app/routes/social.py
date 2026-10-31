"""The social feed's pages (docs/social-plan.md)."""

from __future__ import annotations

import re
import zlib
from datetime import UTC, datetime, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from markupsafe import Markup, escape
from sqlalchemy import or_, text

from app.extensions import db
from app.models import world as world_models
from app.models.social import CLUB, FAN, JOURNALIST, PLAYER, SocialAccount, SocialPost
from app.services import save_jobs, social_fans, social_feed

social_bp = Blueprint("social", __name__, url_prefix="/feed")

PAGE = 60
FIRST_REPLIES = 2  # replies shown under a post in the feed
TRENDING_DAYS = 7  # game days the trending topics count
_HASHTAG = re.compile(r"#([A-Za-z][A-Za-z0-9_]{1,40})")


@social_bp.app_template_global()
def social_enabled() -> bool:
    return social_feed.enabled()


def _club_name(uid: int | None) -> str | None:
    names = g.setdefault("social_club_names", {})
    if uid not in names:
        with db.engines[world_models.BIND].connect() as connection:
            row = connection.execute(text("select name from teams where fm_uid = :u"), {"u": uid}).first() if uid else None
        names[uid] = row[0] if row else None
    return names[uid]


@social_bp.app_template_global()
def fan_label(account: SocialAccount) -> str:
    """Who an account is, beside its handle: "Chelsea fan", "Player · Chelsea", "The Transfer Wire"."""
    if account.kind == FAN:
        return f"{_club_name(account.club_uid)} fan" if _club_name(account.club_uid) else "Fan"
    if account.kind == PLAYER:
        return f"Player · {_club_name(account.club_uid)}" if _club_name(account.club_uid) else "Player"
    if account.kind == JOURNALIST:
        from app.models import Outlet

        outlet = Outlet.query.filter_by(slug=(account.persona or {}).get("outlet")).first()
        return (outlet.name if outlet and outlet.name != account.name else "Journalist") if outlet else "Journalist"
    return ""


@social_bp.app_template_global()
def account_url(account: SocialAccount) -> str:
    return url_for("social.feed", club=account.club_uid) if account.kind == CLUB else url_for("social.feed", account=account.id)


@social_bp.app_template_global()
def article_url(key: str) -> str | None:
    """The address of an article a journalist's post links ("slug:issue:article")."""
    slug, issue, article = key.split(":")
    return url_for("news.edition", slug=slug, issue_id=int(issue)) + f"#article-{article}"


@social_bp.app_template_filter("account_hue")
def account_hue(handle: str) -> int:
    """A fan's colour, the same every time."""
    return zlib.crc32(handle.encode()) % 360


@social_bp.app_template_filter("post_text")
def post_text(post: SocialPost) -> Markup:
    """The post's text, its clubs and people linked (the first time each is named), its lines kept."""
    html = str(escape(post.text))
    for name, kind, uid in post.links or []:
        if not name or kind not in ("club", "person"):
            continue
        target = url_for("world.club", uid=uid) if kind == "club" else url_for("world.person", uid=uid)
        html = html.replace(str(escape(name)), f'<a href="{target}">{escape(name)}</a>', 1)
    html = _HASHTAG.sub(lambda m: f'<a class="hashtag" href="{url_for("social.feed", tag=m.group(1))}">#{m.group(1)}</a>', html)
    return Markup(html.replace("\n", "<br>"))


def _match_ids(posts: list[SocialPost]) -> dict[str, int]:
    """The world's fixture id of each result post's match (ids change when the world is rebuilt; the key does not)."""
    keys = {link[2] for post in posts for link in post.links or [] if link[1] == "match"}
    found = {}
    if not keys:
        return found
    with db.engines[world_models.BIND].connect() as connection:
        for key in keys:
            day, home, away = key.split(":")
            row = connection.execute(text("select id from fixtures where date = :d and home_uid = :h and away_uid = :a"),
                                     {"d": day, "h": int(home), "a": int(away)}).first()
            if row:
                found[key] = row[0]
    return found


def shown():
    """Posts already shown: a drip-fed reply waits for its time (social_fans.write_thread)."""
    return or_(SocialPost.shown_at.is_(None), SocialPost.shown_at <= datetime.now(UTC).replace(tzinfo=None))


def _talking_now(page: int) -> list[SocialPost]:
    """The posts with threads, the one answered last first: where the fans are talking now, whatever the post's date."""
    latest: dict[int, int] = {}
    for reply_id, key in db.session.query(SocialPost.id, SocialPost.event_key).filter(SocialPost.event_key.like("reply:%"), shown()):
        root = int(key.split(":")[1])
        latest[root] = max(latest.get(root, 0), reply_id)
    roots = sorted(latest, key=lambda root: -latest[root])[(page - 1) * PAGE: page * PAGE + 1]
    found = {post.id: post for post in SocialPost.query.filter(SocialPost.id.in_(roots))}
    return [found[root] for root in roots if root in found]


def _thread_of(post: SocialPost) -> list[SocialPost]:
    """Every reply in the post's thread, oldest first."""
    return SocialPost.query.filter(SocialPost.event_key.like(f"reply:{post.id}:%"), shown()).order_by(SocialPost.id).all()


@social_bp.get("/")
def feed():
    if not social_feed.enabled():
        abort(404)
    from app.services.story_leads import followed_clubs

    show = request.args.get("show", "all")
    tag = (request.args.get("tag") or "").strip().lstrip("#")
    club = request.args.get("club", type=int)
    fan_id = request.args.get("account", type=int)
    page = max(request.args.get("page", 1, type=int), 1)
    account = None
    query = SocialPost.query.join(SocialAccount).filter(shown())
    if fan_id:
        account = db.get_or_404(SocialAccount, fan_id)
        query = query.filter(SocialPost.account_id == fan_id)
    elif tag:
        query = query.filter(SocialPost.text.like(f"%#{tag}%"))
    else:
        query = query.filter(SocialPost.reply_to_id.is_(None))
        if club:
            query = query.filter(SocialAccount.kind == CLUB, SocialAccount.club_uid == club)
            account = SocialAccount.query.filter_by(kind=CLUB, club_uid=club).first()
        elif show == "mine":
            query = query.filter(SocialAccount.club_uid.in_(followed_clubs()))
    if show == "talking" and not (fan_id or tag or club):
        posts = _talking_now(page)
    else:
        posts = (query.order_by(SocialPost.posted_on.desc(), SocialPost.likes.desc(), SocialPost.id.desc())
                 .offset((page - 1) * PAGE).limit(PAGE + 1).all())
    more, posts = len(posts) > PAGE, posts[:PAGE]
    ids = [p.id for p in posts]
    reply_counts = {}
    first_replies: dict[int, list[SocialPost]] = {}
    if ids and not fan_id and not tag:
        for post in posts:  # each thread's replies are keyed by the post that started it
            thread = _thread_of(post)
            if thread:
                reply_counts[post.id] = len(thread)
                first_replies[post.id] = [r for r in thread if r.reply_to_id == post.id][:FIRST_REPLIES]
    trending = (db.session.query(SocialAccount, db.func.sum(SocialPost.likes).label("likes")).join(SocialPost)
                .filter(SocialAccount.kind == CLUB).group_by(SocialAccount.id).order_by(db.text("likes desc")).limit(8).all())
    return render_template("world/feed.html", posts=posts, show=show, club=club, account=account, page=page, more=more, tag=tag,
                           topics=trending_topics(),
                           match_ids=_match_ids(posts), trending=trending, total=SocialPost.query.count(),
                           reply_counts=reply_counts, first_replies=first_replies, tempers=social_fans.TEMPERS,
                           job=save_jobs.job(), slow=social_fans.slow_state(),
                           replies_now=SocialPost.query.filter(SocialPost.reply_to_id.isnot(None), shown()).count())


SOCIAL_TAB = 8  # posts on a club's or a person's Social tab


@social_bp.app_template_global()
def club_social(club_uid: int) -> dict:
    """A club page's Social tab: the club's own latest posts, and its fans' latest replies."""
    if not social_feed.enabled():
        return {"posts": [], "fans": []}
    posts = (SocialPost.query.join(SocialAccount).filter(SocialAccount.kind == CLUB, SocialAccount.club_uid == club_uid)
             .order_by(SocialPost.posted_on.desc(), SocialPost.id.desc()).limit(SOCIAL_TAB).all())
    fans = (SocialPost.query.join(SocialAccount).filter(SocialAccount.kind == FAN, SocialAccount.club_uid == club_uid, shown())
            .order_by(SocialPost.posted_on.desc(), SocialPost.likes.desc()).limit(SOCIAL_TAB).all())
    return {"posts": posts, "fans": fans, "match_ids": _match_ids(posts)}


@social_bp.app_template_global()
def person_social(uid: int) -> dict:
    """A person page's Social tab: the person's own posts and the posts that name the person."""
    if not social_feed.enabled():
        return {"posts": []}
    own = SocialAccount.query.filter_by(kind=PLAYER, person_uid=uid).first()
    named = SocialPost.links.cast(db.String).like(f'%"person", {int(uid)}]%')
    query = SocialPost.query.filter(named | (SocialPost.account_id == own.id) if own else named).filter(shown())
    posts = query.order_by(SocialPost.posted_on.desc(), SocialPost.likes.desc()).limit(SOCIAL_TAB).all()
    return {"posts": posts, "account": own, "match_ids": _match_ids(posts)}


def trending_topics() -> dict:
    """The hashtags and the people most posted about in the last TRENDING_DAYS game days."""
    newest = db.session.query(db.func.max(SocialPost.posted_on)).scalar()
    if newest is None:
        return {"tags": [], "people": []}
    recent = SocialPost.query.filter(SocialPost.posted_on >= newest - timedelta(days=TRENDING_DAYS), shown()).all()
    tags: dict[str, int] = {}
    people: dict[int, list] = {}
    for post in recent:
        for tag in {t.lower(): t for t in _HASHTAG.findall(post.text)}.values():
            tags[tag] = tags.get(tag, 0) + 1
        for name, kind, uid in post.links or []:
            if kind == "person" and name:
                people.setdefault(uid, [name, 0])[1] += 1
    return {"tags": sorted(tags.items(), key=lambda item: -item[1])[:8],
            "people": sorted(((uid, name, n) for uid, (name, n) in people.items()), key=lambda item: -item[2])[:5]}


@social_bp.get("/posts/<int:post_id>")
def thread(post_id: int):
    if not social_feed.enabled():
        abort(404)
    post = db.get_or_404(SocialPost, post_id)
    if post.reply_to_id:  # a reply: its whole thread
        root = post.event_key.split(":")[1] if post.event_key.startswith("reply:") else post.reply_to_id
        return redirect(url_for("social.thread", post_id=int(root)) + f"#post-{post.id}")
    replies, depth = [], {post.id: 0}
    children: dict[int, list[SocialPost]] = {}
    for reply in _thread_of(post):
        children.setdefault(reply.reply_to_id, []).append(reply)

    def walk(parent: int) -> None:
        for reply in children.get(parent, []):
            depth[reply.id] = depth[parent] + 1
            replies.append((reply, depth[reply.id] - 1))
            walk(reply.id)
    walk(post.id)
    return render_template("world/thread.html", post=post, replies=replies, match_ids=_match_ids([post]))


@social_bp.get("/status")
def status():
    """How many replies the feed has, for the page's "New replies" notice."""
    return {"replies": SocialPost.query.filter(SocialPost.reply_to_id.isnot(None), shown()).count()}


@social_bp.post("/catch-up")
def catch_up():
    """Post what happened since the feed last posted (it also runs after each import)."""
    made = social_feed.make_posts()
    flash(f"The clubs posted {made} times." if made else "Nothing new since the last posts.", "success" if made else "info")
    return redirect(url_for("social.feed"))
