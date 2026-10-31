"""World Sport's history: competitions season by season, a club's trophy cabinet and records, the world's record books,
its legends and its awards, from the world database (read only, no AI). The heavier lists are kept per game date."""

from __future__ import annotations

from collections import Counter

from flask import url_for
from sqlalchemy.engine import Connection

from app.public.football import _games, crest
from app.services import world_queries as q

SENIOR = "('league', 'cup', 'continental')"
CLUB_KINDS = "(1, 2, 3)"  # league, cup and continental games: a club career
INTERNATIONAL = 4
RECORDS = 25
CLUB_LIST = 10
BIG_WINS = 10
LEGENDS = 60
LEGEND_GOALS, LEGEND_GAMES = 250, 600
ROUNDS_SHOWN = 4

_cache: dict[tuple, object] = {}


def cached(c: Connection, key: tuple, make):
    """The value made once per game date: the world changes only with an import."""
    day = q.game_date(c)
    if (key, day) not in _cache:
        if len(_cache) > 64:
            _cache.clear()
        _cache[(key, day)] = make()
    return _cache[(key, day)]


def trophy(uid: int | None) -> str | None:
    """The curator's trophy picture, then the shipped one, then the competition's logo."""
    if not uid:
        return None
    from app.services import world_images
    from app.services.logo_assets import resolve_logo_path, trophy_path

    uploaded = world_images.uploaded_trophy_url(uid)
    if uploaded:
        return uploaded
    if trophy_path(uid):
        return url_for("public.trophy", uid=uid)
    if resolve_logo_path("competition", uid, "normal"):
        return url_for("public.logo", entity_type="competition", fm_uid=uid)
    return None


def _teams(c: Connection, uids) -> dict[int, dict]:
    found = q.teams(c, uids)
    for t in found.values():
        t["crest"] = crest(t)
    return found


# -- competitions -----------------------------------------------------------------------------------------
def competitions(c: Connection) -> list[dict]:
    """The competitions by continent and country (the archive's competitions page), each with its number of seasons."""
    def make():
        seasons = Counter(r["competition_uid"] for r in q._rows(c, "select competition_uid from competition_editions"))
        groups = q.competition_groups(c)
        for group in groups:
            for r in group["international"]:
                r["seasons"] = seasons.get(r["fm_uid"], 0)
            for country in group["countries"]:
                for r in country["leagues"] + country["cups"]:
                    r["seasons"] = seasons.get(r["fm_uid"], 0)
        return groups
    return cached(c, ("competitions",), make)


def competition(c: Connection, uid: int) -> dict | None:
    comp = q.competition(c, uid)
    if comp is None:
        return None
    editions = q.competition_editions(c, uid)
    teams = _teams(c, {t for e in editions for t in e["places"].values()})
    for e in editions:
        e["teams"] = {place: teams.get(t, {"fm_uid": t}) for place, t in e["places"].items()}
    titles = Counter(e["places"][1] for e in editions if 1 in e["places"])
    most = [{"team": teams.get(t, {"fm_uid": t}), "count": n,
             "last": next((e["season_label"] for e in editions if e["places"].get(1) == t), "")} for t, n in titles.most_common(12)]
    return {"comp": comp, "editions": editions, "most": most, "trophy": trophy(uid)}


