"""The new pages, built on the world database (docs/pages-plan.md).

They live under ``/world`` while the rewrite is in progress and move to ``/`` at the
cut-over (docs/rewrite-plan.md, step 7).
"""

from __future__ import annotations

import re
import threading
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, url_for

from app.extensions import db
from app.models import world as world_models
from app.models.match_text import MatchText
from app.models.person_bio import PersonBio
from app.models.world_story import (
    ClubSeasonChapter,
    ClubSeasonStory,
    ClubText,
    EditionChapter,
    EditionStory,
    StoryVersion,
)
from app.services import bio_writer, expected, moods, story_writer, tactics, world_images, world_portraits
from app.services import world_queries as q
from app.services import writing_voices as writing_voices_module
from app.services.ai_settings import deepseek_model_label
from app.services.logo_assets import resolve_logo_path, trophy_path
from app.services.player_narrative_formatter import format_player_narrative

world_bp = Blueprint("world", __name__)

_PICTURES_TTL = 300  # seconds; a new portrait clears the cache at once
_pictures: dict = {"at": 0.0, "faces": {}, "old": {}, "stadiums": {}}
_league_jobs: dict[int, dict] = {}  # league -> {"text": progress, "stop": asked to stop} of a running "missing pictures" batch
LEAGUE_FAILURES = 3  # failed pictures in a row stop a league's batch: the maker has most likely gone away
_team_jobs: dict[tuple[int, int], str] = {}  # (team, season) -> progress text of a running "generate missing pictures"
_bio_jobs: dict[tuple[int, int], str] = {}  # (team, season) -> progress text of a running "write missing bios"
_photo_jobs: dict[int, str] = {}  # person -> progress text of a running "make an action photo"
_age_jobs: dict[int, str] = {}  # person -> progress text of a running "age up / change shirt"


def _connection():
    return db.engines[world_models.BIND].connect()


def _picture_sources() -> tuple[dict, dict]:
    if time.monotonic() - _pictures["at"] > _PICTURES_TTL:
        _pictures.update(at=time.monotonic(), faces=world_portraits.face_pack_index(), old=world_portraits.old_portraits(),
                         stadiums=world_portraits.stadium_pack_index())
    return _pictures["faces"], _pictures["old"]


def stadium_path(stadium_uid: int | None) -> Path | None:
    if not stadium_uid:
        return None
    _picture_sources()
    return _pictures["stadiums"].get(stadium_uid)


@world_bp.app_template_global()
def stadium_url(team_uid: int | None) -> str | None:
    """The home stadium's picture of a club or team, from the game's stadium pack."""
    if not team_uid:
        return None
    with _connection() as c:
        row = q._one(c, "select stadium_uid from teams where fm_uid = :u", u=team_uid)
    path = stadium_path(row and row["stadium_uid"])
    return url_for("world.stadium", stadium_uid=row["stadium_uid"], v=int(path.stat().st_mtime)) if path else None


def _forget_pictures() -> None:
    _pictures["at"] = 0.0


def picture_path(uid: int | None) -> Path | None:
    if not uid:
        return None
    faces, old = _picture_sources()
    return world_portraits.picture_of(uid, faces, old)


@world_bp.app_template_global()
def face_url(uid: int | None) -> str | None:
    """The picture's address, with its change time, so a new picture is not hidden by the browser's cache."""
    path = picture_path(uid)
    return url_for("world.face", uid=uid, v=int(path.stat().st_mtime)) if path else None


@world_bp.app_template_global()
def crest_url(team: dict | None) -> str | None:
    if not team:
        return None
    if team.get("nation_uid"):
        return url_for("assets.logo", entity_type="nation", size="small", fm_uid=team["nation_uid"]) if resolve_logo_path("nation", team["nation_uid"], "small") else None
    uid = team.get("club_uid")
    return url_for("assets.logo", entity_type="club", size="normal", fm_uid=uid) if uid and resolve_logo_path("club", uid, "normal") else None


@world_bp.app_template_global()
def trophy_url(competition_uid: int | None) -> str | None:
    """The curator's uploaded trophy first, then the shipped trophy, then the competition logo."""
    if not competition_uid:
        return None
    uploaded = world_images.uploaded_trophy_url(competition_uid)
    if uploaded:
        return uploaded
    if trophy_path(competition_uid):
        return url_for("assets.trophy", fm_uid=competition_uid)
    if resolve_logo_path("competition", competition_uid, "normal"):
        return url_for("assets.logo", entity_type="competition", size="normal", fm_uid=competition_uid)
    return None


@world_bp.app_template_global()
def award_image_url(name: str | None) -> str | None:
    if not name:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    path = Path(current_app.static_folder) / "img" / "individual-awards" / f"{slug}.png"
    return url_for("static", filename=f"img/individual-awards/{slug}.png") if path.is_file() else None


@world_bp.app_template_global()
def world_pictures(ref: str, kinds: tuple[str, ...] | None = None) -> list:
    return world_images.pictures(ref, kinds)


@world_bp.app_template_global()
def writing_voices() -> list[tuple[str, str]]:
    """(key, label) of each voice a text can be written in, for the "Write" buttons."""
    return [(key, label) for key, (label, _) in writing_voices_module.VOICES.items()]


@world_bp.app_template_global()
def default_writing_voice() -> str:
    return writing_voices_module.default_voice()


@world_bp.context_processor
def _shared() -> dict:
    return {"kinds": q.TRANSFER_KINDS, "kind_names": q.KIND_NAMES}


@world_bp.app_template_filter("season")
def season_filter(season: int) -> str:
    return q.season_label(season)


