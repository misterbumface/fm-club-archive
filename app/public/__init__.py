"""World Sport: the archive's published stories as a public sports site (in the manner of BBC Sport), on its own port.

A separate Flask app with only these pages, so nothing of the archive (the desk, the settings, the AI buttons, the world
pages) can be reached from outside: it reads the published editions and the pictures, and writes nothing. Each story is
shown once, however many papers wrote it (the most prominent version, then the newest). macapp/server.py serves it on
PUBLIC_PORT for the whole network.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import date
from pathlib import Path

from flask import Blueprint, Flask, abort, render_template, send_file, send_from_directory, url_for
from markupsafe import Markup, escape

from app.extensions import db
from app.public import football, history

PUBLIC_PORT = 8080
SITE_NAME = "World Sport"
# The site's sections, each from the papers' sections.
SECTIONS = OrderedDict([
    ("coming-up", ("Coming up", {"Coming up"})),
    ("europe", ("Europe", {"Europe", "Champions League"})),
    ("world", ("World", {"World", "South America", "Asia", "Africa", "North America"})),  # the other continents' cups (2 Nov 2026)
    ("results", ("Results", {"Results", "The round", "Upsets", "Derbies", "Champions", "Form", "Rising", "Fallen", "Check-in", "Finalists", "Third Place"})),
    ("international", ("International", {"International", "Tournament", "Tournament Stories", "Tournament Briefing", "Knockout Briefs"})),
    ("transfers", ("Transfers", {"Transfers", "Appointments"})),
    ("analysis", ("Analysis", {"Tactics", "Numbers", "Records", "Selection", "Dressing room", "Injuries", "War of words", "Business"})),
    ("talent", ("Talent", {"Next generation", "Scouting", "Academy"})),
    ("history", ("History", {"Legends", "Dynasties", "Farewells", "Where are they now", "Awards", "Great finals", "On this day", "The Last Word"})),
    ("features", ("Features", {"Away days", "Interviews", "Profiles", "Players"})),  # the Away Days column and the interviews (25 Oct 2026)
])
# Every paper section the editions make (editions.SECTIONS) has a site section, or it falls to the "news" bucket: the front
# page's latest list only, no row and no page. tests/test_public.py checks the map stays complete.
FRONT_ROWS = tuple(SECTIONS)
PER_ROW = 4
PROMINENCE = {"lead": 0, "major": 1, "standard": 2, "brief": 3}
SECTION_PAGE = 30
IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".webp")

public_bp = Blueprint("public", __name__)


def create_public_app(config_name: str | None = None) -> Flask:
    """The public site: the archive's database and pictures, these pages only."""
    import os

    from config import ARCHIVE_DIR, CONFIG_BY_NAME

    root = Path(__file__).resolve().parents[1]  # app/: its templates (templates/public) and static files, as the Mac app packs them
    app = Flask(__name__, instance_path=str(ARCHIVE_DIR), static_folder=str(root / "static"), template_folder=str(root / "templates"))
    app.config.from_object(CONFIG_BY_NAME[config_name or os.environ.get("FLASK_CONFIG", "default")])
    db.init_app(app)
    from app import models  # noqa: F401  (the models' tables)

    app.register_blueprint(public_bp)

    @app.before_request
    def _holding_page():
        """While the curator has the site offline, every page is the holding page (the pictures and styles still serve)."""
        from flask import request as current

        if current.path.startswith("/static/") or current.endpoint in ("static",):
            return None
        from app.services import public_site

        if public_site.offline():
            return render_template("public/offline.html"), 503
        return None

    return app


# -- reading the stories ---------------------------------------------------------------------------
def section_of(paper_section: str | None) -> str:
    return next((key for key, (_, names) in SECTIONS.items() if paper_section in names), "news")


def in_section(article, key: str, european: set[int]) -> bool:
    """An article is in its paper's section, and Europe also takes every story of a European competition (a Champions
    League preview is in Coming up and in Europe)."""
    return section_of(article.section) == key or (key == "europe" and article.id in european)