def edition(c: Connection, uid: int, year: int) -> dict | None:
    comp = q.competition(c, uid)
    data = q.edition(c, uid, year)
    if comp is None or data is None:
        return None
    uids = set(data["places"].values()) | {r["team_uid"] for r in data["table"]} | {s["team_uid"] for s in data["scorers"]}
    teams = _teams(c, uids)
    table = [{**r, "team": teams.get(r["team_uid"], {}), "crest": teams.get(r["team_uid"], {}).get("crest")} for r in data["table"]]
    rounds = []
    if not data["is_league"]:
        for r in reversed(data["rounds"][-ROUNDS_SHOWN - 1:]):
            if data["final"] and r.get("name") == "Final":
                continue  # shown on its own
            games = [g for g in r.get("games", []) if g["home_goals"] is not None]
            if games:
                rounds.append({"name": r.get("name") or "Round", "games": _games(c, games)})
    final = _games(c, [data["final"]])[0] if data["final"] else None
    return {"comp": comp, "edition": data["edition"], "label": (data["edition"] or {}).get("season_label") or str(year), "year": year,
            "places": {p: teams.get(t, {"fm_uid": t}) for p, t in data["places"].items()}, "table": table, "final": final,
            "rounds": rounds, "scorers": [{**s, "team": teams.get(s["team_uid"], {})} for s in data["scorers"]],
            "goals": data["goals"], "games": len(data["played"]), "trophy": trophy(uid), "is_league": data["is_league"]}


# -- a club's history ---------------------------------------------------------------------------------------
def club(c: Connection, team_uid: int) -> dict:
    """A club's trophy cabinet, its league finishes season by season, its all-time scorers and most games, its biggest wins."""
    def make():
        honours = q.club_honours(c, team_uid)
        comps = q.competitions(c, {h["competition_uid"] for h in honours})
        cabinet = [{**h, "name": comps.get(h["competition_uid"], {}).get("name"), "trophy": trophy(h["competition_uid"])} for h in honours]
        finishes = q._rows(c, "select e.year, e.season_label, p.place, c.fm_uid competition_uid, c.name, c.level from edition_places p "
                              "join competition_editions e on e.competition_uid = p.competition_uid and e.year = p.year "
                              "join competitions c on c.fm_uid = p.competition_uid where p.team_uid = :t and c.scope = 'league' "
                              "order by e.year desc", t=team_uid)
        scorers = q._rows(c, f"select ps.person_uid, p.name, sum(ps.goals) goals, sum(ps.starts + ps.substitute_appearances) games, "
                             f"min(ps.season_start) first, max(ps.season_start) last from player_seasons ps join people p on p.fm_uid = ps.person_uid "
                             f"where ps.team_uid = :t and ps.kind in {CLUB_KINDS} group by ps.person_uid order by goals desc limit :n",
                          t=team_uid, n=CLUB_LIST)
        games = q._rows(c, f"select ps.person_uid, p.name, sum(ps.goals) goals, sum(ps.starts + ps.substitute_appearances) games, "
                           f"min(ps.season_start) first, max(ps.season_start) last from player_seasons ps join people p on p.fm_uid = ps.person_uid "
                           f"where ps.team_uid = :t and ps.kind in {CLUB_KINDS} group by ps.person_uid order by games desc limit :n",
                        t=team_uid, n=CLUB_LIST)
        wins = q._rows(c, f"select f.* from fixtures f join competitions c on c.fm_uid = f.competition_uid where c.scope in {SENIOR} "
                          "and f.home_goals is not null and ((f.home_uid = :t and f.home_goals > f.away_goals) or (f.away_uid = :t and f.away_goals > f.home_goals)) "
                          "order by abs(f.home_goals - f.away_goals) desc, f.home_goals + f.away_goals desc, f.date desc limit 5", t=team_uid)
        for row in scorers + games:
            row["span"] = f"{q.season_label(row['first'])} to {q.season_label(row['last'])}" if row["first"] != row["last"] else q.season_label(row["first"])
        return {"cabinet": cabinet, "trophies": sum(h["count"] for h in honours), "finishes": finishes, "scorers": scorers, "games": games,
                "wins": _games(c, wins)}
    return cached(c, ("club", team_uid), make)