@world_bp.app_template_global()
def crumbs(kind: str, uid: int, league_uid: int | None = None) -> list[tuple[str, str | None]]:
    """The page's trail back up: continent › country › league › club (world_queries.trail)."""
    with _connection() as c:
        return q.trail(c, kind, uid, league_uid)


@world_bp.app_template_filter("decided")
def decided_filter(day) -> str:
    """An edition's date; the save's placeholder for old history (1 January 1900) shows as nothing."""
    return "" if not day or str(day) <= "1900-12-31" else str(day)


@world_bp.app_template_filter("month_year")
def month_year_filter(day) -> str:
    """"Aug 2062" from a date or its text."""
    if not day:
        return ""
    return (date.fromisoformat(day) if isinstance(day, str) else day).strftime("%b %Y")


@world_bp.app_template_filter("day_month")
def day_month_filter(day, weekday: bool = False) -> str:
    """"10 August" (or "Fri 24 July") from a date or its text."""
    if not day:
        return ""
    day = date.fromisoformat(str(day)[:10]) if not isinstance(day, date) else day
    return f"{day:%a} {day.day} {day:%B}" if weekday else f"{day.day} {day:%B}"


@world_bp.app_template_filter("time_out")
def time_out_filter(days: int | None) -> str:
    """A time out as the game writes it: "12 days", "3 weeks", "3 months"."""
    if days is None:
        return ""
    if days < 14:
        return f"{days} day{'s' if days != 1 else ''}"
    if days < 90:  # the game: "7 weeks - 10 weeks" for 61 days, "2 - 3 months" for 90
        return f"{round(days / 7)} weeks"
    return f"{round(days / 30)} months"


@world_bp.app_template_filter("injury_name")
def injury_name_filter(name: str | None) -> str:
    """"torn hamstring" -> "Torn Hamstring", as the game's tables write it."""
    return " ".join(word[:1].upper() + word[1:] for word in (name or "").split())


@world_bp.app_template_filter("cash")
def cash_filter(pounds: int | None) -> str:
    """A sum of money in pounds, as the game would round it: £1.16bn, -£186.1m, £359k, £0."""
    if pounds is None:
        return "—"
    sign, size = ("-" if pounds < 0 else ""), abs(pounds)
    if size >= 1e9:
        return f"{sign}£{size / 1e9:.2f}bn"
    if size >= 1e6:
        return f"{sign}£{size / 1e6:.1f}m"
    return f"{sign}£{size / 1e3:.0f}k" if size >= 1e3 else f"{sign}£{size}"


@world_bp.app_template_filter("money")
def money_filter(pounds: int | None) -> str:
    if not pounds:
        return ""
    return f"£{pounds / 1e6:.1f}m" if pounds >= 1e6 else f"£{pounds / 1e3:.0f}k"


@world_bp.get("/faces/<int:uid>.png")
def face(uid: int):
    path = picture_path(uid)
    if path is None:
        abort(404)
    return send_file(path, conditional=True, max_age=3600)


@world_bp.get("/stadiums/<int:stadium_uid>")
def stadium(stadium_uid: int):
    path = stadium_path(stadium_uid)
    if path is None:
        abort(404)
    return send_file(path, conditional=True, max_age=86400)


@world_bp.get("/people/<int:uid>")
def person(uid: int):
    with _connection() as c:
        p = q.person(c, uid)
        if p is None:
            abort(404)
        today = q.game_date(c)
        honours = q.player_honours(c, uid)
        career = q.player_career(c, uid, honours)
        context = dict(
            p=p, age=q.age_on(p["birth_date"], today), today=today, honours=honours,
            cabinet=q.group_honours([h for h in honours if h["place"] == 1]),
            runner_up=q.group_honours([h for h in honours if h["place"] == 2]),
            career=career, awards=q.player_awards(c, uid), elevens=q.player_best_elevens(c, uid),
            transfers=q.player_transfers(c, uid), finals=q.goals_in_finals(c, uid, honours),
            goals=q.player_goals_by_season(c, uid), attributes=(attributes := q.latest_attributes(c, uid)),
            character=q.character_words(attributes.get("traits") if attributes else None),
            club_feelings=q.person_club_relations(c, uid), manager_spells=q.person_manager_spells(c, uid),
            manager_profile=q.manager_profile(c, uid),
            injuries=q.person_injuries(c, uid), mood=moods.person_mood(c, uid), market=q.transfer_market(c, uid), games=q.player_games(c, uid), contracts=q.person_contracts(c, uid),
        )
        context["positions"] = q.player_positions(context["attributes"])
        team_ids = {r["team_uid"] for r in career.rows} | {h["team_uid"] for h in honours}
        team_ids |= {t["from_uid"] for t in context["transfers"]} | {t["to_uid"] for t in context["transfers"]}
        team_ids |= {f["home_uid"] for f in context["finals"]} | {f["away_uid"] for f in context["finals"]}
        team_ids |= {(row["club"] or {}).get("team_uid") for row in context["elevens"]}
        context["teams"] = q.teams(c, team_ids)
        context["competitions"] = q.competitions(c, {h["competition_uid"] for h in honours}
                                                 | {r["league_uid"] for r in career.rows} | {g["competition_uid"] for g in context["goals"]})
    bio = db.session.get(PersonBio, uid)
    return render_template("world/person.html", bio=bio, bio_sections=format_player_narrative(bio.profile) if bio else [],
                           photo_job=_photo_jobs.get(uid), age_job=_age_jobs.get(uid), **context)