def european_articles() -> set[int]:
    """The articles linked to a UEFA competition, an edition of one, or a match in one."""
    return competition_articles("scope = 'continental' and name like 'UEFA%'")


def competition_articles(where: str) -> set[int]:
    """The articles linked to the competitions chosen by ``where`` (on competitions), an edition of one, or a match in one."""
    from app.models.world_soccer_entity_link import WorldSoccerEntityLink

    with football.connect() as c:
        uefa = {r[0] for r in c.exec_driver_sql(f"select fm_uid from competitions where {where}")}
        found = set()
        matches: dict[tuple[str, int, int], list[int]] = {}
        for link in WorldSoccerEntityLink.query.filter(WorldSoccerEntityLink.entity_type.in_(("competition", "competition_edition", "match"))).all():
            parts = (link.world_ref or "").split(":")
            if link.entity_type == "match" and len(parts) == 4:
                matches.setdefault((parts[1], int(parts[2]), int(parts[3])), []).append(link.article_id)
            elif len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) in uefa:
                found.add(link.article_id)
        if matches and uefa:
            marks = ",".join(str(u) for u in uefa)
            for day, home, away in c.exec_driver_sql(f"select date, home_uid, away_uid from fixtures where competition_uid in ({marks})"):
                found.update(matches.get((str(day)[:10], home, away), []))
    return found


# How near a story is to the site's readers, who live where the followed clubs play (3 Nov 2026: Corinthians transfers led a
# Chelsea site's front page). The world's big events come first (the World Cup, the Club World Championship, the major
# tournament in play), then the home continent, then the rest of the world; inside each, the bigger competition first, by
# the game's own competition reputation in steps of ten (a club counts as its league); see front_order. A far story keeps its section row and the latest
# list, but leads the front only when there is nothing nearer.
BIG, HOME, FAR = 0, 1, 2
REPUTATION_STEP = 10  # the leagues inside a step (Premier Division 184, La Liga 183) are equals: the story's importance decides
UNKNOWN_REPUTATION = 150  # a story that names no competition and no club in a league: a middling one


def is_football(article) -> bool:
    """The season's news (results, previews, runs, crises, injuries, the bench, the dressing room and the managers) is
    football; the rest (the numbers, the market, the past, the columns, the scouting) is flavour. A story with no lead
    kind counts as football."""
    from app.services.editions import NUMBERS_KINDS, SEASON_NEWS, TRANSFER_KINDS

    kind = (article.lead or {}).get("kind")
    return not kind or (kind in SEASON_NEWS and kind not in TRANSFER_KINDS | NUMBERS_KINDS)