# -- the record books ---------------------------------------------------------------------------------------
def records(c: Connection) -> dict:
    def make():
        def people(kinds: str, order: str) -> list[dict]:
            return q._rows(c, f"select ps.person_uid, p.name, sum(ps.goals) goals, sum(ps.starts + ps.substitute_appearances) games, "
                              f"min(ps.season_start) first, max(ps.season_start) last from player_seasons ps join people p on p.fm_uid = ps.person_uid "
                              f"where ps.kind in {kinds} group by ps.person_uid order by {order} desc limit :n", n=RECORDS)
        titles = q._rows(c, "select p.team_uid, count(*) n from edition_places p join competitions c on c.fm_uid = p.competition_uid "
                            "where p.place = 1 and c.scope = 'league' and c.level = 1 group by p.team_uid order by n desc limit :n", n=RECORDS)
        continental = q._rows(c, "select p.team_uid, count(*) n from edition_places p join competitions c on c.fm_uid = p.competition_uid "
                                 "where p.place = 1 and c.scope = 'continental' and coalesce(c.reputation, 0) >= 120 "
                                 "group by p.team_uid order by n desc limit :n", n=RECORDS)
        wins = q._rows(c, "select f.* from fixtures f join competitions c on c.fm_uid = f.competition_uid where c.scope in ('league', 'continental') "
                          "and c.level is not 2 and coalesce(c.reputation, 0) >= 100 and f.home_goals is not null "
                          "order by abs(f.home_goals - f.away_goals) desc, f.home_goals + f.away_goals desc limit :n", n=BIG_WINS)
        teams = _teams(c, {r["team_uid"] for r in titles + continental})
        for row in titles + continental:
            row["team"] = teams.get(row["team_uid"], {})
        return {"scorers": people(CLUB_KINDS, "goals"), "games": people(CLUB_KINDS, "games"),
                "caps": people(f"({INTERNATIONAL})", "games"), "international_goals": people(f"({INTERNATIONAL})", "goals"),
                "titles": titles, "continental": continental, "wins": _games(c, wins)}
    return cached(c, ("records",), make)


def legends(c: Connection) -> list[dict]:
    """The greatest careers, by the Long Ball's measure: goals, games, trophies and awards."""
    def make():
        careers = q._rows(c, f"select ps.person_uid, p.name, min(ps.season_start) first, max(ps.season_start) last, "
                             f"sum(ps.starts + ps.substitute_appearances) games, sum(ps.goals) goals from player_seasons ps "
                             f"join people p on p.fm_uid = ps.person_uid where ps.kind in {CLUB_KINDS} group by ps.person_uid "
                             f"having sum(ps.goals) >= :g or sum(ps.starts + ps.substitute_appearances) >= :n", g=LEGEND_GOALS, n=LEGEND_GAMES)
        careers = sorted(careers, key=lambda r: -(r["goals"] / 100 + r["games"] / 250))[:LEGENDS * 2]
        awards = Counter(r["person_uid"] for r in q._rows(c, "select person_uid from award_places where place = 1"))
        found = []
        for r in careers:
            honours = [h for h in q.player_honours(c, r["person_uid"]) if h["place"] == 1]
            main = q._one(c, f"select team_uid, sum(starts + substitute_appearances) games from player_seasons where person_uid = :p "
                             f"and kind in {CLUB_KINDS} group by team_uid order by games desc limit 1", p=r["person_uid"])
            found.append({**r, "trophies": len(honours), "awards": awards.get(r["person_uid"], 0), "club_uid": main and main["team_uid"],
                          "score": r["goals"] / 300 + len(honours) / 20 + awards.get(r["person_uid"], 0) / 15 + r["games"] / 1000,
                          "span": f"{q.season_label(r['first'])} to {q.season_label(r['last'])}"})
        found.sort(key=lambda r: -r["score"])
        found = found[:LEGENDS]
        teams = _teams(c, {r["club_uid"] for r in found})
        for r in found:
            r["club"] = teams.get(r["club_uid"], {})
        return found
    return cached(c, ("legends",), make)


# -- awards ---------------------------------------------------------------------------------------------------
def awards(c: Connection) -> list[dict]:
    return cached(c, ("awards",), lambda: q.award_index(c))


def award(c: Connection, uid: int) -> dict | None:
    return q.award(c, uid)


def warm(c: Connection) -> None:
    """Work out the heavy lists ahead of the first visitor (legends need every legend's trophies)."""
    records(c)
    legends(c)
    competitions(c)