@world_bp.post("/people/<int:uid>/bio")
def write_bio(uid: int):
    try:
        bio_writer.write_bio(uid, voice=request.form.get("voice"))
    except bio_writer.BioError as exc:
        flash(f"No bio: {exc}", "error")
    else:
        flash("New bio written.", "success")
    return redirect(url_for("world.person", uid=uid, _anchor="story"))


@world_bp.post("/people/<int:uid>/picture")
def generate_picture(uid: int):
    world_db = Path(current_app.config["SQLALCHEMY_BINDS"][world_models.BIND].removeprefix("sqlite:///"))
    try:
        world_portraits.generate_one(world_db, uid, replace=True)
    except world_portraits.PortraitError as exc:
        flash(f"No picture: {exc}", "error")
    else:
        flash("New picture made. In the game, clear the cache and reload the skin to see it.", "success")
    _forget_pictures()
    return redirect(url_for("world.person", uid=uid))


@world_bp.post("/people/<int:uid>/own-picture")
def own_picture(uid: int):
    """Replace his picture with one the user made: kept in the archive, cut out and put in the game's face pack."""
    upload = request.files.get("picture")
    data = upload.read() if upload else b""
    if not data:
        flash("Choose a picture first.", "error")
        return redirect(url_for("world.person", uid=uid))
    try:
        world_portraits.use_own_picture(uid, data)
    except world_portraits.PortraitError as exc:
        flash(f"No picture: {exc}", "error")
    else:
        flash("Your picture is now his picture. In the game, clear the cache and reload the skin to see it.", "success")
    _forget_pictures()
    return redirect(url_for("world.person", uid=uid))


@world_bp.post("/people/<int:uid>/age-up")
def age_up_picture(uid: int):
    """His picture brought up to date (his age now, his club's shirt now), made in the background from the current one."""
    if _age_jobs.get(uid, "Done").split(":")[0] not in ("Done", "Done, with an error"):
        flash("His picture is already being brought up to date.", "info")
        return redirect(url_for("world.person", uid=uid))
    world_db = Path(current_app.config["SQLALCHEMY_BINDS"][world_models.BIND].removeprefix("sqlite:///"))
    app = current_app._get_current_object()

    def run() -> None:
        with app.app_context():
            try:
                _, what = world_portraits.age_up(world_db, uid)
                _age_jobs[uid] = f"Done: a new picture, {what}. In the game, clear the cache and reload the skin to see it."
            except world_portraits.PortraitError as exc:
                _age_jobs[uid] = f"Done, with an error: {exc}"
            except Exception as exc:  # noqa: BLE001 - the page must never wait for ever
                app.logger.exception("age up failed")
                _age_jobs[uid] = f"Done, with an error: {type(exc).__name__}: {exc}"
            finally:
                _forget_pictures()
                db.session.remove()

    _age_jobs[uid] = "Bringing his picture up to date…"
    threading.Thread(target=run, name=f"age-up-{uid}", daemon=True).start()
    return redirect(url_for("world.person", uid=uid))


@world_bp.post("/people/<int:uid>/action-photo")
def make_action_photo(uid: int):
    """A new action photo of the player from his current picture, made in the background (several minutes)."""
    if _photo_jobs.get(uid, "Done").split(":")[0] not in ("Done", "Done, with an error"):
        flash("An action photo of this player is already being made.", "info")
        return redirect(url_for("world.person", uid=uid, _anchor="photos"))
    world_db = Path(current_app.config["SQLALCHEMY_BINDS"][world_models.BIND].removeprefix("sqlite:///"))
    app = current_app._get_current_object()

    def run() -> None:
        with app.app_context():  # the maker reads its settings and key from the app
            try:
                picture, caption = world_portraits.make_action_photo(world_db, uid)
                world_images.add_generated_image(f"person:{uid}", "photograph", picture, caption)
                _photo_jobs[uid] = f"Done: a new photo, {caption[0].lower()}{caption[1:]}."
            except (world_portraits.PortraitError, world_images.ImageError) as exc:
                _photo_jobs[uid] = f"Done, with an error: {exc}"
            except Exception as exc:  # noqa: BLE001 - the page must never wait for ever
                app.logger.exception("action photo failed")
                _photo_jobs[uid] = f"Done, with an error: {type(exc).__name__}: {exc}"
            finally:
                db.session.remove()

    _photo_jobs[uid] = "Making the photo…"
    threading.Thread(target=run, name=f"action-photo-{uid}", daemon=True).start()
    flash("Making an action photo from his picture. It takes several minutes; reload this page to see it.", "info")
    return redirect(url_for("world.person", uid=uid, _anchor="photos"))