def front_order(article, reach: dict) -> tuple:
    """The front page's order (4 Nov 2026: "football should lead the headlines, the rest is flavour"): newest day first, far
    stories only when nothing else is left, the big events, football before flavour, the bigger competition, then the
    story's own importance (its lead's score: the stage, the drama, the kind)."""
    where, size = reach.get(article.id, (HOME, UNKNOWN_REPUTATION))
    return (where == FAR, -article.issue.publication_date.toordinal(), where, not is_football(article), -(size // REPUTATION_STEP),
            -float((article.lead or {}).get("score") or 0))


def story_reach(articles) -> dict[int, tuple[int, int]]:
    """Each article's (reach, reputation), from its links (clubs, a person's club, nations, competitions): big when it names
    a world competition or the major tournament in play, far when everything it names is on other continents than the
    followed clubs', else home (also when it names nothing placed, or no followed club's continent is known). The
    reputation is the biggest of the competitions it names and the leagues of the clubs it names."""
    from app.models.world_soccer_entity_link import WorldSoccerEntityLink
    from app.services import world_queries as q

    ids = [a.id for a in articles]
    if not ids:
        return {}
    links: dict[int, list] = {}
    for link in WorldSoccerEntityLink.query.filter(WorldSoccerEntityLink.article_id.in_(ids)).all():
        links.setdefault(link.article_id, []).append(link)
    with football.connect() as c:
        continents = q.cached_nation_continents(c)
        team_nation = {r[0]: r[1] for r in c.exec_driver_sql(
            "select t.fm_uid, coalesce(k.nation_uid, t.nation_uid) from teams t left join clubs k on k.fm_uid = t.club_uid")}
        team_nation.update({r[0]: r[1] for r in c.exec_driver_sql("select fm_uid, nation_uid from clubs")})
        competition = {r[0]: (q._continent_by_name(r[1]), r[2] or 0) for r in c.exec_driver_sql("select fm_uid, name, reputation from competitions")}
        people = {link.entity_id for found in links.values() for link in found if link.entity_type == "person"}
        person_team = {r[0]: r[1] for r in c.exec_driver_sql(
            f"select fm_uid, coalesce(team_uid, staff_team_uid) from people where fm_uid in ({','.join(map(str, people)) or 0})")}
        teams = {link.entity_id for found in links.values() for link in found if link.entity_type == "club"} | set(person_team.values())
        marks = ",".join(str(t) for t in teams if t) or "0"
        league = {r[0]: r[2] or 0 for r in c.exec_driver_sql(  # each club's latest league (sqlite takes the row of the max)
            f"select team, competition_uid, reputation, max(date) from (select home_uid team, competition_uid, date from fixtures "
            f"where home_uid in ({marks}) union all select away_uid, competition_uid, date from fixtures where away_uid in ({marks})) "
            f"join competitions c on c.fm_uid = competition_uid where c.scope = 'league' group by team")}
    home = {continents.get(team_nation.get(team)) for team in football.followed()} - {None}
    major = (_major_tournament() or {}).get("uid")
    reach = {}
    for article_id in ids:
        places, sizes, big = [], [], False
        for link in links.get(article_id, []):
            uid = link.entity_id
            team = uid if link.entity_type == "club" else person_team.get(uid) if link.entity_type == "person" else None
            if team:
                places.append(continents.get(team_nation.get(team)))
                sizes.append(league.get(team))
            elif link.entity_type in ("competition", "competition_edition") and str(link.world_ref or "").count(":"):
                competition_uid = int(str(link.world_ref).split(":")[1])
                place, size = competition.get(competition_uid, (None, None))
                big |= place == "World" or competition_uid == major
                places.append(place)
                sizes.append(size)
            elif link.entity_type == "nation":
                places.append(continents.get(uid))
        known = {place for place in places if place}
        sizes = [size for size in sizes if size is not None]
        reach[article_id] = (BIG if big else FAR if home and known and not known & (home | {"World"}) else HOME,
                             max(sizes) if sizes else UNKNOWN_REPUTATION)
    return reach


def section_name(key: str) -> str:
    return SECTIONS[key][0] if key in SECTIONS else "News"


def stories(limit: int | None = None, section: str | None = None, among: set[int] | None = None) -> list:
    """The published articles, newest first, one per story (the most prominent version, then the newest); with ``among``, only
    those of these ids. Chosen on plain rows, and only the articles returned are loaded: nearly every page of the site asks."""
    from app.models.world_soccer_article import WorldSoccerArticle as Article
    from app.models.world_soccer_issue import WorldSoccerIssue as Issue

    if among is not None and not among:
        return []
    rows = (db.session.query(Article.id, Article.lead_key, Article.section, Article.prominence, Issue.publication_date).join(Issue)
            .filter(Issue.status == "published").order_by(Issue.publication_date.desc(), Article.sort_order).all())
    best: dict = {}
    for article in rows:
        key = article.lead_key or f"article:{article.id}"
        kept = best.get(key)
        if kept is None or (article.publication_date == kept.publication_date
                            and PROMINENCE.get(article.prominence, 9) < PROMINENCE.get(kept.prominence, 9)):
            best[key] = article
    found = sorted(best.values(), key=lambda a: (a.publication_date, -PROMINENCE.get(a.prominence, 9)), reverse=True)
    if section:
        european = european_articles() if section == "europe" else set()
        found = [a for a in found if in_section(a, section, european)]
    if among is not None:
        found = [a for a in found if a.id in among]
    ids = [a.id for a in (found[:limit] if limit else found)]
    loaded = {a.id: a for a in Article.query.filter(Article.id.in_(ids)).all()}
    return [loaded[uid] for uid in ids]


def picture(article) -> str | None:
    """The article's picture, chosen as the desk chooses it (news._picture), so the site shows what the curator saw (31 Oct
    2026): its own picture; the archive's photographs, match-day and stadium pictures of its subjects; a face or a trophy;
    the club's stadium from the game's stadium pack; then a picture from its club's or competition's pool, a crest or a logo."""
    from app.services import world_images

    if article.image_url and article.image_url.startswith("/static/"):
        return article.image_url
    logo = re.fullmatch(r"/logos/(club|competition|nation)/\w+/(\d+)\.png", article.image_url or "")
    if logo:
        return url_for("public.logo", entity_type=logo.group(1), fm_uid=int(logo.group(2)))
    refs = [link.world_ref for link in article.entity_links if link.world_ref]
    for ref in refs:
        found = [image for image in world_images.pictures(ref, ("photograph", "matchday", "stadium"))
                 if image.is_active and (image.url or "").startswith("/static/")]
        if found:
            return found[article.id % len(found)].url  # they rotate by the story's number, as on the desk
    for ref in refs:
        kind, _, rest = ref.partition(":")
        uid = int(rest.split(":")[0]) if kind in ("person", "edition", "competition") and rest.split(":")[0].isdigit() else None
        if kind == "person" and uid and _face_path(uid):
            return url_for("public.face", uid=uid)
        if kind in ("edition", "competition") and uid:
            trophy = history.trophy(uid)
            if trophy:
                return trophy
    for ref in refs:
        if ref.startswith("club:") and ref.split(":")[1].isdigit():
            stadium = _stadium_of(int(ref.split(":")[1]))
            if stadium:
                return url_for("public.stadium", stadium_uid=stadium)
    from app.services.article_pictures import pool_picture

    pooled = pool_picture(article)
    if pooled:
        return pooled
    from app.services.logo_assets import resolve_logo_path

    for link in article.entity_links:
        if link.entity_type == "club" and resolve_logo_path("club", link.entity_id, "normal"):
            return url_for("public.logo", entity_type="club", fm_uid=link.entity_id)
        if link.entity_type == "competition" and resolve_logo_path("competition", link.entity_id, "normal"):
            return url_for("public.logo", entity_type="competition", fm_uid=link.entity_id)
    return None


def _stadium_of(team_uid: int) -> int | None:
    """The home stadium of a club or team, when the game's stadium pack has its picture."""
    from app.routes.world import stadium_path

    with football.connect() as c:
        row = c.execute(db.text("select stadium_uid from teams where fm_uid = :u"), {"u": team_uid}).first()
    return row[0] if row and row[0] and stadium_path(row[0]) else None


def _face_path(uid: int) -> Path | None:
    from app.routes.world import picture_path

    try:
        return picture_path(uid)
    except (OSError, ValueError):
        return None


def face_url(uid: int | None) -> str | None:
    return url_for("public.face", uid=uid) if uid and _face_path(uid) else None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:80] or "story"