@world_bp.get("/clubs/<int:uid>/<int:season>")
def club_season(uid: int, season: int):
    with _connection() as c:
        team = q.teams(c, [uid]).get(uid)
        if team is None:
            abort(404)
        data = q.club_season(c, uid, season)
        seasons = q.club_seasons(c, uid)
        overview = q.season_overview(c, uid, season, data)
        start, end = q.league_window(c, data["league_uid"], season) if data["league_uid"] else q.season_window(season)
        how = tactics.how_they_play(c, uid, start, end, data["league_uid"])
        if how:
            how["pitch"] = q.pitch_places([{**p, "meta": f"{p['starts']} starts" + (f" · {p['rating']:.2f}" if p["rating"] else "")}
                                           for p in how["usual_eleven"]])
        team_ids = {uid} | {f["home_uid"] for f in data["fixtures"] + overview["upcoming"]} | {f["away_uid"] for f in data["fixtures"] + overview["upcoming"]}
        team_ids |= {t["from_uid"] for t in data["signed"]} | {t["to_uid"] for t in data["left"]}
        team_ids |= {row["team_uid"] for row in data["table"]}
        teams = q.teams(c, team_ids)
        competitions = q.competitions(c, {comp for comp, _ in data["by_competition"]} | {data["league_uid"]}
                                      | {x["competition_uid"] for x in data["won"] + data["runner_up"]}
                                      | {f["competition_uid"] for f in overview["upcoming"]})
    missing = sum(1 for player in data["players"] if picture_path(player["uid"]) is None)
    story = db.session.get(ClubSeasonStory, (uid, season))
    chapters = db.session.query(ClubSeasonChapter).filter_by(club_uid=uid, season=season).order_by(ClubSeasonChapter.sequence).all()
    return render_template("world/club_season.html", team=team, teams=teams, competitions=competitions, seasons=seasons,
                           story=story, story_sections=format_player_narrative(story.text) if story else [], chapters=chapters,
                           missing_pictures=missing, picture_job=_team_jobs.get((uid, season)),
                           missing_bios=len(_without_bio(data["players"])), bio_job=_bio_jobs.get((uid, season)),
                           versions=story_versions("season", uid, season), news=_season_news(uid, team["name"], season, data), how=how,
                           **overview, **data)


SEASON_NEWS = 8


def _season_news(uid: int, name: str, season: int, data: dict) -> list[dict]:
    """The published articles of the season (its first game to the next summer) about the club, newest first, then
    those that name it (a league round-up, a derby report)."""
    from app.models.world_soccer_article import WorldSoccerArticle
    from app.models.world_soccer_issue import WorldSoccerIssue
    from app.services import editions

    days = [f["date"] for f in data["fixtures"]]
    start = date.fromisoformat(str(min(days))) if days else date(season, 7, 1)
    end = date(season + 1, 7, 31)
    about = [a for a in editions.articles_about(f"club:{uid}", 60) if start <= a.issue.publication_date <= end]
    seen = {a.id for a in about}
    naming = [a for a in WorldSoccerArticle.query.join(WorldSoccerIssue).filter(
        WorldSoccerIssue.status == "published", WorldSoccerIssue.publication_date.between(start, end),
        (WorldSoccerArticle.headline.contains(name)) | (WorldSoccerArticle.body.contains(name)))
        .order_by(WorldSoccerIssue.publication_date.desc(), WorldSoccerArticle.sort_order).limit(SEASON_NEWS * 2) if a.id not in seen]
    rows = []
    for article, mention in [(a, False) for a in about] + [(a, True) for a in naming]:
        outlet = editions.outlet_of(article.issue)
        if outlet is None:
            continue
        rows.append({"headline": article.headline, "standfirst": article.standfirst, "section": article.section, "outlet": outlet.name,
                     "date": article.issue.publication_date, "mention": mention,
                     "url": url_for("news.edition", slug=outlet.slug, issue_id=article.issue_id) + f"#article-{article.id}"})
        if len(rows) == SEASON_NEWS:
            break
    return rows


def _without_bio(players: list[dict]) -> list[int]:
    uids = [p["uid"] for p in players]
    have = {b.fm_uid for b in db.session.query(PersonBio.fm_uid).filter(PersonBio.fm_uid.in_(uids))} if uids else set()
    return [u for u in uids if u not in have]


@world_bp.post("/clubs/<int:uid>/<int:season>/bios")
def write_team_bios(uid: int, season: int):
    key = (uid, season)
    if key in _bio_jobs and not _bio_jobs[key].startswith("Done"):
        flash("Bios for this squad are already being written.", "info")
        return redirect(url_for("world.club_season", uid=uid, season=season))
    with _connection() as c:
        players = q.club_season(c, uid, season)["players"]
    wanted = _without_bio(players)
    app = current_app._get_current_object()

    def run() -> None:
        written, failed = 0, 0
        with app.app_context():
            for number, person_uid in enumerate(wanted, 1):
                _bio_jobs[key] = f"Writing bio {number} of {len(wanted)}…"
                try:
                    bio_writer.write_bio(person_uid)
                    written += 1
                except bio_writer.BioError as exc:
                    failed += 1
                    app.logger.warning("bio for %s: %s", person_uid, exc)
                finally:
                    db.session.remove()
        _bio_jobs[key] = f"Done: {written} new bios" + (f", {failed} refused (see the log)." if failed else ".")

    _bio_jobs[key] = "Starting…"
    threading.Thread(target=run, name=f"bios-{uid}-{season}", daemon=True).start()
    flash(f"Writing {len(wanted)} bios (about 30 seconds each). Reload this page to follow it.", "info")
    return redirect(url_for("world.club_season", uid=uid, season=season))


@world_bp.post("/clubs/<int:uid>/<int:season>/pictures")
def generate_team_pictures(uid: int, season: int):
    key = (uid, season)
    if key in _team_jobs and not _team_jobs[key].startswith("Done"):
        flash("Pictures for this squad are already being made.", "info")
        return redirect(url_for("world.club_season", uid=uid, season=season))
    world_db = Path(current_app.config["SQLALCHEMY_BINDS"][world_models.BIND].removeprefix("sqlite:///"))
    app = current_app._get_current_object()

    def run() -> None:
        # in the app's context: the maker is read from the settings (without it the thread died at the first picture and
        # the page said "Starting…" for ever, 2 Nov 2026)
        with app.app_context():
            try:
                made = world_portraits.generate_team(world_db, uid, season, progress=lambda text: _team_jobs.__setitem__(key, f"Made {text}"))
                _team_jobs[key] = f"Done: {made} new pictures."
            except world_portraits.PortraitError as exc:
                _team_jobs[key] = f"Done, with an error: {exc}"
            except Exception as exc:  # noqa: BLE001 - the page must never wait for ever
                app.logger.exception("squad pictures failed")
                _team_jobs[key] = f"Done, with an error: {type(exc).__name__}: {exc}"
            finally:
                _forget_pictures()
                db.session.remove()

    _team_jobs[key] = "Starting…"
    threading.Thread(target=run, name=f"pictures-{uid}-{season}", daemon=True).start()
    flash("Making the missing pictures (about 25 seconds each). Reload this page to follow it.", "info")
    return redirect(url_for("world.club_season", uid=uid, season=season))


def _names(c, fixtures=(), extra_teams=(), extra_competitions=()):
    team_ids = set(extra_teams) | {f["home_uid"] for f in fixtures} | {f["away_uid"] for f in fixtures}
    competition_ids = set(extra_competitions) | {f["competition_uid"] for f in fixtures}
    return q.teams(c, team_ids), q.competitions(c, competition_ids)


@world_bp.get("/today")
def home():
    """The world at a glance: the big leagues' tables, the competitions in play, the biggest transfers,
    the newest champions and the papers' headlines."""
    from app.services.story_leads import followed_clubs

    followed = followed_clubs()
    with _connection() as c:
        data = q.home(c)
        glance = q.today_page(c, followed)
        fixtures = [f for i in glance["international"] for f in i["now"]["recent"] + i["now"]["upcoming"]]
        fixtures += [f for league in glance["leagues"] for f in league["big_games"]]
        table_teams = {r["team_uid"] for league in glance["leagues"] for r in league["now"]["table"][:10]}
        table_teams |= {r["team_uid"] for i in glance["international"] for g in i["now"]["groups"] for r in g["table"]}
        transfer_teams = {t[side] for t in glance["transfers"] for side in ("from_uid", "to_uid") if t[side]}
        teams, competitions = _names(c, fixtures, {w["team_uid"] for w in data["winners"]} | table_teams | transfer_teams,
                                     {w["competition_uid"] for w in data["winners"]} | {league["uid"] for league in glance["leagues"]}
                                     | {i["uid"] for i in glance["international"]})
    data.update(glance)
    return render_template("world/home.html", teams=teams, competitions=competitions, followed=set(followed),
                           headlines=_headlines(), **data)


HEADLINES = 8


def _headlines() -> list[dict]:
    """The newest published papers' lead stories, one or two from each outlet."""
    from app.models.world_soccer_article import WorldSoccerArticle
    from app.models.world_soccer_issue import WorldSoccerIssue
    from app.services import editions

    found = []
    issues = (WorldSoccerIssue.query.filter_by(status="published")
              .order_by(WorldSoccerIssue.knowledge_cutoff.desc(), WorldSoccerIssue.id.desc()).limit(12).all())
    for issue in issues:
        outlet = editions.outlet_of(issue)
        if outlet is None:
            continue
        articles = (WorldSoccerArticle.query.filter_by(issue_id=issue.id)
                    .order_by(WorldSoccerArticle.sort_order, WorldSoccerArticle.id).limit(2).all())
        for article in articles:
            found.append({"headline": article.headline, "outlet": outlet.name,
                          "href": url_for("news.edition", slug=outlet.slug, issue_id=issue.id) + f"#article-{article.id}"})
    return found[:HEADLINES]


@world_bp.get("/search")
def search():
    term = (request.args.get("q") or "").strip()
    with _connection() as c:
        today = q.game_date(c)
        found = q.search(c, term) if len(term) >= 2 else None
    return render_template("world/search.html", term=term, found=found, today=today)


@world_bp.get("/clubs")
def clubs():
    from app.services.story_leads import followed_clubs

    with _connection() as c:
        data, today = q.clubs_index(c, followed_clubs()), q.game_date(c)
    return render_template("world/clubs.html", today=today, **data)


@world_bp.get("/clubs/<int:uid>")
def club(uid: int):
    with _connection() as c:
        team = q.teams(c, [uid]).get(uid)
        if team is None:
            abort(404)
        data = q.club_overview(c, uid)
        data["relations"] = q.club_relations(c, team["club_uid"] or uid)
        data["money"] = q.club_money(c, team["club_uid"] or uid)
        data["staff"] = q.club_staff(c, team["club_uid"] or uid)
        data["managers"] = q.club_managers(c, uid)
        squad = q.club_squad(c, uid)
        data["injured"] = q.current_injuries(c, {p["uid"] for g in squad["teams"] for p in g["players"]})
        data["moods"] = moods.squad_moods(c, {p["uid"] for g in squad["teams"] for p in g["players"]})
        data["dressing_room"] = moods.dressing_room(c, uid)
        squad_teams = {p["on_loan_from"] for g in squad["teams"] for p in g["players"]} | {o["to_uid"] for o in squad["out_on_loan"]}
        squad_teams |= {(p["last_season"] or {}).get("team_uid") for g in squad["teams"] for p in g["players"]}
        squad_teams |= {(o["last_season"] or {}).get("team_uid") for o in squad["out_on_loan"]}
        today = q.game_date(c)
        data["now"] = _club_now(c, uid, today, squad) if today else None
        now_fixtures = (data["now"] or {}).get("fixtures", [])
        squad_teams |= {t for f in now_fixtures for t in (f["home_uid"], f["away_uid"])} | {r["team_uid"] for r in (data["now"] or {}).get("table_around", [])}
        teams, competitions = _names(c, data["recent"] + now_fixtures, {uid} | squad_teams,
                                     {h["competition_uid"] for h in data["honours"]} | {s["league_uid"] for s in data["seasons"]}
                                     | {f["competition_uid"] for f in now_fixtures})
    club_text = db.session.get(ClubText, uid)
    summary = format_player_narrative(club_text.profile_summary) if club_text else []
    return render_template("world/club.html", team=team, teams=teams, competitions=competitions, today=today, squad=squad,
                           club_text=club_text, about_summary=[p for section in summary for p in section["paragraphs"]],
                           about_sections=format_player_narrative(club_text.historical_context) if club_text else [],
                           club_versions=story_versions("club", uid, 0), model_label=deepseek_model_label,
                           **data)