def paragraphs(body: str) -> Markup:
    parts = [p.strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]
    return Markup("".join(f"<p>{escape(p).replace(chr(10), Markup('<br>'))}</p>" for p in parts))


@public_bp.app_template_global()
def story_url(article) -> str:
    return url_for("public.story", article_id=article.id, name=slug(article.headline))


@public_bp.app_template_global()
def club_url(uid: int | None, team: dict | None = None) -> str | None:
    if not uid:
        return None
    return url_for("public.club", uid=uid, name=slug((team or {}).get("name") or "club"))


def stories_about(kind: str, uid: int, limit: int = 12) -> list:
    """The published stories linked to a club (or another world entity), newest first, one per story."""
    from app.models.world_soccer_entity_link import WorldSoccerEntityLink

    linked = {row.article_id for row in WorldSoccerEntityLink.query.filter_by(entity_type=kind, entity_id=uid).all()}
    return stories(limit, among=linked)


def _major_tournament() -> dict | None:
    """The tournament in play, for the nav; read once per game date."""
    global _MAJOR_CACHE
    with football.connect() as c:
        today = football.today_of(c)
        if _MAJOR_CACHE is None or _MAJOR_CACHE[0] != today:
            _MAJOR_CACHE = (today, football.major_tournament(c))
    return _MAJOR_CACHE[1]