def _club_now(connection, uid: int, today: date, squad: dict) -> dict:
    """The club's season at the game date: results, the next games, the table around it, the formations it uses, its
    likely eleven now and its last team sheet, and each first-team player's numbers and form."""
    season = today.year if today.month >= 7 else today.year - 1
    data = q.club_season(connection, uid, season)
    overview = q.season_overview(connection, uid, season, data)
    start, end = q.league_window(connection, data["league_uid"], season) if data["league_uid"] else q.season_window(season)
    how = tactics.how_they_play(connection, uid, start, end, data["league_uid"])
    likely = tactics.expected_eleven(connection, uid, today + timedelta(days=1))
    likely_pitch = q.pitch_places([{"person_uid": p, "name": likely["names"][p], "position": slot, "slot": 0,
                                    "meta": f"{tactics.SHORT.get(slot, slot)}" + (f" · {likely['form'][p]:.2f}" if likely["form"].get(p) else "")}
                                   for p, slot in likely["chosen"].items()]) if likely else []
    last = q._one(connection, "select f.* from fixtures f where (f.home_uid = :t or f.away_uid = :t) and f.home_goals is not null and exists "
                              "(select 1 from match_players m where m.fixture_id = f.id and m.start_position is not null) order by f.date desc limit 1", t=uid)
    last_sheet = next((s for s in _team_sheets(connection, last["id"]) if s["team_uid"] == uid), None) if last else None
    first_team = next((g["players"] for g in squad["teams"] if g["team"]["fm_uid"] == uid), [])
    form = tactics.player_form(connection, uid, [p["uid"] for p in first_team], start)
    numbers = {p["uid"]: p for p in data["players"]}
    manager = q._one(connection, "select fm_uid, name, staff_since from people where staff_job = 'Manager' and staff_team_uid = :t "
                                 "order by staff_since desc limit 1", t=uid)
    if manager:
        manager["profile"] = q.manager_profile(connection, manager["fm_uid"])
    return {**overview, "manager": manager, "game_partnerships": tactics.game_partnerships(connection, uid), "season": season, "league_uid": data["league_uid"], "table": data["table"], "how": how, "likely": likely,
            "likely_pitch": likely_pitch, "last": last, "last_sheet": last_sheet, "player_form": form, "numbers": numbers,
            "fixtures": overview["recent"] + overview["upcoming"] + ([last] if last else [])}


@world_bp.post("/clubs/<int:uid>/about")
def write_club_text(uid: int):
    try:
        story_writer.write_club_text(uid)
    except story_writer.StoryError as exc:
        flash(f"No club history: {exc}", "error")
    else:
        flash("New club history written; the text it replaced is kept among the earlier versions.", "success")
    return redirect(url_for("world.club", uid=uid, _anchor="about"))


@world_bp.get("/competitions/<int:uid>")
def competition(uid: int):
    with _connection() as c:
        comp = q.competition(c, uid)
        if comp is None:
            abort(404)
        editions = q.competition_editions(c, uid)
        parts = q.league_parts(c, uid) if any("champion_uid" in e for e in editions) else []
        now = q.competition_now(c, uid)
        now["xg_table"] = expected.xg_table(c, uid, *now["window"]) if now["is_league"] and now.get("window") else []
        clubs = q.league_clubs(c, uid) if now["is_league"] else []
        now_teams = {r["team_uid"] for r in now["table"]} | {f[side] for f in now["recent"] + now["upcoming"] for side in ("home_uid", "away_uid")}
        now_teams |= {r["team_uid"] for group in now["groups"] for r in group["table"]}
        teams = q.teams(c, {t for e in editions for t in e["places"].values()} | {t for p in parts for t in p["winners"].values()} | now_teams)
        competitions = q.competitions(c, [p["competition_uid"] for p in parts])
        today = q.game_date(c)
    titles = Counter(e["places"][1] for e in editions if 1 in e["places"])
    return render_template("world/competition.html", comp=comp, editions=editions, teams=teams, today=today, parts=parts,
                           competitions=competitions, most_titles=titles.most_common(12), now=now, clubs=clubs,
                           league_pictures=_league_pictures(uid) if now["is_league"] else None)


def _league_job_running(uid: int) -> bool:
    return uid in _league_jobs and not _league_jobs[uid]["text"].startswith("Done")


def _league_pictures(uid: int) -> dict | None:
    """The competition page's card: the league's first-team players without a picture, and whether the maker is local."""
    from app.services import picture_makers

    faces, old = _picture_sources()
    with _connection() as c:
        missing = world_portraits.league_without_pictures(c.connection.driver_connection, uid, faces, old)
    maker = picture_makers.maker()
    return {"missing": len(missing), "maker": world_portraits.LOCAL_MAKERS.get(maker), "other_maker": maker,
            "job": _league_jobs.get(uid, {}).get("text"), "running": _league_job_running(uid)}


@world_bp.post("/competitions/<int:uid>/pictures")
def generate_league_pictures(uid: int):
    """Pictures for every first-team player of the league's clubs who has none, one after another in the background,
    with a local maker only (Draw Things or InvokeAI): a few hundred pictures would cost real money online."""
    from app.services import picture_makers

    back = redirect(url_for("world.competition", uid=uid))
    if _league_job_running(uid):
        flash("Pictures for this league are already being made.", "info")
        return back
    if picture_makers.maker() not in world_portraits.LOCAL_MAKERS:
        flash("A league's pictures are made only with a local maker: choose Draw Things or InvokeAI on the Settings page.", "error")
        return back
    ready, why = picture_makers.online()
    if not ready:
        flash(f"No pictures: {why}.", "error")
        return back
    world_db = Path(current_app.config["SQLALCHEMY_BINDS"][world_models.BIND].removeprefix("sqlite:///"))
    with _connection() as c:
        wanted = world_portraits.league_without_pictures(c.connection.driver_connection, uid, *_picture_sources())
        names = q.teams(c, {team for _, team in wanted})
    app = current_app._get_current_object()
    job = _league_jobs[uid] = {"text": "Starting…", "stop": False}

    def run() -> None:
        made = failed = in_a_row = 0
        with app.app_context():
            try:
                for number, (person, team) in enumerate(wanted, 1):
                    if job["stop"]:
                        break
                    job["text"] = f"Making picture {number} of {len(wanted)} ({names.get(team, {}).get('name', '')}); {made} made so far"
                    try:
                        world_portraits.generate_one(world_db, person)  # keeps a picture made meanwhile
                        made, in_a_row = made + 1, 0
                    except world_portraits.PortraitError as exc:
                        failed, in_a_row = failed + 1, in_a_row + 1
                        app.logger.warning("league picture for %s: %s", person, exc)
                        if in_a_row >= LEAGUE_FAILURES:
                            job["text"] = f"Done, with an error: {made} new pictures, then {in_a_row} failed in a row ({exc})"
                            return
                    finally:
                        db.session.remove()
                        if made and made % 10 == 0:
                            _forget_pictures()
                end = "stopped" if job["stop"] else "finished"
                job["text"] = f"Done ({end}): {made} new pictures" + (f", {failed} failed (see the log)." if failed else ".")
            except Exception as exc:  # noqa: BLE001 - the page must never wait for ever
                app.logger.exception("league pictures failed")
                job["text"] = f"Done, with an error: {made} new pictures, then {type(exc).__name__}: {exc}"
            finally:
                _forget_pictures()

    threading.Thread(target=run, name=f"league-pictures-{uid}", daemon=True).start()
    flash(f"Making {len(wanted)} pictures with {world_portraits.LOCAL_MAKERS[picture_makers.maker()]}, one after another. "
          "Reload this page to follow it.", "info")
    return back


@world_bp.post("/competitions/<int:uid>/pictures/stop")
def stop_league_pictures(uid: int):
    if _league_job_running(uid):
        _league_jobs[uid]["stop"] = True
        flash("The pictures stop after the one being made now.", "info")
    return redirect(url_for("world.competition", uid=uid))


@world_bp.get("/competitions/<int:uid>/<int:year>")
def edition(uid: int, year: int):
    with _connection() as c:
        comp = q.competition(c, uid)
        data = q.edition(c, uid, year)
        if comp is None or data is None:
            abort(404)
        part_winners = {p["winners"].get(year) for p in data["parts"]}
        final = [data["final"]] if data["final"] else []
        teams, competitions = _names(c, data["fixtures"] + final, set(data["places"].values()) | {s["team_uid"] for s in data["scorers"]} | part_winners,
                                     {p["competition_uid"] for p in data["parts"]})
        today = q.game_date(c)
    story = db.session.get(EditionStory, (uid, year))
    chapters = db.session.query(EditionChapter).filter_by(competition_uid=uid, year=year).order_by(EditionChapter.sequence).all()
    return render_template("world/edition.html", comp=comp, teams=teams, competitions=competitions, today=today, story=story,
                           story_sections=format_player_narrative(story.text) if story else [], chapters=chapters,
                           versions=story_versions("edition", uid, year), **data)


@world_bp.get("/matches/<int:fixture_id>")
def match(fixture_id: int):
    with _connection() as c:
        data = q.match(c, fixture_id)
        if data is None:
            abort(404)
        teams, competitions = _names(c, [data["fixture"], *data["meetings"]])
        today = q.game_date(c)
        team_sheets = _team_sheets(c, fixture_id)
        known = q.fixture_player_games(c, fixture_id) if not team_sheets and data["fixture"]["home_goals"] is not None else [[], []]
    f = data["fixture"]
    texts = {t.kind: t for t in db.session.query(MatchText).filter_by(date=date.fromisoformat(str(f["date"])), home_uid=f["home_uid"],
                                                                      away_uid=f["away_uid"])}
    sections = {kind: _match_text_sections(t.text) for kind, t in texts.items()}
    return render_template("world/match.html", teams=teams, competitions=competitions, today=today, texts=texts,
                           text_sections=sections, team_sheets=team_sheets, known=known if any(known) else None, **data)


def _team_sheets(connection, fixture_id: int) -> list[dict]:
    """Each side's eleven on a pitch in its slots (shirt number, rating), its substitutions and the talking points of its
    selection; empty for a match without a team sheet."""
    sides = tactics.match_sides(connection, fixture_id)
    if not sides:
        return []
    notes = tactics.selection_notes(connection, fixture_id) or {}
    found = []
    for side in sides:
        players = [{**r, "slot": 0, "position": r["start_position"],
                    "meta": " · ".join(x for x in (tactics.SHORT.get(r["start_position"], ""), f"{r['rating']:.1f}" if r["rating"] else "") if x)}
                   for r in side["starters"]]
        picked = notes.get(side["team_uid"], {})
        found.append({"team_uid": side["team_uid"], "shape": tactics.shape_words(side["shape"]),
                      "shape_at_end": tactics.shape_words(side["shape_at_end"]) if side["shape_at_end"] != side["shape"] else None,
                      "pitch": q.pitch_places(players), "substitutions": picked.get("substitutions", []),
                      "notes": [n for n in picked.get("notes", []) if not n.startswith("Finished the game in")]})
    return found