_MAJOR_CACHE: tuple | None = None


@public_bp.app_context_processor
def site():
    return {"site_name": SITE_NAME, "sections": [(key, name) for key, (name, _) in SECTIONS.items()], "picture": picture,
            "followed_teams": football.followed(), "picture_of": face_url, "slug": slug, "major_tournament": _major_tournament(),
            "section_of": section_of, "section_name": section_name, "paragraphs": paragraphs}


# -- pages -------------------------------------------------------------------------------------------
@public_bp.get("/")
def front():
    everything = stories()
    reach = story_reach(everything)
    top = sorted(everything, key=lambda a: front_order(a, reach))[:5]
    shown = {a.id for a in top}
    rows = []
    european = european_articles()
    for key in FRONT_ROWS:
        items = [a for a in everything if in_section(a, key, european) and a.id not in shown][:PER_ROW]
        shown |= {a.id for a in items}
        if items:
            rows.append((key, section_name(key), items))
    latest = [a for a in everything if a.id not in shown][:8]
    with football.connect() as c:
        leagues = football.main_leagues(c)
        box = football.league_box(c, leagues[0]) if leagues else None
        results = football.top_results(c)
        hub = football.tournament_hub(c)
    return render_template("public/front.html", top=top, rows=rows, latest=latest, box=box, results=results, hub=hub, active=None)


@public_bp.get("/uefa")
def uefa():
    with football.connect() as c:
        boards = [t for r in football.in_play(c, "continental", "UEFA%", 3) if (t := football.tournament(c, r["fm_uid"]))]
    linked = european_articles()
    items = stories(8, among=linked)
    return render_template("public/tournaments.html", title="UEFA", boards=boards, items=items, active="europe")


@public_bp.get("/international")
def international():
    with football.connect() as c:
        boards = [t for r in sorted(football.in_play(c, "national_teams", None, 14), key=football.international_order)
                  if (t := football.tournament(c, r["fm_uid"]))]
    linked = competition_articles("scope = 'national_teams'")
    items = stories(8, among=linked)
    return render_template("public/tournaments.html", title="International", boards=boards, items=items, active="international")


@public_bp.get("/tournaments/<int:uid>/<name>")
@public_bp.get("/tournaments/<int:uid>")
def tournament(uid: int, name: str | None = None):
    with football.connect() as c:
        page = football.tournament(c, uid)
        scope = (c.exec_driver_sql("select scope from competitions where fm_uid = ?", (uid,)).fetchone() or [None])[0]
        major = football.major_tournament(c)
        if page is not None and major and major["uid"] == uid:
            page["major"] = major
            page["ours"] = football.our_players_at(c, uid, str(major["first"]), str(major["last"]))
            page["today_games"] = football.tournament_hub(c)["today_games"]
    if page is None:
        abort(404)
    linked = competition_articles(f"fm_uid = {int(uid)}")
    items = stories(8, among=linked)
    return render_template("public/tournament.html", page=page, items=items,
                           active="major" if page.get("major") else "international" if scope == "national_teams" else "europe")