def _match_text_sections(text: str) -> list[dict]:
    """A match text in sections; a short first line that does not end a sentence is its headline."""
    first, _, rest = text.strip().partition("\n")
    if rest.strip() and len(first.split()) <= 14 and not first.rstrip().endswith((".", "!", "?", ":")):
        sections = format_player_narrative(rest)
        return [{"heading": first.strip(), "paragraphs": [p for s in sections for p in ([s["heading"]] if s["heading"] else []) + s["paragraphs"]]}]
    return format_player_narrative(text)


@world_bp.get("/nations/<int:uid>")
def nation(uid: int):
    with _connection() as c:
        data = q.nation(c, uid)
        if data is None:
            abort(404)
        teams, competitions = _names(c, data["results"], {s["fm_uid"] for s in data["sides"]},
                                     {t["competition_uid"] for t in data["titles"]})
        today = q.game_date(c)
    return render_template("world/nation.html", teams=teams, competitions=competitions, today=today, **data)


@world_bp.get("/awards/<int:uid>")
def award(uid: int):
    with _connection() as c:
        data = q.award(c, uid)
        if data is None:
            abort(404)
        teams = q.teams(c, {row["club_uid"] for _, places in data["years"] for row in places.values() if row["club_uid"]})
        today = q.game_date(c)
    return render_template("world/award.html", teams=teams, today=today, **data)


@world_bp.get("/competitions")
def competitions():
    with _connection() as c:
        groups, today = q.competition_groups(c), q.game_date(c)
    return render_template("world/competitions.html", today=today, groups=groups)


@world_bp.get("/nations")
def nations():
    with _connection() as c:
        rows, today = q.nation_index(c), q.game_date(c)
    return render_template("world/index_list.html", title="Nations", today=today, rows=rows, kind="nation")


@world_bp.get("/awards")
def awards():
    with _connection() as c:
        rows, today = q.award_index(c), q.game_date(c)
    return render_template("world/index_list.html", title="Awards", today=today, rows=rows, kind="award")


def story_versions(kind: str, subject_uid: int, year: int) -> list[StoryVersion]:
    return (db.session.query(StoryVersion).filter_by(kind=kind, subject_uid=subject_uid, year=year)
            .order_by(StoryVersion.replaced_at.desc()).all())


@world_bp.post("/story-versions/<int:version_id>/restore")
def restore_story_version(version_id: int):
    try:
        kind, subject_uid, year = story_writer.restore_version(version_id)
    except story_writer.StoryError as exc:
        flash(str(exc), "error")
        return redirect(request.referrer or url_for("world.competitions"))
    flash("The earlier version is the story again; the one it replaced is kept among the earlier versions.", "success")
    if kind == "club":
        return redirect(url_for("world.club", uid=subject_uid, _anchor="about"))
    if kind == "season":
        return redirect(url_for("world.club_season", uid=subject_uid, season=year, _anchor="story"))
    return redirect(url_for("world.edition", uid=subject_uid, year=year, _anchor="story"))


@world_bp.post("/clubs/<int:uid>/<int:season>/story")
def write_season_story(uid: int, season: int):
    try:
        story_writer.write_club_season_story(uid, season, request.form.get("voice"))
    except story_writer.StoryError as exc:
        flash(f"No season story: {exc}", "error")
    else:
        flash("New season story written.", "success")
    return redirect(url_for("world.club_season", uid=uid, season=season, _anchor="story"))


@world_bp.post("/competitions/<int:uid>/<int:year>/story")
def write_edition_story(uid: int, year: int):
    try:
        story_writer.write_edition_story(uid, year, request.form.get("voice"))
    except story_writer.StoryError as exc:
        flash(f"No story: {exc}", "error")
    else:
        flash("New story written.", "success")
    return redirect(url_for("world.edition", uid=uid, year=year))


@world_bp.post("/matches/<int:fixture_id>/report")
def write_match_report(fixture_id: int):
    try:
        story_writer.write_match_report(fixture_id, replace_own=request.form.get("replace") == "1", voice=request.form.get("voice"))
    except story_writer.StoryError as exc:
        flash(f"No report: {exc}", "error")
    else:
        flash("New match report written.", "success")
    return redirect(url_for("world.match", fixture_id=fixture_id))


_IMAGE_PAGES = {"person": "world.person", "club": "world.club", "competition": "world.competition"}


@world_bp.post("/<any(person, club, competition):entity>/<int:uid>/images")
def add_world_image(entity: str, uid: int):
    try:
        world_images.add_image(f"{entity}:{uid}", request.form.get("kind", ""), request.files.get("image"), request.form.get("caption"))
    except world_images.ImageError as exc:
        flash(f"No picture added: {exc}", "error")
    else:
        flash("Picture added.", "success")
        _forget_pictures()
    return redirect(url_for(_IMAGE_PAGES[entity], uid=uid))


@world_bp.post("/images/<int:image_id>/hide")
def hide_world_image(image_id: int):
    world_images.hide_image(image_id)
    _forget_pictures()
    flash("Picture removed from the pages.", "success")
    return redirect(request.form.get("back") or url_for("world.home"))