@public_bp.app_template_global()
def tournament_url(uid: int | None, name: str | None = None) -> str | None:
    return url_for("public.tournament", uid=uid, name=slug(name or "competition")) if uid else None


@public_bp.get("/scores")
def scores():
    with football.connect() as c:
        boxes = [box for uid in football.main_leagues(c) if (box := football.league_box(c, uid)) and (box["recent"] or box["upcoming"])]
        cups = [t for r in football.in_play(c, "continental", "UEFA%", 3) + football.in_play(c, "national_teams", None, 6)
                if (t := football.tournament(c, r["fm_uid"])) and (t["recent"] or t["upcoming"])]
    boxes = boxes[:1] + [{**t, "is_tournament": True} for t in cups[:3]] + boxes[1:] + [{**t, "is_tournament": True} for t in cups[3:]]
    return render_template("public/scores.html", boxes=boxes, active="scores")


@public_bp.get("/scores/<day>")
def scores_day(day: str):
    """One day's results, ESPN-style: the days around it to step through, the competitions the paper follows. Games link to
    their match page only for the followed clubs (a match page for every game of every day would fill the export)."""
    from app.services.story_leads import followed_clubs

    try:
        when = date.fromisoformat(day)
    except ValueError:
        abort(404)
    with football.connect() as c:
        order = football.score_competitions(c)
        days = football.match_days(c, order)
        if when not in days:
            abort(404)
        results = football.day_results(c, when, order)
    followed = set(followed_clubs())
    for comp in results:
        for g in comp["games"]:
            if not ({g.get("home_uid"), g.get("away_uid")} & followed):
                g.pop("id", None)
    at = days.index(when)
    strip = days[max(0, at - 3):at + 4]
    return render_template("public/scores_day.html", day=when, results=results, strip=strip,
                           before=days[at - 1] if at > 0 else None, after=days[at + 1] if at + 1 < len(days) else None, active="scores")


@public_bp.get("/tables")
def tables():
    with football.connect() as c:
        boxes = [box for uid in football.main_leagues(c) if (box := football.league_box(c, uid)) and box["table"]]
        phases = [t for r in football.in_play(c, "continental", "UEFA%", 3) if (t := football.tournament(c, r["fm_uid"])) and t["groups"]]
    return render_template("public/tables.html", boxes=boxes, phases=phases, active="tables")


@public_bp.get("/tables/<int:uid>")
def table(uid: int):
    with football.connect() as c:
        box = football.league_box(c, uid)
    if box is None or not box["table"]:
        abort(404)
    return render_template("public/table.html", box=box, active="tables")


@public_bp.app_template_global()
def match_url(game: dict) -> str | None:
    if not game.get("id"):
        return None
    return url_for("public.match", fixture_id=game["id"], name=slug(f"{game['home'].get('name', '')} {game['away'].get('name', '')}"))


@public_bp.get("/matches/<int:fixture_id>/<name>")
@public_bp.get("/matches/<int:fixture_id>")
def match(fixture_id: int, name: str | None = None):
    from app.models.world_soccer_entity_link import WorldSoccerEntityLink

    with football.connect() as c:
        page = football.match(c, fixture_id)
        coming = football.preview(c, fixture_id) if page is None else None
    if page is None and coming is None:
        abort(404)
    ref = (page or coming)["ref"]
    linked = {row.article_id for row in WorldSoccerEntityLink.query.filter_by(world_ref=ref).all()}
    items = stories(among=linked)
    if coming:
        return render_template("public/preview.html", page=coming, items=items, active="scores")
    return render_template("public/match.html", page=page, items=items, active="scores")


@public_bp.app_template_global()
def player_url(uid: int | None, name: str | None = None) -> str | None:
    return url_for("public.player", uid=uid, name=slug(name or "player")) if uid else None


@public_bp.get("/players/<int:uid>/<name>")
@public_bp.get("/players/<int:uid>")
def player(uid: int, name: str | None = None):
    from app.models.person_bio import PersonBio

    with football.connect() as c:
        page = football.player(c, uid)
    if page is None:
        abort(404)
    bio = db.session.get(PersonBio, uid)
    items = stories(12, among=_linked({"person", "player"}, uid))
    return render_template("public/player.html", page=page, bio=bio, items=items, face=face_url(uid), active=None)


def _linked(kinds: set[str], uid: int) -> set[int]:
    from app.models.world_soccer_entity_link import WorldSoccerEntityLink

    return {row.article_id for row in WorldSoccerEntityLink.query.filter(WorldSoccerEntityLink.entity_type.in_(kinds),
                                                                         WorldSoccerEntityLink.entity_id == uid).all()}


@public_bp.get("/clubs/<int:uid>/<name>")
@public_bp.get("/clubs/<int:uid>")
def club(uid: int, name: str | None = None):
    with football.connect() as c:
        page = football.club(c, uid)
        past = history.club(c, uid) if page else None
    if page is None:
        abort(404)
    return render_template("public/club.html", page=page, past=past, items=stories_about("club", uid), active=None)


# -- history ----------------------------------------------------------------------------------------------
@public_bp.app_template_global()
def competition_url(uid: int | None, name: str | None = None, year: int | None = None) -> str | None:
    if not uid:
        return None
    if year:
        return url_for("public.edition", uid=uid, name=slug(name or "competition"), year=year)
    return url_for("public.competition", uid=uid, name=slug(name or "competition"))


@public_bp.app_template_global()
def trophy_url(uid: int | None) -> str | None:
    return history.trophy(uid)


@public_bp.get("/history")
def history_page():
    with football.connect() as c:
        legends = history.legends(c)[:8]
        records = history.records(c)
    return render_template("public/history.html", legends=legends, records=records, items=stories(8, "history"), active="history")


@public_bp.get("/about")
def about():
    """What the site is and how it is made, with the world's size counted from the archive (31 Oct 2026)."""
    from sqlalchemy import text

    from app.models.outlet import Outlet

    counts = {}
    with football.connect() as c:
        for key, sql in (("matches", "SELECT count(*) FROM fixtures WHERE home_goals IS NOT NULL"),
                         ("goals", "SELECT count(*) FROM goals"), ("people", "SELECT count(*) FROM people"),
                         ("clubs", "SELECT count(*) FROM clubs"), ("transfers", "SELECT count(*) FROM transfers"),
                         ("saves", "SELECT count(*) FROM saves"), ("newest", "SELECT max(game_date) FROM saves")):
            try:
                counts[key] = c.execute(text(sql)).scalar()
            except Exception:  # noqa: BLE001 - a world not read yet: the page goes without its numbers
                counts[key] = None
    if counts["newest"]:
        counts["newest"] = date.fromisoformat(str(counts["newest"])[:10]).strftime("%-d %B %Y")
    counts["stories"] = len(stories())
    counts["papers"] = Outlet.query.filter_by(active=True).count()
    return render_template("public/about.html", counts=counts, active="about")


@public_bp.get("/competitions")
def competitions():
    with football.connect() as c:
        groups = history.competitions(c)
    return render_template("public/competitions.html", groups=groups, active="history")


@public_bp.get("/competitions/<int:uid>/<name>")
@public_bp.get("/competitions/<int:uid>")
def competition(uid: int, name: str | None = None):
    with football.connect() as c:
        page = history.competition(c, uid)
        box = football.league_box(c, uid) if page else None
    if page is None:
        abort(404)
    return render_template("public/competition.html", page=page, box=box if box and box["table"] else None, active="history")


@public_bp.get("/competitions/<int:uid>/<name>/<int:year>")
def edition(uid: int, name: str, year: int):
    with football.connect() as c:
        page = history.edition(c, uid, year)
    if page is None:
        abort(404)
    return render_template("public/edition.html", page=page, active="history")


@public_bp.get("/records")
def records():
    with football.connect() as c:
        page = history.records(c)
    return render_template("public/records.html", page=page, active="history")


@public_bp.get("/legends")
def legends():
    with football.connect() as c:
        rows = history.legends(c)
    return render_template("public/legends.html", rows=rows, active="history")


@public_bp.get("/awards")
def awards():
    with football.connect() as c:
        rows = history.awards(c)
    return render_template("public/awards.html", rows=rows, active="history")


@public_bp.get("/awards/<int:uid>/<name>")
@public_bp.get("/awards/<int:uid>")
def award(uid: int, name: str | None = None):
    with football.connect() as c:
        page = history.award(c, uid)
        clubs = q_teams(c, {row[1]["club_uid"] for _, row in page["years"] if 1 in row}) if page else {}
    if page is None:
        abort(404)
    return render_template("public/award.html", page=page, clubs=clubs, active="history")


def q_teams(c, uids) -> dict:
    from app.services import world_queries as q

    return q.teams(c, uids)


@public_bp.get("/section/<key>")
def section(key: str):
    if key not in SECTIONS and key != "news":
        abort(404)
    return render_template("public/section.html", items=stories(SECTION_PAGE, key), title=section_name(key), active=key)


@public_bp.get("/story/<int:article_id>/<name>")
@public_bp.get("/story/<int:article_id>")
def story(article_id: int, name: str | None = None):
    from app.models.world_soccer_article import WorldSoccerArticle

    article = db.session.get(WorldSoccerArticle, article_id)
    if article is None or article.issue.status != "published":
        abort(404)
    key = section_of(article.section)
    more = [a for a in stories(SECTION_PAGE, key) if a.id != article.id][:6]
    return render_template("public/story.html", a=article, more=more, active=key, related=related_stories(article))


def related_stories(article) -> list[dict]:
    """The other published stories about the same match: the report of a game this paper previewed, and the preview a report
    settles ("What we said before the game")."""
    from app.models.world_soccer_entity_link import WorldSoccerEntityLink

    ref = next((link.world_ref for link in article.entity_links if link.world_ref and link.world_ref.startswith("match:")), None)
    if ref is None:
        return []
    linked = {row.article_id for row in WorldSoccerEntityLink.query.filter_by(world_ref=ref).all()} - {article.id}
    found = []
    for other in stories(among=linked):
        before = other.section == "Coming up"
        found.append({"article": other, "label": "What we said before the game" if before else "The match report"})
    return found


@public_bp.get("/faces/<int:uid>")
def face(uid: int):
    path = _face_path(uid)
    if path is None:
        abort(404)
    return send_file(path, conditional=True, max_age=3600)


@public_bp.get("/stadiums/<int:stadium_uid>.jpg")
def stadium(stadium_uid: int):
    from app.routes.world import stadium_path

    path = stadium_path(stadium_uid)
    if path is None:
        abort(404)
    return send_file(path, conditional=True, max_age=86400)


@public_bp.get("/trophies/<int:uid>.png")
def trophy(uid: int):
    from app.services.logo_assets import trophy_path

    path = trophy_path(uid)
    if path is None:
        abort(404)
    return send_file(path, conditional=True, max_age=86400)


@public_bp.get("/logos/<entity_type>/<int:fm_uid>.png")
def logo(entity_type: str, fm_uid: int):
    from app.services.logo_assets import resolve_logo_path

    if entity_type not in ("club", "competition", "nation"):
        abort(404)
    path = resolve_logo_path(entity_type, fm_uid, "normal")
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/png", conditional=True, max_age=86400)


@public_bp.get("/static/generated/<path:filename>")
def generated(filename: str):
    from flask import current_app

    if not filename.lower().endswith(IMAGE_TYPES):
        abort(404)
    return send_from_directory(current_app.config["GENERATED_DIR"], filename, max_age=86400)
