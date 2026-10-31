"""Read the world database for the pages (app/routes/world.py) and for stories.

Every function takes a SQLAlchemy connection to the ``world`` bind and returns plain
dicts and lists, so the same facts can feed a page, an article or an MCP tool.
"""

from __future__ import annotations

import json
import re
import time
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.services import fm_save

FRIENDLY, LEAGUE, CUP, CONTINENTAL, INTERNATIONAL = range(5)
KIND_NAMES = {FRIENDLY: "Friendlies", LEAGUE: "League", CUP: "Cups", CONTINENTAL: "Continental", INTERNATIONAL: "National team"}
TRANSFER_KINDS = {1: "Transfer", 2: "Part-exchange", 3: "Loan", 4: "Free transfer", 6: "Compensation",
                  7: "Free agent", 8: "Trade", 9: "Released", 14: "Youth intake"}
SIGNINGS = (1, 2, 4, 6, 7, 8)


def _rows(connection: Connection, sql: str, **params) -> list[dict]:
    return [dict(row) for row in connection.execute(text(sql), params).mappings()]


def _one(connection: Connection, sql: str, **params) -> dict | None:
    row = connection.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def _add(target: dict, **amounts: int) -> None:
    for name, amount in amounts.items():
        target[name] += amount


def season_label(season: int) -> str:
    return f"{season}/{str(season + 1)[-2:]}"


def rating(total: int, rated: int) -> float | None:
    return round(total / rated / 10, 2) if rated else None


# -- shared lookups -------------------------------------------------------------------------------
def game_date(connection: Connection) -> date | None:
    value = connection.execute(text("select max(game_date) from saves")).scalar()
    return date.fromisoformat(value) if isinstance(value, str) else value


def teams(connection: Connection, uids) -> dict[int, dict]:
    """Team rows (name, club or nation, age limit) by FM uid."""
    wanted = sorted({uid for uid in uids if uid})
    found: dict[int, dict] = {}
    for start in range(0, len(wanted), 500):
        chunk = wanted[start : start + 500]
        marks = ",".join(str(int(uid)) for uid in chunk)
        for row in _rows(connection, f"select fm_uid, name, club_uid, nation_uid, age_limit from teams where fm_uid in ({marks})"):
            found[row["fm_uid"]] = row
    return found


def competitions(connection: Connection, uids) -> dict[int, dict]:
    wanted = sorted({uid for uid in uids if uid})
    if not wanted:
        return {}
    marks = ",".join(str(int(uid)) for uid in wanted)
    return {r["fm_uid"]: r for r in _rows(connection, f"select * from competitions where fm_uid in ({marks})")}


def league_uids(connection: Connection) -> set[int]:
    """Competitions that are some club's league (from the season statistics)."""
    return {r[0] for r in connection.execute(text(
        "select distinct league_uid from player_seasons where kind = 1 and league_uid is not null"))}


def edition_seasons(season_label_text: str, year: int) -> list[int]:
    """The club seasons an edition can belong to: 2061/62 -> [2061]; a one-year edition 2062 -> [2061, 2062]."""
    return [int(season_label_text[:4])] if "/" in season_label_text else [year - 1, year]


# -- people ---------------------------------------------------------------------------------------
def person(connection: Connection, uid: int) -> dict | None:
    """A person with nation and nationality. The save keeps no nationality for retired
    players; then the senior national team they scored for gives it."""
    found = _one(connection,
                 "select p.*, n.name nation, n.adjective nationality, st.name staff_team, st.club_uid staff_club_uid "
                 "from people p left join nations n on n.fm_uid = p.nation_uid left join teams st on st.fm_uid = p.staff_team_uid "
                 "where p.fm_uid = :uid", uid=uid)
    if found and found["nation_uid"] is None:
        scored_for = _one(connection,
                          "select n.fm_uid, n.name, n.adjective, count(*) goals from goals g join fixtures f on f.id = g.fixture_id "
                          "join teams t on t.fm_uid = case g.side when 0 then f.home_uid else f.away_uid end "
                          "join nations n on n.fm_uid = t.nation_uid "
                          "where g.scorer_uid = :uid and g.kind in (0, 2) and t.age_limit is null "
                          "group by n.fm_uid order by goals desc limit 1", uid=uid)
        if scored_for:
            found.update(nation_uid=scored_for["fm_uid"], nation=scored_for["name"], nationality=scored_for["adjective"])
    return found


def age_on(birth: str | date | None, when: date | None) -> int | None:
    if not birth or not when:
        return None
    born = date.fromisoformat(birth) if isinstance(birth, str) else birth
    return when.year - born.year - ((when.month, when.day) < (born.month, born.day))


def player_honours(connection: Connection, uid: int, leagues: set[int] | None = None) -> list[dict]:
    """Editions the player's club (or national team) won or finished second in, in a season they played that kind of competition.

    A league counts when they played league games for the club that season in that league; a domestic
    cup when they played cup games for the club, a continental competition when they played continental
    games (one continental substitute appearance once gave a youth player the DFB-Pokal); a competition
    of unknown scope when they played either. All domestic cups of a club share one statistics record,
    so a player who played only in one cup is also credited with another cup his club won that season.
    """
    leagues = league_uids(connection) if leagues is None else leagues
    played: dict[tuple[int, int], set[int]] = defaultdict(set)
    league_of: dict[tuple[int, int], int] = {}
    for r in _rows(connection, "select team_uid, season_start, kind, league_uid from player_seasons "
                               "where person_uid = :uid and starts + substitute_appearances > 0", uid=uid):
        played[(r["team_uid"], r["season_start"])].add(r["kind"])
        if r["kind"] == LEAGUE:
            league_of[(r["team_uid"], r["season_start"])] = r["league_uid"]
    clubs = {team for team, _ in played}
    if not clubs:
        return []
    marks = ",".join(str(int(t)) for t in clubs)
    honours = []
    needs = {"cup": {CUP}, "continental": {CONTINENTAL}}
    for r in _rows(connection,
                   "select p.competition_uid, p.year, p.place, p.team_uid, e.season_label, e.decided_on, c.scope "
                   "from edition_places p join competition_editions e on e.competition_uid = p.competition_uid and e.year = p.year "
                   "left join competitions c on c.fm_uid = p.competition_uid "
                   f"where p.place <= 2 and p.team_uid in ({marks})"):
        for season in edition_seasons(r["season_label"], r["year"]):
            kinds = played.get((r["team_uid"], season))
            if not kinds:
                continue
            if r["competition_uid"] in leagues:
                counts = LEAGUE in kinds and league_of.get((r["team_uid"], season)) == r["competition_uid"]
            else:
                counts = bool(kinds & needs.get(r["scope"], {CUP, CONTINENTAL}))
            if counts:
                honours.append({key: value for key, value in r.items() if key != "scope"} | {"season": season})
                break
    honours += _national_honours(connection, uid)
    honours.sort(key=lambda h: (str(h["decided_on"] or ""), h["competition_uid"]))
    return honours


def _national_honours(connection: Connection, uid: int) -> list[dict]:
    """His country's senior titles and finals (World Cup, continental championships) in seasons he played for it. The save
    files a player's international games under his club, so the season and his nationality tell."""
    person = globals()["person"](connection, uid)  # with the nation of a retired player, from the national team he scored for
    if not person or not person["nation_uid"]:
        return []
    capped = {r["season_start"] for r in _rows(connection, "select season_start from player_seasons where person_uid = :u and kind = :k "
                                                           "and starts + substitute_appearances > 0", u=uid, k=INTERNATIONAL)}
    if not capped:
        return []
    found = []
    for r in _rows(connection,
                   "select p.competition_uid, p.year, p.place, p.team_uid, e.season_label, e.decided_on from edition_places p "
                   "join competition_editions e on e.competition_uid = p.competition_uid and e.year = p.year "
                   "join teams t on t.fm_uid = p.team_uid left join competitions c on c.fm_uid = p.competition_uid "
                   "where p.place <= 2 and t.nation_uid = :n and t.age_limit is null and coalesce(c.scope, '') != 'youth'",
                   n=person["nation_uid"]):  # any other scope: the European/South American Super Cup has none
        decided = r["decided_on"]
        if decided:  # a summer tournament ends the season that began the July before
            decided = date.fromisoformat(str(decided)[:10])
            seasons = [decided.year - 1 if decided.month < 8 else decided.year]
        else:
            seasons = edition_seasons(r["season_label"], r["year"])
        season = next((s for s in seasons if s in capped), None)
        if season is not None:
            found.append(dict(r) | {"season": season})
    return found


def group_honours(honours: list[dict]) -> list[dict]:
    """One card per competition: how often, which seasons, with which club."""
    cards: dict[int, dict] = {}
    for h in honours:
        card = cards.setdefault(h["competition_uid"], {"competition_uid": h["competition_uid"], "count": 0, "seasons": [], "teams": []})
        card["count"] += 1
        card["seasons"].append(h["season_label"])
        if h["team_uid"] not in card["teams"]:
            card["teams"].append(h["team_uid"])
    return sorted(cards.values(), key=lambda c: (-c["count"], c["seasons"][0]))


@dataclass
class Career:
    rows: list[dict] = field(default_factory=list)  # one per season and club: league numbers and all-competition numbers
    totals: Counter = field(default_factory=Counter)


def player_career(connection: Connection, uid: int, honours: list[dict]) -> Career:
    won = defaultdict(list)
    for h in honours:
        if h["place"] == 1:
            won[(h["team_uid"], h["season"])].append(h["competition_uid"])
    per_club: dict[tuple[int, int], dict] = {}
    for r in _rows(connection, "select * from player_seasons where person_uid = :uid order by season_start, kind, seq", uid=uid):
        apps = r["starts"] + r["substitute_appearances"]
        key = (r["season_start"], r["team_uid"])
        row = per_club.setdefault(key, {"season": r["season_start"], "team_uid": r["team_uid"], "league_uid": None,
                                        "apps": 0, "subs": 0, "goals": 0, "assists": 0, "pom": 0, "rt": 0, "ra": 0,
                                        "all_apps": 0, "all_goals": 0, "national_apps": 0, "national_goals": 0,
                                        "conceded": 0, "clean_sheets": 0, "won": won.get((r["team_uid"], r["season_start"]), [])})
        if r["kind"] == LEAGUE:
            row["league_uid"] = r["league_uid"]
            _add(row, apps=apps, subs=r["substitute_appearances"], goals=r["goals"], assists=r["assists"],
                 pom=r["player_of_match"], rt=r["rating_total"], ra=r["rated_appearances"],
                 conceded=r["goals_conceded"], clean_sheets=r["clean_sheets"])
        if r["kind"] == INTERNATIONAL:
            _add(row, national_apps=apps, national_goals=r["goals"])
        elif r["kind"] != FRIENDLY:
            _add(row, all_apps=apps, all_goals=r["goals"])
    career = Career()
    for row in per_club.values():
        if not (row["apps"] or row["all_apps"] or row["national_apps"]):
            continue
        row["rating"] = rating(row["rt"], row["ra"])
        career.rows.append(row)
        for name in ("apps", "goals", "assists", "pom", "all_apps", "all_goals", "national_apps", "national_goals"):
            career.totals[name] += row[name]
    return career


def player_awards(connection: Connection, uid: int) -> list[dict]:
    grouped: dict[int, dict] = {}
    for a in _rows(connection, "select ap.*, aw.name, aw.short_name from award_places ap join awards aw on aw.fm_uid = ap.award_uid "
                               "where ap.person_uid = :uid order by ap.year", uid=uid):
        g = grouped.setdefault(a["award_uid"], {"award_uid": a["award_uid"], "name": a["name"], "wins": 0, "podiums": 0, "years": []})
        if a["place"] == 1:
            g["wins"] += 1
            g["years"].append(a["year"])
        else:
            g["podiums"] += 1
    return sorted(grouped.values(), key=lambda g: (-g["wins"], -g["podiums"], g["name"]))


def player_best_elevens(connection: Connection, uid: int) -> list[dict]:
    rows: dict[int, dict] = {}
    for b in _rows(connection, "select * from best_elevens where person_uid = :uid order by season_start", uid=uid):
        row = rows.setdefault(b["season_start"], {"season": b["season_start"], "club": None, "national": None})
        row["club" if b["team_uid"] else "national"] = b
    return list(rows.values())


def player_transfers(connection: Connection, uid: int) -> list[dict]:
    return _rows(connection, "select * from transfers where person_uid = :uid order by date", uid=uid)


def goals_in_finals(connection: Connection, uid: int, honours: list[dict]) -> list[dict]:
    finals = []
    for h in honours:
        fixture = final_of(connection, h["competition_uid"], h["decided_on"], h["team_uid"]) if h["decided_on"] else None
        if not fixture:
            continue
        minutes = [f"{g['minute']}{'+' + str(g['added_time']) if g['added_time'] else ''}'"
                   for g in _rows(connection, "select * from goals where fixture_id = :f and scorer_uid = :uid and kind in (0, 2)",
                                  f=fixture["id"], uid=uid)]
        if minutes:
            finals.append({**fixture, "minutes": minutes, "won": h["place"] == 1, "team_uid": h["team_uid"]})
    return finals


def player_goals_by_season(connection: Connection, uid: int) -> list[dict]:
    return _rows(connection,
                 "select case when cast(strftime('%m', f.date) as int) >= 7 then cast(strftime('%Y', f.date) as int) "
                 "else cast(strftime('%Y', f.date) as int) - 1 end season, f.competition_uid, count(*) goals, sum(g.kind = 2) penalties "
                 "from goals g join fixtures f on f.id = g.fixture_id where g.scorer_uid = :uid and g.kind in (0, 2) "
                 "group by 1, 2 order by 1, 3 desc", uid=uid)


# The hidden traits in words, for a page and for a writer: only the marked ones (16 and over, 7 and under) say anything.
CHARACTER_WORDS = {
    "Professionalism": ("a model professional", "careless about his trade"), "Ambition": ("fiercely ambitious", "content where he is"),
    "Loyalty": ("loyal to a badge", "no loyalty to a badge"), "Pressure": ("thrives under pressure", "wilts under pressure"),
    "Temperament": ("unflappable", "a short fuse"), "Sportsmanship": ("a sporting player", "will do anything to win"),
    "Important Matches": ("a big-game player", "goes missing in big games"), "Consistency": ("the same every week", "blows hot and cold"),
    "Adaptability": ("settles anywhere", "slow to settle in a new place"), "Versatility": ("plays anywhere he is asked", "one position only"),
    "Controversy": ("courts headlines", None), "Dirtiness": ("a dirty player", None), "Injury Proneness": ("injury prone", None),
}
CHARACTER_HIGH, CHARACTER_LOW = 16, 7


def character_words(traits: dict | None) -> list[str]:
    """The marked traits as phrases, the strongest first: Parkins (Important Matches 20, Pressure 18) is 'a big-game player,
    thrives under pressure'; Pardal (Versatility 19, Professionalism 18, Consistency 18, Temperament 4) has a short fuse."""
    if not traits:
        return []
    found = []
    for name, (high, low) in CHARACTER_WORDS.items():
        value = traits.get(name)
        if value is None:
            continue
        if value >= CHARACTER_HIGH and high:
            found.append((value - CHARACTER_HIGH + 1, high))
        elif value <= CHARACTER_LOW and low:
            found.append((CHARACTER_LOW - value + 1, low))
    return [words for _, words in sorted(found, key=lambda f: -f[0])]


def latest_attributes(connection: Connection, uid: int) -> dict | None:
    """A player's newest snapshot, its JSON columns (attributes, positions, traits) decoded."""
    found = _one(connection,
                 "select ps.*, s.game_date from player_snapshots ps join saves s on s.id = ps.save_id "
                 "where ps.person_uid = :uid order by s.game_date desc limit 1", uid=uid)
    if found:
        for column in ("attributes", "positions", "traits"):
            if isinstance(found.get(column), str):
                found[column] = json.loads(found[column])
    return found


# -- a club's season ------------------------------------------------------------------------------
def result_for(fixture: dict, team_uid: int) -> tuple[int, int, str]:
    """Goals for, goals against and W/D/L for one side, after extra time and penalties."""
    home = fixture["home_uid"] == team_uid
    goals = (fixture["home_goals"], fixture["away_goals"])
    if fixture["home_goals_after_extra_time"] is not None:
        goals = (fixture["home_goals_after_extra_time"], fixture["away_goals_after_extra_time"])
    scored, conceded = goals if home else goals[::-1]
    if scored != conceded:
        return scored, conceded, "W" if scored > conceded else "L"
    if fixture["home_penalties"] is not None:
        home_won = fixture["home_penalties"] > fixture["away_penalties"]
        return scored, conceded, "W" if home_won == home else "L"
    return scored, conceded, "D"


def season_window(season: int) -> tuple[str, str]:
    return f"{season}-07-01", f"{season + 1}-06-30"


def club_season(connection: Connection, team_uid: int, season: int) -> dict:
    league = _one(connection, "select league_uid, count(*) n from player_seasons where team_uid = :t and season_start = :s and kind = 1 "
                              "and league_uid is not null group by 1 order by n desc limit 1", t=team_uid, s=season)
    start, end = league_window(connection, league["league_uid"], season) if league else season_window(season)
    fixtures = _rows(connection, "select * from fixtures where (home_uid = :t or away_uid = :t) and date between :s and :e "
                                 "and home_goals is not null order by date", t=team_uid, s=start, e=end)
    record = Counter(played=0, won=0, drawn=0, lost=0, scored=0, conceded=0)
    by_competition: dict[int | None, list[dict]] = defaultdict(list)
    for f in fixtures:
        scored, conceded, f["result"] = result_for(f, team_uid)
        record["played"] += 1
        record["scored"] += scored
        record["conceded"] += conceded
        record[{"W": "won", "D": "drawn", "L": "lost"}[f["result"]]] += 1
        by_competition[f["competition_uid"]].append(f)
    editions = _rows(connection, "select p.*, e.season_label, e.decided_on from edition_places p join competition_editions e "
                                 "on e.competition_uid = p.competition_uid and e.year = p.year "
                                 "where p.team_uid = :t and p.place <= 2 and e.decided_on between :s and :e2",
                     t=team_uid, s=start, e2=str(date.fromisoformat(end) + timedelta(days=62)))
    finals = []
    leagues = league_uids(connection)
    for x in editions:
        if x["competition_uid"] in leagues:
            continue  # a league is decided by its last game, not a final
        final = final_of(connection, x["competition_uid"], x["decided_on"], team_uid) if x["decided_on"] else None
        if final:
            final["result"] = result_for(final, team_uid)[2]
            finals.append({**final, "won": x["place"] == 1})
    squad: dict[int, dict] = {}
    for r in _rows(connection, "select ps.*, p.name, p.nation_uid from player_seasons ps join people p on p.fm_uid = ps.person_uid "
                               "where ps.team_uid = :t and ps.season_start = :season", t=team_uid, season=season):
        s = squad.setdefault(r["person_uid"], {"uid": r["person_uid"], "name": r["name"], "league_apps": 0, "league_goals": 0,
                                               "apps": 0, "goals": 0, "assists": 0, "rt": 0, "ra": 0, "pom": 0, "awards": []})
        apps = r["starts"] + r["substitute_appearances"]
        if r["kind"] == LEAGUE:
            s["league_apps"] += apps
            s["league_goals"] += r["goals"]
        if r["kind"] not in (FRIENDLY, INTERNATIONAL):
            _add(s, apps=apps, goals=r["goals"], assists=r["assists"], rt=r["rating_total"],
                 ra=r["rated_appearances"], pom=r["player_of_match"])
    # The game writes a season's numbers into the players' history only when the season ends: in the season being played
    # they are empty (Liverpool 2063/64: 21 league rows, all with 0 games). The match reports have every game.
    for uid, r in _season_from_reports(connection, team_uid, start, end, league["league_uid"] if league else None).items():
        s = squad.setdefault(uid, {"uid": uid, "name": r["name"], "league_apps": 0, "league_goals": 0, "apps": 0, "goals": 0,
                                   "assists": 0, "rt": 0, "ra": 0, "pom": 0, "awards": []})
        if r["apps"] > s["apps"]:
            s.update(league_apps=r["league_apps"], league_goals=r["league_goals"], apps=r["apps"], goals=r["goals"],
                     assists=r["assists"], rt=r["rt"], ra=r["ra"], from_reports=True)
    for a in _rows(connection, "select ap.person_uid, aw.short_name, ap.place from award_places ap join awards aw on aw.fm_uid = ap.award_uid "
                               "where ap.year = :y and ap.place = 1", y=season + 1):
        if a["person_uid"] in squad:
            squad[a["person_uid"]]["awards"].append(a["short_name"])
    for s in squad.values():
        s["rating"] = round(s["rt"] / s["ra"], 2) if s.get("from_reports") and s["ra"] else rating(s["rt"], s["ra"])
    players = sorted((s for s in squad.values() if s["apps"]), key=lambda s: (-s["apps"], -s["goals"]))
    eleven = _rows(connection, "select b.*, p.name from best_elevens b left join people p on p.fm_uid = b.person_uid "
                               "where b.team_uid = :t and b.season_start = :season order by b.slot", t=team_uid, season=season)
    signed = _rows(connection, "select t.*, p.name from transfers t join people p on p.fm_uid = t.person_uid "
                               f"where t.to_uid = :t and t.date between :s and :e and t.kind in {SIGNINGS} order by t.fee desc, t.date",
                   t=team_uid, s=start, e=end)
    left = _rows(connection, "select t.*, p.name from transfers t join people p on p.fm_uid = t.person_uid "
                             "where t.from_uid = :t and t.date between :s and :e and t.kind in (1, 2, 3, 4, 9) order by t.fee desc, t.date",
                 t=team_uid, s=start, e=end)
    top_scorer = max(players, key=lambda s: s["goals"], default=None)
    table = league_table(connection, league["league_uid"], start, end) if league else []
    return {
        "team_uid": team_uid, "season": season, "record": record, "fixtures": fixtures,
        "league_uid": league["league_uid"] if league else None, "table": table, "pitch": pitch_places(eleven),
        "by_competition": sorted(by_competition.items(), key=lambda kv: -len(kv[1])),
        "won": [x for x in editions if x["place"] == 1], "runner_up": [x for x in editions if x["place"] == 2],
        "finals": finals, "players": players, "eleven": eleven, "signed": signed, "left": left, "top_scorer": top_scorer,
    }


# The lines of a compact squad, and the positions in each (the game's best-eleven codes).
SQUAD_LINES = (("Goalkeepers", ("GK",)), ("Defence", ("D (C)", "D (L)", "D (R)", "SW", "WB (L)", "WB (R)")),
               ("Midfield", ("DM", "M (C)", "M (L)", "M (R)", "AM (C)")), ("Attack", ("AM (L)", "AM (R)", "ST (C)")))
RECENT_RESULTS, COMING_UP, COMING_UP_DAYS = 6, 5, 28


def season_overview(connection: Connection, team_uid: int, season: int, data: dict) -> dict:
    """What a club season's front page shows: the recent results and the form, the next games (the current season),
    the squad in four lines with each player's season numbers (the squad now in the current season, the players who
    played in an older one), who is injured and who is new, and the league table around the club."""
    today = game_date(connection)
    current = bool(today) and season == (today.year if today.month >= 7 else today.year - 1)
    friendly = {r["fm_uid"] for r in _rows(connection, "select fm_uid from competitions where scope = 'friendly'")}
    played = [f for f in data["fixtures"] if f["competition_uid"] and f["competition_uid"] not in friendly]
    recent = list(reversed(played[-RECENT_RESULTS:]))
    upcoming = _rows(connection, "select * from fixtures where (home_uid = :t or away_uid = :t) and home_goals is null "
                                 "and date >= :d and date <= :e order by date limit :n", t=team_uid, d=str(today),
                     e=str(today + timedelta(days=COMING_UP_DAYS)), n=COMING_UP * 2) if current else []
    upcoming = [f for f in upcoming if f["competition_uid"] not in friendly][:COMING_UP]
    stats = {p["uid"]: p for p in data["players"]}
    if current:
        people = _rows(connection, "select p.fm_uid uid, p.name, p.birth_date from people p where p.team_uid = :t "
                                   "and p.staff_job is null", t=team_uid)
    else:
        people = _rows(connection, f"select fm_uid uid, name, birth_date from people where fm_uid in "
                                   f"({','.join(str(int(u)) for u in stats) or '0'})")
    positions = main_positions(connection, [p["uid"] for p in people])
    injured = current_injuries(connection, [p["uid"] for p in people]) if current else {}
    new = {t["person_uid"] for t in data["signed"]}
    when = today if current else date(season + 1, 6, 30)
    lines = {name: [] for name, _ in SQUAD_LINES}
    for p in people:
        code = positions.get(p["uid"])
        line = next((name for name, codes in SQUAD_LINES if code in codes), "Midfield")
        s = stats.get(p["uid"], {})
        lines[line].append({**p, "position": code, "age": age_on(p["birth_date"], when), "apps": s.get("apps", 0),
                            "goals": s.get("goals", 0), "assists": s.get("assists", 0), "rating": s.get("rating"),
                            "injury": injured.get(p["uid"]), "new": p["uid"] in new})
    for players in lines.values():
        players.sort(key=lambda p: (-p["apps"], -(p["rating"] or 0), p["name"]))
    table = data["table"]
    around = []
    mine = next((i for i, r in enumerate(table) if r["team_uid"] == team_uid), None)
    if mine is not None:
        keep = set(range(min(3, len(table)))) | set(range(max(mine - 2, 0), min(mine + 3, len(table))))
        around = [table[i] for i in sorted(keep)]
    return {"current": current, "today": today, "recent": recent, "upcoming": upcoming,
            "form": [f["result"] for f in played[-RECENT_RESULTS:]], "squad_lines": [(name, lines[name]) for name, _ in SQUAD_LINES],
            "table_around": around}


def _season_from_reports(connection: Connection, team_uid: int, start: str, end: str, league_uid: int | None) -> dict[int, dict]:
    """Each player's games, goals, assists and ratings for the team in its competitive matches between two dates, from
    the match reports (ratings as the reports give them, 6.0-10.0, summed)."""
    found: dict[int, dict] = {}
    for r in _rows(connection, "select m.person_uid, p.name, f.competition_uid, count(*) apps, sum(m.goals) goals, sum(m.assists) assists, "
                               "sum(coalesce(m.rating, 0)) rt, sum(m.rating is not null) ra from match_players m "
                               "join fixtures f on f.id = m.fixture_id join competitions c on c.fm_uid = f.competition_uid "
                               "join people p on p.fm_uid = m.person_uid where m.played = 1 and f.date between :s and :e "
                               "and ((f.home_uid = :t and m.side = 0) or (f.away_uid = :t and m.side = 1)) "
                               "and c.scope in ('league', 'cup', 'continental') group by m.person_uid, f.competition_uid",
                   t=team_uid, s=start, e=end):
        x = found.setdefault(r["person_uid"], {"name": r["name"], "apps": 0, "goals": 0, "assists": 0, "rt": 0.0, "ra": 0,
                                               "league_apps": 0, "league_goals": 0})
        _add(x, apps=r["apps"], goals=r["goals"], assists=r["assists"], rt=r["rt"], ra=r["ra"])
        if r["competition_uid"] == league_uid:
            _add(x, league_apps=r["apps"], league_goals=r["goals"])
    return found


# Where each position stands on a pitch drawn upright (attack at the top): x and y in per cent.
PITCH_SPOTS = {
    "ST (C)": (50, 13), "AM (L)": (16, 30), "AM (C)": (50, 31), "AM (R)": (84, 30), "M (L)": (12, 49), "M (C)": (50, 51),
    "M (R)": (88, 49), "WB (L)": (10, 63), "DM": (50, 66), "WB (R)": (90, 63), "D (L)": (15, 79), "D (C)": (50, 81),
    "D (R)": (85, 79), "SW": (50, 89), "GK": (50, 94),
}
PITCH_SPREAD = 25  # per cent between two players in the same position


def pitch_places(eleven: list[dict]) -> list[dict]:
    """The best eleven's starters with an (x, y) on the pitch; players in the same position stand side by side."""
    starters = [dict(row) for row in eleven if row["slot"] < 11]
    by_spot: dict[str, list[dict]] = defaultdict(list)
    for row in starters:
        by_spot[row["position"] if row["position"] in PITCH_SPOTS else "M (C)"].append(row)
    for spot, rows in by_spot.items():
        x, y = PITCH_SPOTS[spot]
        for index, row in enumerate(rows):
            offset = (index - (len(rows) - 1) / 2) * PITCH_SPREAD
            row["x"], row["y"] = max(8, min(92, x + offset)), y
    return starters


# The game's words for a position rating (1-20), and the lines and sides of its position label.
POSITION_LEVELS = ((20, "natural"), (15, "accomplished"), (10, "competent"), (5, "unconvincing"), (2, "awkward"), (1, "ineffectual"))
_LINES = (("GK", "Goalkeeper", ("GK",)), ("D", "Defender", ("D (R)", "D (L)", "D (C)")), ("SW", "Sweeper", ("SW",)),
          ("WB", "Wing-Back", ("WB (R)", "WB (L)")), ("DM", "Defensive Midfielder", ("DM",)),
          ("M", "Midfielder", ("M (R)", "M (L)", "M (C)")), ("AM", "Attacking Midfielder", ("AM (R)", "AM (L)", "AM (C)")),
          ("ST", "Striker", ("ST (C)",)))
_SIDES = {"R": "Right", "L": "Left", "C": "Centre"}
PLAYABLE = 15  # a rating the game lists among a player's positions
SMALL_PITCH_SPOTS = PITCH_SPOTS | {"SW": (50, 88.5), "GK": (50, 96)}  # labels, not faces: room for both behind the defence


def position_level(rating: int) -> str:
    return next(word for floor, word in POSITION_LEVELS if rating >= floor)


def player_positions(snapshot: dict | None) -> dict | None:
    """A player's positions from his latest snapshot: the game's label ("Defender (Right, Centre)",
    "Striker (Centre) / AM (RC)"), each position on the pitch with its rating, and his feet."""
    if not snapshot or not snapshot.get("positions"):
        return None
    ratings = snapshot["positions"]
    if isinstance(ratings, str):
        ratings = json.loads(ratings)
    lines = []
    for short, long, codes in _LINES:
        best = max(ratings.get(code, 0) for code in codes)
        sides = "".join(code[-2] for code in codes if ratings.get(code, 0) >= PLAYABLE and "(" in code)
        if best >= PLAYABLE:
            lines.append((best, short, long, sides))
    lines.sort(key=lambda line: -line[0])
    parts = []
    for number, (_, short, long, sides) in enumerate(lines):
        if number == 0:
            parts.append(f"{long} ({', '.join(_SIDES[side] for side in sides)})" if sides else long)
        else:
            parts.append(f"{short} ({sides})" if sides else short)
    spots = [{"code": code, "short": "ST" if code == "ST (C)" else code.replace(" (", "").rstrip(")"), "rating": rating,
              "level": position_level(rating), "x": SMALL_PITCH_SPOTS[code][0], "y": SMALL_PITCH_SPOTS[code][1]}
             for code, rating in ratings.items() if code in SMALL_PITCH_SPOTS]
    left, right = snapshot.get("left_foot"), snapshot.get("right_foot")
    foot = None
    if left and right:
        foot = ("Either foot" if min(left, right) >= PLAYABLE else
                f"Right foot (left {left})" if right >= left else f"Left foot (right {right})")
    return {"label": " / ".join(parts), "natural": [code for code, rating in ratings.items() if rating == 20],
            "spots": spots, "foot": foot, "left_foot": left, "right_foot": right}


def main_positions(connection: Connection, uids) -> dict[int, str]:
    """Each player's main position (a best-eleven code such as "D (C)"): his highest position rating
    in the newest save that has him, the best elevens breaking a tie; the best elevens alone for a
    player the saves give no ratings for (one who retired before the first save)."""
    marks = ",".join(str(int(uid)) for uid in uids) or "0"
    elevens: dict[int, Counter] = defaultdict(Counter)
    for row in _rows(connection, f"select person_uid, position from best_elevens where position is not null and person_uid in ({marks})"):
        elevens[row["person_uid"]][row["position"]] += 1
    found = {uid: counts.most_common(1)[0][0] for uid, counts in elevens.items()}
    for row in _rows(connection, "select person_uid, positions from (select ps.person_uid, ps.positions, row_number() over "
                                 "(partition by ps.person_uid order by s.game_date desc) n from player_snapshots ps join saves s on s.id = ps.save_id "
                                 f"where ps.person_uid in ({marks}) and ps.positions is not null) where n = 1"):
        ratings = json.loads(row["positions"]) if isinstance(row["positions"], str) else row["positions"]
        best = max(ratings.values())
        top = [code for code, rating in ratings.items() if rating == best]
        counts = elevens.get(row["person_uid"], Counter())
        found[row["person_uid"]] = max(top, key=lambda code: counts[code])
    return found


def club_seasons(connection: Connection, team_uid: int) -> list[int]:
    return [r[0] for r in connection.execute(text(
        "select distinct season_start from player_seasons where team_uid = :t order by 1 desc"), {"t": team_uid})]


# -- clubs ------------------------------------------------------------------------------------------
def club_squad(connection: Connection, club_uid: int) -> dict:
    """The players at a club's teams now (the newest save), by team, and the players it has out
    on loan.

    A player's current team (``people.team_uid``) is the club that holds the contract, or the
    loan club for some loans. A loan still runs when it is the player's last move and the player
    is at the loan club or has a row for the current season there (two-season loans).
    """
    today = game_date(connection)
    season = (today.year if today.month >= 7 else today.year - 1) if today else 0
    own = _rows(connection, "select fm_uid, name, age_limit from teams where club_uid = :c or fm_uid = :c "
                            "order by fm_uid != :c, age_limit is not null, age_limit desc", c=club_uid)
    own_ids = {t["fm_uid"] for t in own}
    team_ids = ",".join(str(int(t)) for t in own_ids) or "0"
    since = str(today.replace(year=today.year - 3)) if today else "1900-01-01"
    loans = _rows(connection,
        "select t.person_uid, t.from_uid, t.to_uid, t.date from transfers t "
        f"where t.kind = 3 and (t.from_uid in ({team_ids}) or t.to_uid in ({team_ids})) and t.date >= :since "
        "and t.date = (select max(date) from transfers x where x.person_uid = t.person_uid)", since=since)
    members = _rows(connection, f"select fm_uid from people where team_uid in ({team_ids})")
    uids = {m["fm_uid"] for m in members} | {loan["person_uid"] for loan in loans}
    marks = ",".join(str(int(u)) for u in uids) or "0"
    people = {p["uid"]: p for p in _rows(connection,
        f"select p.fm_uid uid, p.name, p.birth_date, p.team_uid, n.adjective nationality from people p "
        f"left join nations n on n.fm_uid = p.nation_uid where p.fm_uid in ({marks})")}
    this_season: dict[int, set[int]] = defaultdict(set)
    for r in _rows(connection, f"select person_uid, team_uid from player_seasons where person_uid in ({marks}) "
                               "and season_start >= :season and kind = 1", season=season):
        this_season[r["person_uid"]].add(r["team_uid"])
    running = {loan["person_uid"]: loan for loan in loans
               if people.get(loan["person_uid"]) and (people[loan["person_uid"]]["team_uid"] == loan["to_uid"]
                                                      or loan["to_uid"] in this_season[loan["person_uid"]])}
    at_club: dict[int, int] = {}  # person -> the club's team they are in
    for uid, p in people.items():
        loan = running.get(uid)
        if loan and loan["to_uid"] in own_ids:
            at_club[uid] = loan["to_uid"]  # loaned in
        elif p["team_uid"] in own_ids and not (loan and loan["from_uid"] in own_ids and loan["to_uid"] not in own_ids):
            at_club[uid] = p["team_uid"]
    out = [{**people[uid], "to_uid": loan["to_uid"], "from_uid": loan["from_uid"], "date": loan["date"]}
           for uid, loan in running.items() if loan["from_uid"] in own_ids and loan["to_uid"] not in own_ids]
    out.sort(key=lambda o: str(o["date"]), reverse=True)

    shown = list(at_club) + [o["uid"] for o in out]
    marks = ",".join(str(int(u)) for u in shown) or "0"
    positions: dict[int, str] = {}
    for r in _rows(connection, f"select person_uid, position from best_elevens where person_uid in ({marks}) and position is not null "
                               "order by season_start"):
        positions[r["person_uid"]] = r["position"]
    last_season: dict[int, dict] = {}
    for r in _rows(connection, f"select person_uid, team_uid, season_start, starts + substitute_appearances games, goals from player_seasons "
                               f"where person_uid in ({marks}) and kind = 1 and starts + substitute_appearances > 0 "
                               "order by season_start, starts + substitute_appearances"):
        last_season[r["person_uid"]] = r
    players = []
    for uid, team in at_club.items():
        loan = running.get(uid)
        players.append({**people[uid], "team_uid": team, "on_loan_from": loan["from_uid"] if loan and loan["to_uid"] == team else None})
    for p in players + out:
        p["age"] = age_on(p["birth_date"], today)
        p["position"] = positions.get(p["uid"])
        p["last_season"] = last_season.get(p["uid"])
    by_team = [{"team": t, "players": sorted((p for p in players if p["team_uid"] == t["fm_uid"]),
                                             key=lambda p: (p["age"] is None, -(p["age"] or 0), p["name"]))} for t in own]
    return {"teams": [group for group in by_team if group["players"]], "out_on_loan": out, "today": today}


def club_honours(connection: Connection, team_uid: int) -> list[dict]:
    rows = _rows(connection,
                 "select p.competition_uid, count(*) count, group_concat(e.season_label, ', ') seasons, max(e.year) last_year "
                 "from edition_places p join competition_editions e on e.competition_uid = p.competition_uid and e.year = p.year "
                 "where p.team_uid = :t and p.place = 1 group by 1 order by count desc", t=team_uid)
    for r in rows:
        r["seasons"] = sorted(r["seasons"].split(", ")) if r["seasons"] else []
    return rows


def league_table(connection: Connection, competition_uid: int, start: str, end: str) -> list[dict]:
    """A table built from the played league fixtures between two dates (3 points a win).

    Only the league's main stage counts (the stage with the most games): promotion play-offs
    and relegation play-offs share the competition but are not part of the table.
    """
    fixtures = _rows(connection, "select * from fixtures where competition_uid = :c and date between :s and :e "
                                 "and home_goals is not null order by date", c=competition_uid, s=start, e=end)
    if fixtures:
        main = Counter(f["stage"] for f in fixtures).most_common(1)[0][0]
        fixtures = [f for f in fixtures if f["stage"] == main]
    return table_from(fixtures)


def table_from(fixtures: list[dict]) -> list[dict]:
    """A table from games in date order (3 points a win); unplayed games do not count."""
    rows: dict[int, dict] = {}
    for f in fixtures:
        if f["home_goals"] is None:
            continue
        for team, scored, conceded in ((f["home_uid"], f["home_goals"], f["away_goals"]), (f["away_uid"], f["away_goals"], f["home_goals"])):
            r = rows.setdefault(team, {"team_uid": team, "played": 0, "won": 0, "drawn": 0, "lost": 0, "scored": 0, "conceded": 0, "points": 0, "form": []})
            result = "W" if scored > conceded else "D" if scored == conceded else "L"
            _add(r, played=1, scored=scored, conceded=conceded, points={"W": 3, "D": 1, "L": 0}[result],
                 **{{"W": "won", "D": "drawn", "L": "lost"}[result]: 1})
            r["form"] = (r["form"] + [result])[-5:]
    table = sorted(rows.values(), key=lambda r: (-r["points"], -(r["scored"] - r["conceded"]), -r["scored"]))
    for position, r in enumerate(table, 1):
        r["position"] = position
        r["difference"] = r["scored"] - r["conceded"]
    return table


def club_overview(connection: Connection, team_uid: int) -> dict:
    seasons = []
    for season in club_seasons(connection, team_uid):
        league = _one(connection, "select league_uid, count(*) n from player_seasons where team_uid = :t and season_start = :s and kind = 1 "
                                  "and league_uid is not null group by 1 order by n desc limit 1", t=team_uid, s=season)
        if not league:
            continue
        start, end = league_window(connection, league["league_uid"], season)
        table = league_table(connection, league["league_uid"], start, end)
        row = next((r for r in table if r["team_uid"] == team_uid), None)
        won = [r["competition_uid"] for r in _rows(
            connection, "select p.competition_uid from edition_places p join competition_editions e on e.competition_uid = p.competition_uid "
                        "and e.year = p.year where p.team_uid = :t and p.place = 1 and e.decided_on between :s and :e2",
            t=team_uid, s=start, e2=f"{season + 1}-08-31")]
        top = _one(connection, "select ps.person_uid, p.name, ps.goals from player_seasons ps join people p on p.fm_uid = ps.person_uid "
                               "where ps.team_uid = :t and ps.season_start = :s and ps.kind = 1 order by ps.goals desc limit 1", t=team_uid, s=season)
        seasons.append({"season": season, "league_uid": league["league_uid"], "position": row["position"] if row else None,
                        "points": row["points"] if row else None, "teams_in_league": len(table), "won": won, "top": top})
    leaders = ("select ps.person_uid, p.name, count(distinct ps.season_start) seasons, sum(ps.starts + ps.substitute_appearances) apps, "
               "sum(ps.goals) goals from player_seasons ps join people p on p.fm_uid = ps.person_uid where ps.team_uid = :t and ps.kind = 1 "
               "group by 1 order by {} desc limit 15")
    return {
        "honours": club_honours(connection, team_uid), "seasons": seasons,
        "top_scorers": _rows(connection, leaders.format("goals"), t=team_uid),
        "most_apps": _rows(connection, leaders.format("apps"), t=team_uid),
        "players": connection.execute(text("select count(distinct person_uid) from player_seasons where team_uid = :t "
                                           "and starts + substitute_appearances > 0"), {"t": team_uid}).scalar(),
        "recent": _rows(connection, "select * from fixtures where (home_uid = :t or away_uid = :t) and home_goals is not null "
                                    "order by date desc limit 10", t=team_uid),
    }


LEGEND_LEVEL = 100


def relation_group(relation: str, level: int, permanent: bool) -> str:
    """The game's name for a club's favourite: a Legend (100, permanent), an Icon (permanent) or
    Favoured personnel; else "Rival" or "Disliked"."""
    if relation == "favourite":
        return "Legend" if permanent and level >= LEGEND_LEVEL else "Icon" if permanent else "Favoured"
    return "Rival" if relation == "rival" else "Disliked"


def _reason(relation: str, code: int | None) -> str:
    return (fm_save.RIVAL_REASONS if relation == "rival" else fm_save.PERSON_REASONS).get(code or 0, "")


def club_relations(connection: Connection, club_uid: int) -> dict:
    """A club's Legends, Icons, Favoured personnel, disliked people and rival clubs, as the game
    shows them, from the newest save; highest level first."""
    groups: dict[str, list[dict]] = {"Legend": [], "Icon": [], "Favoured": [], "Disliked": [], "Rival": []}
    rows = _rows(connection, "select r.*, coalesce(p.name, t.name) name from club_relations r "
                             "left join people p on r.relation != 'rival' and p.fm_uid = r.subject_uid "
                             "left join teams t on r.relation = 'rival' and t.fm_uid = r.subject_uid "
                             "where r.club_uid = :c order by r.level desc, name", c=club_uid)
    for row in rows:
        if row["name"]:
            row["reason_name"] = _reason(row["relation"], row["reason"])
            groups[relation_group(row["relation"], row["level"], row["permanent"])].append(row)
    return groups


def rivalry(connection: Connection, club_uid: int, other_uid: int) -> dict | None:
    """How a club ranks another as a rival: the level (0-100) and the reason, or None."""
    row = _one(connection, "select level, reason from club_relations where club_uid = :c and relation = 'rival' and subject_uid = :o",
               c=club_uid, o=other_uid)
    return {"level": row["level"], "reason": _reason("rival", row["reason"])} if row else None


def person_club_relations(connection: Connection, uid: int) -> list[dict]:
    """The clubs that count a person as a Legend, an Icon or Favoured, or dislike them; the
    strongest feelings first."""
    rows = _rows(connection, "select r.*, t.name club_name from club_relations r join teams t on t.fm_uid = r.club_uid "
                             "where r.subject_uid = :u and r.relation != 'rival' order by r.relation = 'disliked', r.level desc", u=uid)
    for row in rows:
        row["group"] = relation_group(row["relation"], row["level"], row["permanent"])
        row["reason_name"] = _reason(row["relation"], row["reason"])
    return rows


def club_money(connection: Connection, club_uid: int) -> dict | None:
    """A club's finances: the newest save's balance, budgets, income streams and debts, and the balance and
    budgets at every save the archive has (pounds)."""
    history = _rows(connection, "select * from club_finances where club_uid = :c order by game_date", c=club_uid)
    if not history:
        return None
    incomes = _rows(connection, "select * from club_incomes where club_uid = :c order by yearly desc", c=club_uid)
    debts = _rows(connection, "select * from club_debts where club_uid = :c order by amount desc", c=club_uid)
    for i in incomes:
        i["kind_name"] = fm_save.INCOME_TYPES.get(i["kind"], f"Income type {i['kind']}")
    for d in debts:
        d["source_name"] = fm_save.DEBT_SOURCES.get(d["source"], "")
    return {"now": history[-1], "history": history, "incomes": incomes, "debts": debts,
            "yearly_income": sum(i["yearly"] for i in incomes), "debt_total": sum(d["amount"] for d in debts),
            "monthly_repayments": sum(d["monthly_repayment"] + d["monthly_interest"] for d in debts)}


# The game's staff jobs (fm_save.JOB_NAMES) in the departments a club page groups them in, most senior first.
STAFF_DEPARTMENTS = (
    ("Management", ("Manager", "Assistant Manager", "Director of Football", "Technical Director", "Loan Manager")),
    ("Coaching", ("Coach", "Goalkeeping Coach", "Fitness Coach", "Set Piece Coach")),
    ("Youth", ("Head of Youth Development",)),
    ("Medical and sports science", ("Chief Doctor", "Head Physio", "Physio", "Head of Sports Science", "Sports Scientist")),
    ("Scouting and analysis", ("Chief Scout", "Scout", "Head Performance Analyst", "Performance Analyst", "Recruitment Analyst")),
)


def club_staff(connection: Connection, club_uid: int) -> list[dict]:
    """The club's staff now, by department ({"department", "people"}), with each person's age, nationality, team
    (when it is not the first team) and the seasons they played for the club, if they did."""
    people = _rows(connection, "select p.fm_uid, p.name, p.staff_job, p.staff_since, p.staff_wage, p.staff_contract_ends, p.birth_date, p.staff_team_uid, "
                               "t.name team_name, t.age_limit, n.adjective nationality from people p "
                               "join teams t on t.fm_uid = p.staff_team_uid left join nations n on n.fm_uid = p.nation_uid "
                               "where t.club_uid = :c and p.staff_job is not null order by p.staff_since", c=club_uid)
    if not people:
        return []
    marks = ",".join(str(int(p["fm_uid"])) for p in people)
    played = {r["person_uid"]: r for r in _rows(
        connection, "select ps.person_uid, min(ps.season_start) first, max(ps.season_start) last, sum(ps.starts + ps.substitute_appearances) games "
                    f"from player_seasons ps join teams t on t.fm_uid = ps.team_uid where t.club_uid = :c and ps.kind = 1 and ps.person_uid in ({marks}) "
                    "group by 1", c=club_uid)}
    today = game_date(connection)
    for p in people:
        p["age"] = age_on(p["birth_date"], today)
        p["played"] = played.get(p["fm_uid"])
    found = []
    for department, jobs in STAFF_DEPARTMENTS:
        members = sorted((p for p in people if p["staff_job"] in jobs), key=lambda p: (jobs.index(p["staff_job"]), str(p["staff_since"] or "")))
        if members:
            found.append({"department": department, "people": members})
    known = {job for _, jobs in STAFF_DEPARTMENTS for job in jobs}
    others = [p for p in people if p["staff_job"] not in known]
    if others:
        found.append({"department": "Other staff", "people": others})
    return found


def _spell_rows(rows: list[dict]) -> list[dict]:
    for r in rows:
        games = r["won"] + r["drawn"] + r["lost"]
        r["games"], r["win_share"] = games, round(100 * r["won"] / games) if games else None
        r["reason_name"] = fm_save.MANAGER_LEAVING.get(r["reason"], "") if r.get("reason") is not None else ""
        r["caretaker"] = r.get("reason") == fm_save.CARETAKER_LEFT
        started, ended = (date.fromisoformat(str(r[k])) if r.get(k) else None for k in ("started", "ended"))
        r["days"] = (ended - started).days if started and ended else None
    return rows


def club_managers(connection: Connection, team_uid: int) -> list[dict]:
    """The team's managers, the current one first, then every past spell the newest save keeps, newest first."""
    past = _spell_rows(_rows(connection, "select s.*, p.name from manager_spells s left join people p on p.fm_uid = s.person_uid "
                                         "where s.team_uid = :t order by s.seq desc", t=team_uid))
    # The current manager's record is not in the save's history, and counting it from the results does not give
    # the game's figure (Everton's manager: 81-46-45 from the results, 53-44-45 in the game): it is left out.
    now = _one(connection, "select fm_uid person_uid, name, staff_since started from people where staff_team_uid = :t "
                           "and staff_job = 'Manager' order by staff_since desc limit 1", t=team_uid)
    if now:
        known = now["started"] and str(now["started"]) > "1901"
        now.update(won=0, drawn=0, lost=0, ended=None, reason=None, current=True, started=now["started"] if known else None)
        _spell_rows([now])
        now["games"] = now["win_share"] = None
    return ([now] if now else []) + past


def manager_profile(connection: Connection, uid: int) -> dict | None:
    """A manager's coaching profile from the save: coaching attributes (1-20), the coaching style (his highest coaching
    attribute, as the game shows it), his preferred formations and his tactical style where the game's names for them
    are known (fm_save.FORMATION_NAMES, TACTICAL_STYLES); the unnamed are left out."""
    row = _one(connection, "select * from staff_profiles where person_uid = :u", u=uid)
    if row is None:
        return None
    coaching = {name: row[name] for name in fm_save.STAFF_COACHING}
    names = fm_save.FORMATION_NAMES
    others = [names[n] for n in dict.fromkeys((row["formation_other"], row["formation_other_2"])) if n in names]
    found = {"coaching": coaching, "working_with_youngsters": row["working_with_youngsters"],
             "coaching_style": max(fm_save.COACHING_STYLES, key=lambda name: coaching[name]).capitalize(),
             "preferred_formation": names.get(row["formation"]), "other_formations": [f for f in others if f != names.get(row["formation"])],
             "tactical_style": fm_save.TACTICAL_STYLES.get(row["tactical_style"]) if row["tactical_style"] is not None else None}
    return found


def person_manager_spells(connection: Connection, uid: int) -> list[dict]:
    """A manager's spells at the teams whose history the newest save keeps, newest first."""
    return _spell_rows(_rows(connection, "select s.*, t.name team_name from manager_spells s join teams t on t.fm_uid = s.team_uid "
                                         "where s.person_uid = :u order by s.started desc", u=uid))


def person_injuries(connection: Connection, uid: int) -> list[dict]:
    """A player's injuries, the current ones first, then newest first, each with its expected or actual return."""
    rows = _rows(connection, "select i.*, t.name team_name from injuries i left join teams t on t.fm_uid = i.team_uid "
                             "where i.person_uid = :u order by i.current desc, i.injured_on desc", u=uid)
    for row in rows:
        row["back_on"] = _back_on(row)
    return rows


def _back_on(row: dict) -> date | None:
    """The day a player is (or was) back; the injury's date becomes a date too."""
    day = row.get("injured_on")
    if day is None:
        return None
    row["injured_on"] = date.fromisoformat(str(day)[:10])
    return row["injured_on"] + timedelta(days=row["days"])


def current_injuries(connection: Connection, uids) -> dict[int, dict]:
    """The longest current injury of each of the given players."""
    marks = ",".join(str(int(uid)) for uid in uids) or "0"
    found: dict[int, dict] = {}
    for row in _rows(connection, f"select * from injuries where current = 1 and person_uid in ({marks}) order by days"):
        row["back_on"] = _back_on(row)
        found[row["person_uid"]] = row
    return found


# -- competitions and editions ------------------------------------------------------------------------
def league_window(connection: Connection, uid: int, season: int) -> tuple[str, str]:
    """The dates of a league's season: July to June, or the calendar year for a league whose
    editions carry one year (Major League Soccer: season 2062 is 2062)."""
    label = _one(connection, "select season_label from competition_editions where competition_uid = :c order by year desc limit 1", c=uid)
    labels = [label["season_label"]] if label else [e["season_label"] for e in fixture_seasons(connection, uid)[:3]]
    if labels and all("/" not in x for x in labels):
        return f"{season}-01-01", f"{season}-12-31"
    return season_window(season)


ROUND_DAYS = 6  # the latest round: the games of the last week before the newest result
NEXT_FIXTURES = 12


def competition_now(connection: Connection, uid: int) -> dict:
    """The competition as it stands: the current (or just finished) season's table for a league, the
    latest round's results and the next fixtures."""
    latest = (_one(connection, "select max(date) d from fixtures where competition_uid = :c and home_goals is not null", c=uid) or {}).get("d")
    upcoming_from = (_one(connection, "select min(date) d from fixtures where competition_uid = :c and home_goals is null", c=uid) or {}).get("d")
    is_league = uid in league_uids(connection)
    now = {"is_league": is_league, "table": [], "recent": [], "upcoming": [], "season": None, "finished": False,
           "next_season_starts": None, "next_season": None}
    if latest:
        day = date.fromisoformat(str(latest))
        start, end = league_window(connection, uid, day.year if day.month >= 7 else day.year - 1)
        if start.endswith("-01-01"):
            start, end = league_window(connection, uid, day.year)
        now["season"] = start[:4] if start.endswith("-01-01") else season_label(int(start[:4]))
        now["window"] = (start, end)
        if is_league:
            now["table"] = league_table(connection, uid, start, end)
        now["recent"] = _rows(connection, "select * from fixtures where competition_uid = :c and home_goals is not null "
                                          "and date between date(:d, :back) and :d order by date desc, id",
                              c=uid, d=str(latest), back=f"-{ROUND_DAYS} days")
        if upcoming_from and str(upcoming_from) > end:
            now["finished"], now["next_season_starts"] = True, upcoming_from
            first = int(start[:4]) + 1
            now["next_season"] = str(first) if start.endswith("-01-01") else season_label(first)
        elif not upcoming_from:
            now["finished"] = True
    if upcoming_from:
        now["upcoming"] = _rows(connection, "select * from fixtures where competition_uid = :c and home_goals is null and date >= :d "
                                            "order by date, id limit :n", c=uid, d=str(upcoming_from), n=NEXT_FIXTURES)
    now["groups"], now["edition"], now["knockout"] = [], None, False
    if latest and not is_league:  # a cup or tournament with groups: their tables, as they stand
        current = _current_edition(connection, uid, str(latest))
        if current and current["stages"]["groups"]:
            now["groups"], now["knockout"] = current["stages"]["groups"], bool(current["stages"]["bracket"])
            now["edition"] = {"year": current["edition"]["year"], "label": current["edition"].get("season_label")}
    return now


def _current_edition(connection: Connection, uid: int, latest: str) -> dict | None:
    """The edition whose games include the competition's latest played one."""
    years = [r["year"] for r in _rows(connection, "select year from competition_editions where competition_uid = :c order by year desc", c=uid)]
    years += [e["year"] for e in fixture_seasons(connection, uid) if e["year"] not in years]
    for year in sorted(set(years), reverse=True)[:3]:
        found = edition(connection, uid, year)
        if found and any(str(f["date"]) == latest for f in found["played"]):
            return found
    return None


def fixture_seasons(connection: Connection, uid: int) -> list[dict]:
    """Editions made from the games of a competition that keeps no history of its own (or not yet of its running
    edition): the games split where they stop for more than 90 days, and after each day an edition was decided.
    The Copa Libertadores stops longer inside a season (75 days) than between two (59). Newest first; no winner."""
    dates = [date.fromisoformat(str(r[0])) for r in connection.execute(text(
        "select distinct date from fixtures where competition_uid = :c and home_goals is not null order by date"), {"c": uid})]
    decided = sorted(date.fromisoformat(str(r[0])) for r in connection.execute(text(
        "select decided_on from competition_editions where competition_uid = :c and decided_on > '1900-12-31'"), {"c": uid}))
    spans: dict[int, list[date]] = {}
    start = previous = None
    for day in dates:
        final_between = previous and bisect_left(decided, previous) < bisect_left(decided, day)  # a decided day in [previous, day)
        if previous and ((day - previous).days > 90 or final_between):
            spans[previous.year] = [spans.get(previous.year, [start])[0], previous]
            start = day
        start = start or day
        previous = day
    if previous:
        spans[previous.year] = [spans.get(previous.year, [start])[0], previous]
    return [{"competition_uid": uid, "year": year, "decided_on": None, "start": str(first), "end": str(last), "places": {},
             "season_label": str(year) if first.year == year else season_label(first.year)}
            for year, (first, last) in sorted(spans.items(), reverse=True)]


def league_parts(connection: Connection, uid: int) -> list[dict]:
    """Competitions that belong to a league without a history of its own (Major League Soccer:
    MLS Cup, Supporters' Shield and the two conferences): every team in their games and on
    their podiums plays in the league that year. The part decided last in most years (MLS Cup)
    is the league's champion and comes first."""
    teams_by_year: dict[int, set[int]] = defaultdict(set)
    for day, home, away in connection.execute(text("select date, home_uid, away_uid from fixtures where competition_uid = :c "
                                                   "and home_goals is not null"), {"c": uid}):
        teams_by_year[int(str(day)[:4])] |= {home, away}
    if not teams_by_year:
        return []
    marks = ",".join(str(int(t)) for t in set().union(*teams_by_year.values()))
    candidates = [r[0] for r in connection.execute(text(
        f"select distinct competition_uid from edition_places where place = 1 and team_uid in ({marks}) and competition_uid != :c"), {"c": uid})]
    parts = []
    for candidate in candidates:
        editions = _rows(connection, "select e.year, e.decided_on, p.place, p.team_uid from competition_editions e "
                                     "join edition_places p using (competition_uid, year) where e.competition_uid = :c and p.place <= 3",
                         c=candidate)
        inside = [r for r in editions if r["year"] in teams_by_year]
        if len({r["year"] for r in inside}) < 2 or any(r["team_uid"] not in teams_by_year[r["year"]] for r in inside):
            continue
        games = connection.execute(text("select date, home_uid, away_uid from fixtures where competition_uid = :c"), {"c": candidate}).all()
        if any(home not in teams_by_year.get(int(str(day)[:4]), ()) or away not in teams_by_year.get(int(str(day)[:4]), ())
               for day, home, away in games):
            continue
        decided = {r["year"]: str(r["decided_on"] or "") for r in editions}
        winners = {r["year"]: r["team_uid"] for r in editions if r["place"] == 1}
        parts.append({"competition_uid": candidate, "decided": decided, "winners": winners})
    last_count = Counter()
    for year in teams_by_year:
        deciding = [p for p in parts if p["decided"].get(year)]
        if deciding:
            last_count[max(deciding, key=lambda p: p["decided"][year])["competition_uid"]] += 1
    parts.sort(key=lambda p: (-last_count[p["competition_uid"]], p["competition_uid"]))
    return parts


def competition(connection: Connection, uid: int) -> dict | None:
    return _one(connection, "select * from competitions where fm_uid = :uid", uid=uid)


def competition_editions(connection: Connection, uid: int) -> list[dict]:
    """Every edition with its first three. A competition without a history of its own gets its
    seasons from its games, and its champions from the part decided last (``league_parts``);
    such an edition has ``champion_uid``, and ``own`` is False for years before its games."""
    editions = _rows(connection, "select * from competition_editions where competition_uid = :c order by year desc", c=uid)
    source = uid
    if not editions:
        own = {e["year"]: e for e in fixture_seasons(connection, uid)}
        parts = league_parts(connection, uid)
        if not parts:
            return list(own.values())
        source = parts[0]["competition_uid"]
        champion = {e["year"]: e for e in _rows(connection, "select * from competition_editions where competition_uid = :c", c=source)}
        editions = [{"competition_uid": uid, "year": year, "season_label": (own.get(year) or champion[year])["season_label"],
                     "decided_on": champion.get(year, {}).get("decided_on"), "own": year in own, "champion_uid": source}
                    for year in sorted(set(own) | set(champion), reverse=True)]
    places: dict[int, dict[int, int]] = defaultdict(dict)
    for r in _rows(connection, "select year, place, team_uid from edition_places where competition_uid = :c and place <= 3", c=source):
        places[r["year"]][r["place"]] = r["team_uid"]
    for e in editions:
        e["places"] = places.get(e["year"], {})
    return editions


def edition_window(connection: Connection, edition: dict) -> tuple[str, str]:
    """The dates of one edition: after the previous edition was decided, up to the day this one
    was decided. Early rounds of the next edition can start before the season does."""
    if "start" in edition:  # an edition made from the games (fixture_seasons)
        return edition["start"], edition["end"]
    label, year = edition["season_label"], edition["year"]
    start, end = season_window(int(label[:4])) if "/" in label else (f"{year}-01-01", f"{year}-12-31")
    before = _one(connection, "select max(decided_on) d from competition_editions where competition_uid = :c and year < :y",
                  c=edition["competition_uid"], y=year)
    if before and before["d"]:
        earliest = date.fromisoformat(start) - timedelta(days=90)
        start = str(max(date.fromisoformat(str(before["d"])) + timedelta(days=1), earliest))
    if edition["decided_on"]:
        end = str(edition["decided_on"])
    return start, end


KNOCKOUT_NAMES = {2: "Final", 4: "Semi-finals", 8: "Quarter-finals"}


def _sides(games: list[dict]) -> set[int]:
    return {f["home_uid"] for f in games} | {f["away_uid"] for f in games}


def round_names(stages: list[list[dict]]) -> list[dict]:
    """Join the stages of an edition into named rounds.

    A stage whose games are all between pairs of an earlier round is that round's second leg
    (every pair again) or its replays (some pairs). The names count back from the final while
    each round has twice the teams of the next one (a single game just before the final is the
    third-place play-off). A stage with as many games as teams is a group (one group of more than
    eight teams is a league phase); the others keep a number.
    """
    rounds: list[dict] = []
    for games in stages:
        pairs = {frozenset((f["home_uid"], f["away_uid"])) for f in games}
        owner = next((r for r in reversed(rounds) if pairs <= r["pairs"] and "legs" not in r), None)
        if owner is not None:
            owner["legs"] = "second legs" if pairs == owner["pairs"] else "replays"
            owner["games"] = owner["games"] + games
            continue
        rounds.append({"games": games, "pairs": pairs, "first": games})
    expected, chain = 2, True
    if rounds:  # an unfinished edition counts back from its last round
        while expected < len(_sides(rounds[-1]["games"])):
            expected *= 2
    for r in reversed(rounds):
        sides, first = _sides(r["games"]), r["first"]
        knockout = len(first) * 2 == len(sides)
        if chain and expected == 4 and len(first) == 1:
            r["name"] = "Third-place play-off"
            continue
        if chain and knockout and expected - 2 <= len(sides) <= expected:  # one game can be missing
            r["name"] = KNOCKOUT_NAMES.get(expected, f"Last {expected}")
            expected *= 2
            continue
        if chain and knockout and len(sides) == expected // 2 and expected > 2:
            r["name"] = "Play-off round"
        chain = False
    groups = [r for r in rounds if "name" not in r and len(r["first"]) >= len(_sides(r["games"]))]
    for letter, r in enumerate(groups):
        if 1 < len(groups) <= 26:
            r["name"] = f"Group {chr(65 + letter)}"
        else:
            r["name"] = "League phase" if len(_sides(r["games"])) > 8 else "Group stage"
    number = 0
    for r in rounds:
        if "name" not in r:
            number += 1
            r["name"] = f"Round {number}"
        r.pop("pairs")
        r.pop("first")
    return rounds


def final_of(connection: Connection, uid: int, decided_on, winner: int) -> dict | None:
    """The winner's last game of an edition: on the day it was decided, or the day before
    (the MLS Cup final of 12 November 2062 is decided on the 13th)."""
    day = date.fromisoformat(str(decided_on))
    return _one(connection, "select * from fixtures where competition_uid = :c and date between :s and :e and :t in (home_uid, away_uid) "
                            "order by date desc limit 1", c=uid, s=str(day - timedelta(days=1)), e=str(day), t=winner)


def edition(connection: Connection, uid: int, year: int) -> dict | None:
    e = _one(connection, "select * from competition_editions where competition_uid = :c and year = :y", c=uid, y=year)
    parts, places_from = [], uid
    if e is None:
        e = next((x for x in fixture_seasons(connection, uid) if x["year"] == year), None)
        if e is None:
            return None
        parts = league_parts(connection, uid)
        for part in parts:
            part["edition"] = _one(connection, "select * from competition_editions where competition_uid = :c and year = :y",
                                   c=part["competition_uid"], y=year)
        if parts and parts[0]["edition"]:
            places_from = parts[0]["competition_uid"]
            e = {**e, "decided_on": parts[0]["edition"]["decided_on"]}
    start, end = edition_window(connection, e)
    fixtures = _rows(connection, "select * from fixtures where competition_uid = :c and date between :s and :e order by date, id",
                     c=uid, s=start, e=end)
    is_league = uid in league_uids(connection)
    stages: dict[int, list[dict]] = defaultdict(list)
    for f in fixtures:
        stages[f["stage"]].append(f)
    rounds = round_names(sorted(stages.values(), key=lambda games: games[0]["date"]))
    for r in rounds:
        if r["name"].startswith("Group") or r["name"] == "League phase":
            r["table"] = table_from(r["games"])
    places = {r["place"]: r["team_uid"] for r in _rows(connection, "select place, team_uid from edition_places where competition_uid = :c and year = :y "
                                                                    "and place <= 3", c=places_from, y=year)}
    final = None
    if e["decided_on"] and places.get(1) and (not is_league or places_from != uid):
        final = final_of(connection, places_from, e["decided_on"], places[1])
    scorers = _rows(connection,
                    "select g.scorer_uid, p.name, count(*) goals, sum(g.kind = 2) penalties, "
                    "max(case g.side when 0 then f.home_uid else f.away_uid end) team_uid "
                    "from goals g join fixtures f on f.id = g.fixture_id left join people p on p.fm_uid = g.scorer_uid "
                    "where f.competition_uid = :c and f.date between :s and :e and g.kind in (0, 2) and g.scorer_uid is not null "
                    "group by g.scorer_uid order by goals desc limit 15", c=uid, s=start, e=end)
    played = [f for f in fixtures if f["home_goals"] is not None]
    return {
        "edition": e, "fixtures": fixtures, "played": played, "is_league": is_league, "rounds": rounds, "places": places, "parts": parts,
        "final": final, "scorers": scorers, "table": league_table(connection, uid, start, end) if is_league else [],
        "goals": sum((f["home_goals"] or 0) + (f["away_goals"] or 0) for f in played),
        "stages": edition_stages(rounds),
    }


# -- an edition's knockout tree --------------------------------------------------------------------------
KNOCKOUT_ROUNDS = ("Last 64", "Last 32", "Last 16", "Quarter-finals", "Semi-finals", "Final")


def _goals_of(f: dict) -> tuple[int, int]:
    if f["home_goals_after_extra_time"] is not None:
        return f["home_goals_after_extra_time"], f["away_goals_after_extra_time"]
    return f["home_goals"] or 0, f["away_goals"] or 0


def ties(games: list[dict]) -> list[dict]:
    """A knockout round's games as ties: both teams (the first leg's home side first), the legs, the
    aggregate, penalties and the winner (None while undecided)."""
    found: dict[frozenset, dict] = {}
    for f in sorted(games, key=lambda g: (g["date"], g["id"])):
        key = frozenset((f["home_uid"], f["away_uid"]))
        tie = found.setdefault(key, {"teams": (f["home_uid"], f["away_uid"]), "legs": [], "aggregate": [0, 0],
                                     "penalties": None, "winner": None, "played": True})
        tie["legs"].append(f)
        if f["home_goals"] is None:
            tie["played"] = False
            continue
        home, away = _goals_of(f)
        first = f["home_uid"] == tie["teams"][0]
        tie["aggregate"][0] += home if first else away
        tie["aggregate"][1] += away if first else home
        if f["home_penalties"] is not None:
            tie["penalties"] = (f["home_penalties"], f["away_penalties"]) if first else (f["away_penalties"], f["home_penalties"])
    for tie in found.values():
        if not tie["played"]:
            continue
        a, b = tie["aggregate"]
        if a != b:
            tie["winner"] = tie["teams"][0] if a > b else tie["teams"][1]
        elif tie["penalties"]:
            tie["winner"] = tie["teams"][0] if tie["penalties"][0] > tie["penalties"][1] else tie["teams"][1]
    return list(found.values())


def edition_stages(rounds: list[dict]) -> dict:
    """An edition in its stages: the qualifying rounds, the groups or league phase (with where each
    team went next), and the knockout tree, its rounds ordered so each tie feeds the next one."""
    knockout = [r for r in rounds if r["name"] in KNOCKOUT_ROUNDS]
    first_knockout = rounds.index(knockout[0]) if knockout else len(rounds)
    groups = [r for r in rounds if "table" in r]
    playoff = next((r for r in rounds if r["name"] == "Play-off round"), None)
    group_end = max((rounds.index(r) for r in groups), default=-1)
    qualifying = [dict(r, ties=ties(r["games"])) for i, r in enumerate(rounds)
                  if i < (group_end if groups else first_knockout) and "table" not in r and r is not playoff]
    columns = [{"name": r["name"], "ties": ties(r["games"])} for r in knockout]
    for later, earlier in zip(reversed(columns), list(reversed(columns))[1:]):
        ordered = []
        for tie in later["ties"]:
            for team in tie["teams"]:
                feeder = next((t for t in earlier["ties"] if team in t["teams"] and t not in ordered), None)
                if feeder:
                    ordered.append(feeder)
        earlier["ties"] = ordered + [t for t in earlier["ties"] if t not in ordered]
    playoff_ties = ties(playoff["games"]) if playoff else []
    if columns and playoff_ties:  # each play-off winner joins one tie of the next round: stand beside it
        side = []
        for tie in columns[0]["ties"]:
            side.append(next((p for p in playoff_ties if p["winner"] and p["winner"] in tie["teams"]), None))
        side += [p for p in playoff_ties if p not in side]
        columns.insert(0, {"name": "Play-off round", "ties": side})
    later_teams = {t for r in knockout for f in r["games"] for t in (f["home_uid"], f["away_uid"])}
    playoff_teams = {t for f in (playoff["games"] if playoff else []) for t in (f["home_uid"], f["away_uid"])}
    for r in groups:
        for row in r["table"]:
            row["went"] = ("through" if row["team_uid"] in later_teams - playoff_teams
                           else "play-off" if row["team_uid"] in playoff_teams else "out")
    return {"qualifying": qualifying, "groups": groups, "bracket": columns}


def next_round(stages: dict, fixture: dict, drawn: bool = False) -> dict | None:
    """What the winner of a knockout tie meets next, as the tree shows it on the fixture's day: the
    opponent when that was settled by then, else the tie that will settle it. None when the fixture
    did not settle a tie of the tree; "known": False when the tree does not show the next round yet.
    A competition ``drawn`` round by round (a domestic cup) pairs no one until the round's main day
    is over: "draw_to_come" for a tie settled on or before it."""
    columns = stages["bracket"]
    for number, column in enumerate(columns):
        tie = next((t for t in column["ties"] if t and any(leg["id"] == fixture["id"] for leg in t["legs"])), None)
        if tie is None:
            continue
        if tie["legs"][-1]["id"] != fixture["id"] or not tie["winner"] or column["name"] == "Final":
            return None
        later = next((t for t in columns[number + 1]["ties"] if t and tie["winner"] in t["teams"]), None) if number + 1 < len(columns) else None
        settled = {"winner": tie["winner"]}
        if len(tie["legs"]) > 1:
            settled["aggregate"] = {"teams": tie["teams"], "goals": tuple(tie["aggregate"]), "penalties": tie["penalties"]}
        if later is None:
            return settled | {"known": False}
        opponent = next(team for team in later["teams"] if team != tie["winner"])
        feeder = next((t for t in column["ties"] if t and t is not tie and opponent in t["teams"]), None)
        result = settled | {"known": True, "round": columns[number + 1]["name"]}
        if drawn and result["round"] != "Final":  # the round's main days: replays come a week or more later
            settled = sorted(date.fromisoformat(str(t["legs"][-1]["date"])) for t in column["ties"] if t)
            if date.fromisoformat(str(fixture["date"])) <= max(day for day in settled if (day - settled[0]).days <= 3):
                return result | {"draw_to_come": True}
        if feeder and str(feeder["legs"][-1]["date"]) > str(fixture["date"]):
            return result | {"winner_of": feeder["teams"]}
        first_leg = min(later["legs"], key=lambda leg: (str(leg["date"]), leg["id"]))
        return result | {"opponent": opponent, "next_game": {"home": first_leg["home_uid"], "away": first_leg["away_uid"],
                                                            "date": str(first_leg["date"])}}
    return None


# -- matches ------------------------------------------------------------------------------------------
def match(connection: Connection, fixture_id: int) -> dict | None:
    f = _one(connection, "select * from fixtures where id = :id", id=fixture_id)
    if f is None:
        return None
    goals = _rows(connection, "select g.*, p.name from goals g left join people p on p.fm_uid = g.scorer_uid "
                              "where g.fixture_id = :id order by g.seq", id=fixture_id)
    meetings = _rows(connection, "select * from fixtures where ((home_uid = :h and away_uid = :a) or (home_uid = :a and away_uid = :h)) "
                                 "and home_goals is not null and id != :id order by date desc",
                     h=f["home_uid"], a=f["away_uid"], id=fixture_id)
    record = Counter(home=0, away=0, drawn=0)
    for m in meetings:
        _, _, result = result_for(m, f["home_uid"])
        record[{"W": "home", "L": "away", "D": "drawn"}[result]] += 1
    edition_row = None
    if f["competition_uid"]:
        edition_row = _one(connection, "select * from competition_editions where competition_uid = :c and decided_on >= :d "
                                       "order by decided_on limit 1", c=f["competition_uid"], d=f["date"])
    is_final = False
    if edition_row and f["competition_uid"] not in league_uids(connection):
        winner = _one(connection, "select team_uid from edition_places where competition_uid = :c and year = :y and place = 1",
                      c=f["competition_uid"], y=edition_row["year"])
        final = final_of(connection, f["competition_uid"], edition_row["decided_on"], winner["team_uid"]) if winner else None
        is_final = bool(final and final["id"] == f["id"])
    return {"fixture": f, "goals": goals, "meetings": meetings[:12], "record": record, "edition": edition_row, "is_final": is_final,
            "game_report": match_report(connection, fixture_id)}


RATED_MINUTES = 10  # an estimate: 3 minutes on the pitch gave no rating, 19 minutes did
REPORT_TOTALS = ("shots", "shots_on_target", "xg", "clear_cut_chances", "key_passes", "crosses", "crosses_completed",
                 "tackles", "tackles_won", "interceptions", "headers", "headers_won", "fouls", "yellow_cards", "red_cards",
                 "long_shots", "half_chances", "blocked_shots", "saves_held", "saves_parried", "saves_tipped")  # the last six: null before 24 Oct 2026


def match_report(connection: Connection, fixture_id: int) -> dict | None:
    """The game's detailed report of a match, when a save brought one: both sides with their
    totals and players, the Player of the Match and the referee."""
    report = _one(connection, "select r.*, p.name as player_of_the_match from match_reports r "
                              "left join people p on p.fm_uid = r.player_of_the_match_uid where r.fixture_id = :id", id=fixture_id)
    if report is None:
        return None
    sides = {row["side"]: dict(row) for row in _rows(connection, "select * from match_teams where fixture_id = :id", id=fixture_id)}
    players = _rows(connection, "select m.*, p.name from match_players m left join people p on p.fm_uid = m.person_uid "
                                "where m.fixture_id = :id order by m.side, m.seq", id=fixture_id)
    for player in players:  # the game shows no rating for a few minutes on the pitch
        on_pitch = (player["minute_off"] or report["minutes"]) - player["minute_on"]
        player["rating_shown"] = player["rating"] if player["played"] and on_pitch >= RATED_MINUTES else None
    for number, side in sides.items():
        side["players"] = [player for player in players if player["side"] == number]
        played = [player for player in side["players"] if player["played"]]
        for name in REPORT_TOTALS:  # the team block's own figure when it has one (clear-cut chances, fouls, cards)
            if side.get(name) is None:
                side[name] = sum(player.get(name) or 0 for player in played)
        side["xg"] = round(side["xg"], 2)
        side["saves_known"] = any(player.get("saves_held") is not None for player in played)  # rows written since the figures were read
        side["saves"] = side["saves_held"] + side["saves_parried"] + side["saves_tipped"] if side["saves_known"] else None
        if not side["saves_known"]:  # older rows: no figure rather than a false zero
            for name in ("long_shots", "half_chances", "blocked_shots", "saves_held", "saves_parried", "saves_tipped"):
                side[name] = None
        if side["passes"] is None:  # no team block: the players' sums
            side["passes"] = sum(player["passes"] for player in played)
            side["passes_completed"] = sum(player["passes_completed"] for player in played)
    if len(sides) == 2:
        completed = sides[0]["passes_completed"] + sides[1]["passes_completed"]
        sides[0]["possession"] = round(100 * sides[0]["passes_completed"] / completed) if completed else None
        sides[1]["possession"] = 100 - sides[0]["possession"] if completed else None
    return {**report, "sides": [sides.get(0), sides.get(1)]}


LATE_WINNER = 85  # a winning goal from this minute on is a late winner
FAN_SELECTION_NOTES = 4  # the selection's talking points the fans get for each side
SMASH_AND_GRAB = 0.8  # a winner with this much less xG than the beaten side robbed them


def _minute_words(minute: int, added: int = 0) -> str:
    return f"{minute}+{added}'" if added else f"{minute}'"


def match_summary(connection: Connection, fixture_id: int) -> dict | None:
    """What fans argue about after a match, from the game's report: the player of the match, each side's xG, shots,
    possession, woodwork, red cards and injuries with their minute, the best and worst ratings, a late winner, a win
    against the run of play. None when the save brought no report."""
    report = match_report(connection, fixture_id)
    f = _one(connection, "select * from fixtures where id = :id", id=fixture_id)
    if report is None or f is None or None in report["sides"]:
        return None
    names = teams(connection, [f["home_uid"], f["away_uid"]])
    shots = _rows(connection, "select s.*, p.name from match_shots s left join people p on p.fm_uid = s.person_uid "
                              "where s.fixture_id = :id order by s.side, s.seq", id=fixture_id)
    summary: dict = {"player_of_the_match": report["player_of_the_match"], "sides": []}
    for number, side in enumerate(report["sides"]):
        played = [p for p in side["players"] if p["rating_shown"] and p["name"]]
        ranked = sorted(played, key=lambda p: -p["rating_shown"])
        facts = {"team": names.get(f["home_uid"] if number == 0 else f["away_uid"], {}).get("name"), "xg": side["xg"],
                 "shots": side["shots"], "on_target": side["shots_on_target"], "possession": side.get("possession"),
                 "woodwork": [f"{s['name']} {s['minute'] + 1}'" for s in shots if s["side"] == number and s["outcome"] == "woodwork"]
                 or side["woodwork"] or None,
                 "red_cards": [f"{p['name']} {_minute_words(p['sent_off_minute'])}" for p in side["players"] if p["red_cards"]] or None,
                 "injured_in_the_match": [f"{p['name']} {_minute_words(p['injury_minute'])}" for p in side["players"] if p["injury_minute"]] or None}
        if ranked:
            facts["best_rated"] = f"{ranked[0]['name']} {ranked[0]['rating_shown']:.1f}"
            facts["worst_rated"] = f"{ranked[-1]['name']} {ranked[-1]['rating_shown']:.1f}"
        summary["sides"].append({key: value for key, value in facts.items() if value is not None})
    from app.services import tactics  # it reads this module

    selection = tactics.selection_notes(connection, fixture_id)
    for side, team in zip(summary["sides"], (f["home_uid"], f["away_uid"])):
        picked = (selection or {}).get(team)
        if picked:  # what the fans argue about: the system, who played and who did not
            side["shape"] = picked["shape"]
            if picked["notes"]:
                side["talking_points"] = picked["notes"][:FAN_SELECTION_NOTES]
    home_goals = f["home_goals_after_extra_time"] if f["home_goals_after_extra_time"] is not None else f["home_goals"]
    away_goals = f["away_goals_after_extra_time"] if f["away_goals_after_extra_time"] is not None else f["away_goals"]
    if home_goals != away_goals:
        winner = 0 if home_goals > away_goals else 1
        goals = _rows(connection, "select g.*, p.name from goals g left join people p on p.fm_uid = g.scorer_uid where g.fixture_id = :id "
                                  "and g.kind in (0, 1, 2) and g.side = :w order by g.minute, g.added_time, g.seq", id=fixture_id, w=winner)
        decisive = min(home_goals, away_goals)  # the winner's goal that put them ahead for good
        if len(goals) > decisive and goals[decisive]["minute"] >= LATE_WINNER:
            g = goals[decisive]
            summary["late_winner"] = f"{g['name'] or 'an own goal'} {_minute_words(g['minute'], g['added_time'])}"
        if report["sides"][winner]["xg"] + SMASH_AND_GRAB <= report["sides"][1 - winner]["xg"]:
            summary["won_against_the_run_of_play"] = summary["sides"][winner]["team"]
    return summary


# -- home, search, nations, awards ------------------------------------------------------------------------
BIG_LEAGUES = (11, 67, 32, 22, 16)  # Premier Division, Spanish First Division, Serie A, Bundesliga, Ligue 1


def home(connection: Connection) -> dict:
    today = game_date(connection)
    winners = _rows(connection,
                    "select p.competition_uid, p.team_uid, e.season_label, e.decided_on, e.year from edition_places p "
                    "join competition_editions e on e.competition_uid = p.competition_uid and e.year = p.year "
                    "where p.place = 1 and e.decided_on >= date(:d, '-60 days') and p.competition_uid in "
                    "(select competition_uid from fixtures where home_goals is not null and date >= date(:d, '-400 days') "
                    " group by competition_uid having count(*) >= 30) order by e.decided_on desc limit 16",
                    d=str(today))
    latest = []
    for uid in BIG_LEAGUES:
        last = connection.execute(text("select max(date) from fixtures where competition_uid = :c and home_goals is not null"), {"c": uid}).scalar()
        if last:
            latest.append((uid, _rows(connection, "select * from fixtures where competition_uid = :c and home_goals is not null "
                                                  "and date between date(:d, '-3 days') and :d order by date", c=uid, d=last)[:10]))
    counts = _one(connection, "select (select count(*) from fixtures where home_goals is not null) matches, "
                              "(select count(*) from people) people, (select count(*) from clubs) clubs")
    return {"today": today, "winners": winners, "latest": latest, "counts": counts}


TODAY_LEAGUES = 6  # league tables on the Today page
TODAY_INTERNATIONAL = 6  # club and national-team competitions there
TODAY_BIG_GAMES = 5  # a league's games in its latest round, on the Today page
TODAY_WINDOW = 45  # days around the game date an international competition must have games in
TODAY_TRANSFERS = 10
TRANSFER_DAYS = 90
PAID = (1, 2, 4)  # transfers, part-exchanges and free transfers: no loans


def today_page(connection: Connection, followed: tuple[int, ...] = ()) -> dict:
    """The world at a glance, as the game's "Leagues in Focus": the big leagues' tables (the followed
    clubs' leagues first), the international competitions in play, and the biggest transfers."""
    today = game_date(connection)
    if today is None:
        return {"today": None, "leagues": [], "international": [], "transfers": []}
    followed_leagues = []
    for team in followed:  # each followed club's league in its latest season
        row = _one(connection, "select league_uid from player_seasons where team_uid = :t and kind = 1 and league_uid is not null "
                               "order by season_start desc limit 1", t=team)
        if row and row["league_uid"] not in followed_leagues:
            followed_leagues.append(row["league_uid"])
    recent = {r["competition_uid"] for r in _rows(connection,
        "select distinct competition_uid from fixtures where date between date(:d, '-200 days') and date(:d, '+60 days')", d=str(today))}
    by_reputation = [r["fm_uid"] for r in _rows(connection,
        "select fm_uid from competitions where scope = 'league' and level = 1 and reputation is not null order by reputation desc")]
    leagues, seen = [], set()
    for uid in [*followed_leagues, *BIG_LEAGUES, *by_reputation]:
        if uid in seen or uid not in recent or len(leagues) >= TODAY_LEAGUES:
            continue
        seen.add(uid)
        now = competition_now(connection, uid)
        if now["table"]:
            leagues.append({"uid": uid, "now": now, "followed": uid in followed_leagues, "big_games": _big_games(now, followed)})
    international = []
    for row in _rows(connection, "select fm_uid from competitions where scope in ('continental', 'national_teams') "
                                 "and reputation is not null order by reputation desc"):
        uid = row["fm_uid"]
        active = _one(connection, "select count(*) n from fixtures where competition_uid = :c and date between date(:d, :back) and date(:d, :ahead)",
                      c=uid, d=str(today), back=f"-{TODAY_WINDOW} days", ahead=f"+{TODAY_WINDOW} days")
        if active and active["n"]:
            now = competition_now(connection, uid)
            international.append({"uid": uid, "now": now})
        if len(international) >= TODAY_INTERNATIONAL:
            break
    transfers = _rows(connection,
        f"select t.*, p.name from transfers t left join people p on p.fm_uid = t.person_uid where t.date between date(:d, :back) and :d "
        f"and t.kind in ({','.join(map(str, PAID))}) and t.fee > 0 order by t.fee desc limit :n",
        d=str(today), back=f"-{TRANSFER_DAYS} days", n=TODAY_TRANSFERS)
    return {"today": today, "leagues": leagues, "international": international, "transfers": transfers}


def _big_games(now: dict, followed: tuple[int, ...] = ()) -> list[dict]:
    """While a league season runs, its latest round's games of the clubs highest in the table (the followed
    clubs' games first)."""
    if now["finished"] or not now["recent"]:
        return []
    place = {row["team_uid"]: n for n, row in enumerate(now["table"], 1)}
    last = len(place) + 1

    def weight(f: dict) -> tuple:
        return (not ({f["home_uid"], f["away_uid"]} & set(followed)),
                min(place.get(f["home_uid"], last), place.get(f["away_uid"], last)))
    return sorted(now["recent"], key=weight)[:TODAY_BIG_GAMES]


def search(connection: Connection, term: str, limit: int = 30) -> dict:
    like = f"%{term.strip()}%"
    return {
        "people": _rows(connection, "select fm_uid, name, birth_date, nation_uid from people where name like :q "
                                    "order by (name like :prefix) desc, length(name) limit :n", q=like, prefix=f"{term.strip()}%", n=limit),
        "teams": _rows(connection, "select fm_uid, name, club_uid, nation_uid, age_limit from teams where name like :q "
                                   "and (club_uid is null or fm_uid = club_uid) "  # a club's other teams play no matches
                                   "order by age_limit is not null, length(name) limit :n", q=like, n=limit),
        "competitions": [c for c in _rows(connection, "select * from competitions where name like :q or short_name like :q "
                                                      "order by length(name) limit :n", q=like, n=limit) if not is_friendly(c["name"])],
        "awards": _rows(connection, "select * from awards where name like :q order by length(name) limit :n", q=like, n=limit),
    }


def nation(connection: Connection, uid: int) -> dict | None:
    n = _one(connection, "select * from nations where fm_uid = :uid", uid=uid)
    if n is None:
        return None
    sides = _rows(connection, "select * from teams where nation_uid = :uid "
                              "order by fm_uid = :uid desc, age_limit is not null, age_limit desc", uid=uid)
    sides = list({t["name"]: t for t in reversed(sides)}.values())[::-1]  # the senior team's uid is the nation's
    senior = next((t for t in sides if t["fm_uid"] == uid), None)
    results, titles, scorers = [], [], []
    if senior:
        results = _rows(connection, "select * from fixtures where (home_uid = :t or away_uid = :t) and home_goals is not null "
                                    "order by date desc limit 20", t=senior["fm_uid"])
        titles = club_honours(connection, senior["fm_uid"])
        scorers = _rows(connection, "select g.scorer_uid, p.name, count(*) goals from goals g join fixtures f on f.id = g.fixture_id "
                                    "left join people p on p.fm_uid = g.scorer_uid where g.kind in (0, 2) and g.scorer_uid is not null "
                                    "and ((g.side = 0 and f.home_uid = :t) or (g.side = 1 and f.away_uid = :t)) "
                                    "group by 1 order by goals desc limit 15", t=senior["fm_uid"])
    players = connection.execute(text("select count(*) from people where nation_uid = :uid"), {"uid": uid}).scalar()
    return {"nation": n, "sides": sides, "senior": senior, "results": results, "titles": titles, "scorers": scorers, "players": players}


def award(connection: Connection, uid: int) -> dict | None:
    a = _one(connection, "select * from awards where fm_uid = :uid", uid=uid)
    if a is None:
        return None
    years: dict[int, dict[int, dict]] = defaultdict(dict)
    for r in _rows(connection, "select ap.*, p.name from award_places ap left join people p on p.fm_uid = ap.person_uid "
                               "where ap.award_uid = :uid order by ap.year desc, ap.place", uid=uid):
        years[r["year"]][r["place"]] = r
    winners = Counter(row[1]["person_uid"] for row in years.values() if 1 in row and row[1]["person_uid"])
    names = {row[1]["person_uid"]: row[1]["name"] for row in years.values() if 1 in row}
    return {"award": a, "years": sorted(years.items(), reverse=True),
            "most": [(names[uid], uid, count) for uid, count in winners.most_common(10) if count > 1]}


FRIENDLY_NAMES = re.compile(r"Friendly|Testimonial|Under \d+ International")


def is_friendly(name: str) -> bool:
    """Friendlies and testimonials are not tournaments: they have no winner."""
    return bool(FRIENDLY_NAMES.fullmatch(name))


BIG_LEAGUE_COUNT = 8  # top divisions shown with all their clubs on the clubs page
BIGGEST_CLUBS = 48
LEAGUE_SEASON_DAYS = 330  # a league's current clubs: the teams in its fixtures this long before its newest one


def league_clubs(connection: Connection, competition_uid: int) -> list[dict]:
    """The clubs of a league's latest season (from its newest fixtures, played or planned), biggest first."""
    return _rows(connection,
                 "select t.fm_uid, t.name, t.club_uid, t.nation_uid, t.reputation from teams t where t.fm_uid in ("
                 " select home_uid from fixtures where competition_uid = :c and date >= "
                 "  date((select max(date) from fixtures where competition_uid = :c), :back)"
                 " union select away_uid from fixtures where competition_uid = :c and date >= "
                 "  date((select max(date) from fixtures where competition_uid = :c), :back)) "
                 "order by t.reputation desc, t.name", c=competition_uid, back=f"-{LEAGUE_SEASON_DAYS} days")


def clubs_index(connection: Connection, followed: tuple[int, ...]) -> dict:
    """The clubs page: the followed clubs, the world's biggest clubs and the big leagues' clubs; the other
    top divisions only by name."""
    leagues = _rows(connection, "select c.*, n.name as nation from competitions c left join nations n on n.fm_uid = c.nation_uid "
                                "where c.scope = 'league' and c.level = 1 and c.reputation is not null order by c.reputation desc, c.name")
    big = [dict(league, clubs=league_clubs(connection, league["fm_uid"])) for league in leagues[:BIG_LEAGUE_COUNT]]
    biggest = _rows(connection, "select fm_uid, name, club_uid, nation_uid, reputation from teams where club_uid = fm_uid "
                                "and age_limit is null and reputation is not null order by reputation desc limit :n", n=BIGGEST_CLUBS)
    mine = _rows(connection, f"select fm_uid, name, club_uid, nation_uid, reputation from teams where fm_uid in "
                             f"({','.join(str(int(u)) for u in followed) or '0'}) order by reputation desc")
    return {"followed": mine, "biggest": biggest, "big_leagues": big, "other_leagues": leagues[BIG_LEAGUE_COUNT:]}


def competition_index(connection: Connection) -> list[dict]:
    """Tournaments with played matches since 2022, the busiest first."""
    rows = _rows(connection, "select c.*, count(f.id) matches, max(f.date) last_played from fixtures f join competitions c on c.fm_uid = f.competition_uid "
                             "where f.home_goals is not null group by c.fm_uid order by matches desc")
    return [r for r in rows if not is_friendly(r["name"])]


CONTINENTS = ("World", "Europe", "South America", "North & Central America", "Asia", "Africa", "Oceania", "Other")
WORLD_COMPETITIONS = ("World Cup", "Club World Championship", "Olympic Games", "World Cup Playoff", "Under 20 World Cup")
SHOWN_YOUTH = ("Under 20 World Cup",)  # the only youth competition on the competitions page
_CONTINENT_WORDS = (("Europe", ("UEFA", "European")),
                    ("South America", ("South American", "Copa Libertadores", "Copa Sudamericana", "Copa América")),
                    ("North & Central America", ("North American", "Caribbean", "Central American", "Gold Cup")),
                    ("Asia", ("Asian",)), ("Africa", ("African",)), ("Oceania", ("Oceania", "Pacific")))


def _continent_by_name(name: str) -> str | None:
    if name in WORLD_COMPETITIONS:
        return "World"
    return next((continent for continent, words in _CONTINENT_WORDS if any(word in name for word in words)), None)


def nation_continents(connection: Connection) -> dict[int, str]:
    """Each nation's confederation, from the international competitions its clubs and national teams play in."""
    international = {r["fm_uid"]: _continent_by_name(r["name"]) for r in _rows(
        connection, "select fm_uid, name from competitions where scope in ('continental', 'national_teams')")}
    international = {uid: continent for uid, continent in international.items() if continent not in (None, "World")}
    if not international:
        return {}
    marks = ",".join(str(uid) for uid in international)
    votes: dict[int, Counter] = defaultdict(Counter)
    for r in _rows(connection, f"select f.competition_uid, coalesce(c.nation_uid, t.nation_uid) nation, count(*) n from fixtures f "
                               f"join teams t on t.fm_uid in (f.home_uid, f.away_uid) left join clubs c on c.fm_uid = t.club_uid "
                               f"where f.competition_uid in ({marks}) group by 1, 2"):
        if r["nation"]:
            votes[r["nation"]][international[r["competition_uid"]]] += r["n"]
    return {nation: count.most_common(1)[0][0] for nation, count in votes.items()}


_continents_cache: dict = {}


def cached_nation_continents(connection: Connection) -> dict[int, str]:
    """nation_continents, kept for five minutes (every page's crumbs use it)."""
    found = _continents_cache.get("map")
    if found is None or time.monotonic() - found[0] > 300:
        found = (time.monotonic(), nation_continents(connection))
        _continents_cache["map"] = found
    return found[1]


def continent_anchor(continent: str) -> str:
    """The id of a continent's tab on the competitions page."""
    return continent.lower().replace(" & ", "-").replace(" ", "-")


def trail(connection: Connection, kind: str, uid: int, league_uid: int | None = None) -> list[tuple[str, str | None]]:
    """The crumbs of a page, as (label, path): continent › country › league › club › player.
    ``kind`` is competition, team, person or nation; a team's league is its latest one unless given."""
    from flask import url_for

    def competition_part(comp_uid: int | None) -> list[tuple[str, str | None]]:
        comp = _one(connection, "select fm_uid, name, nation_uid from competitions where fm_uid = :c", c=comp_uid) if comp_uid else None
        if comp is None:
            return [("Competitions", url_for("world.competitions"))]
        continent = _continent_by_name(comp["name"])
        crumbs = nation_part(comp["nation_uid"]) if not continent and comp["nation_uid"] else \
            [("Competitions", url_for("world.competitions")),
             (continent or "Other", url_for("world.competitions", _anchor=continent_anchor(continent or "Other")))]
        return crumbs + [(comp["name"], url_for("world.competition", uid=comp["fm_uid"]))]

    def nation_part(nation_uid: int | None) -> list[tuple[str, str | None]]:
        nation = _one(connection, "select fm_uid, name from nations where fm_uid = :n", n=nation_uid) if nation_uid else None
        continent = cached_nation_continents(connection).get(nation_uid, "Other")
        crumbs = [("Competitions", url_for("world.competitions")),
                  (continent, url_for("world.competitions", _anchor=continent_anchor(continent)))]
        return crumbs + ([(nation["name"], url_for("world.nation", uid=nation["fm_uid"]))] if nation else [])

    def team_part(team_uid: int | None, league: int | None) -> list[tuple[str, str | None]]:
        team = teams(connection, [team_uid]).get(team_uid) if team_uid else None
        if team is None:
            return []
        if team["nation_uid"] and not team["club_uid"]:  # a national team: its country's page
            return nation_part(team["nation_uid"])
        league = league or (_one(connection, "select league_uid from player_seasons where team_uid = :t and kind = 1 "
                                             "and league_uid is not null order by season_start desc limit 1", t=team_uid) or {}).get("league_uid")
        return competition_part(league) + [(team["name"], url_for("world.club", uid=team_uid))]

    if kind == "competition":
        return competition_part(uid)
    if kind == "nation":
        return nation_part(uid)
    if kind == "team":
        return team_part(uid, league_uid)
    if kind == "person":
        person = _one(connection, "select team_uid from people where fm_uid = :p", p=uid) or {}
        team_uid = person.get("team_uid") or (_one(connection, "select team_uid from player_seasons where person_uid = :p and kind = 1 "
                                                               "and team_uid != 0 order by season_start desc limit 1", p=uid) or {}).get("team_uid")
        return team_part(team_uid, None)
    return []


def competition_groups(connection: Connection) -> list[dict]:
    """The competitions page: per continent its international competitions, then its countries, each with
    its leagues (top division first) and cups. Youth competitions are left out but for the Under 20 World Cup."""
    rows = [r for r in competition_index(connection) if r["scope"] != "youth" or r["name"] in SHOWN_YOUTH]
    continents = cached_nation_continents(connection)
    nations = {r["fm_uid"]: r["name"] for r in _rows(connection, "select fm_uid, name from nations")}
    groups: dict[str, dict] = {name: {"continent": name, "international": [], "countries": {}} for name in CONTINENTS}
    for r in rows:
        continent = _continent_by_name(r["name"])  # the name first: "UEFA Super Cup" is European, wherever it is played
        if continent:
            groups[continent]["international"].append(r)
            continue
        nation = r["nation_uid"]
        if not nation:  # "French Cup", "Trofeo de Campeones": the country most of its teams come from
            home = _one(connection, "select coalesce(c.nation_uid, t.nation_uid) nation, count(*) n from fixtures f join teams t "
                                    "on t.fm_uid in (f.home_uid, f.away_uid) left join clubs c on c.fm_uid = t.club_uid "
                                    "where f.competition_uid = :c group by 1 order by n desc limit 1", c=r["fm_uid"])
            nation = home["nation"] if home else None
        continent = continents.get(nation, "Other") if nation else "Other"
        country = groups[continent]["countries"].setdefault(nation, {"nation_uid": nation, "name": nations.get(nation, "Other"),
                                                                      "leagues": [], "cups": []})
        (country["leagues"] if r["scope"] == "league" else country["cups"]).append(r)
    result = []
    for name in CONTINENTS:
        group = groups[name]
        group["international"].sort(key=lambda r: -(r["reputation"] or 0))
        countries = list(group["countries"].values())
        for country in countries:
            country["leagues"].sort(key=lambda r: (r["level"] or 99, -(r["reputation"] or 0), r["name"]))
            country["cups"].sort(key=lambda r: (-(r["reputation"] or 0), r["name"]))
            country["top"] = max((r["reputation"] or 0 for r in country["leagues"] + country["cups"]), default=0)
        group["countries"] = sorted(countries, key=lambda c: (-c["top"], c["name"]))
        if group["international"] or group["countries"]:
            result.append(group)
    return result


def nation_index(connection: Connection) -> list[dict]:
    return _rows(connection, "select n.*, (select count(*) from people p where p.nation_uid = n.fm_uid) players from nations n order by n.name")


def award_index(connection: Connection) -> list[dict]:
    return _rows(connection, "select a.*, count(distinct ap.year) years, max(ap.year) last_year from awards a join award_places ap on ap.award_uid = a.fm_uid "
                             "group by a.fm_uid order by a.name")


# -- the transfer market ---------------------------------------------------------------------------------------------------
def transfer_market(connection: Connection, uid: int) -> dict:
    """The clubs after a player as of the newest save (active, or only rumoured) and the bids made for him, newest first."""
    interest = _rows(connection, "select i.*, t.name team from transfer_interest i join teams t on t.fm_uid = i.team_uid "
                                 "where i.person_uid = :p order by i.rumoured, t.reputation desc, t.name", p=uid)
    bids = _rows(connection, "select o.*, t.name team, s.name squad from transfer_offers o join teams t on t.fm_uid = o.team_uid "
                             "left join teams s on s.fm_uid = o.squad_uid where o.person_uid = :p order by o.offered_on desc, o.fee desc", p=uid)
    for b in bids:  # the offer as the game states it (the fee and its guaranteed clauses), and what add-ons may make it
        b["fee"] = b.get("fee_guaranteed") or b["fee"]
        b["rising_to"] = b["fee_max"] if b.get("fee_max") and b["fee_max"] > b["fee"] else None
    return {"active": [r for r in interest if not r["rumoured"]], "rumoured": [r for r in interest if r["rumoured"]], "bids": bids}


def wanted_players(connection: Connection, team_uids: list[int] | None = None, limit: int = 40) -> list[dict]:
    """Players with clubs after them, the most wanted and best known first: each with his clubs."""
    teams = ",".join(str(int(u)) for u in team_uids or []) or None
    rows = _rows(connection, "select i.person_uid, p.name, p.team_uid, p.reputation_world, i.team_uid suitor_uid, t.name suitor, t.reputation suitor_reputation, "
                             "coalesce(t.nation_uid, c.nation_uid) suitor_nation, (select count(*) from transfer_interest x where x.team_uid = i.team_uid) suitor_list, "
                             "i.rumoured, i.offered_wage, i.wage from transfer_interest i join people p on p.fm_uid = i.person_uid "
                             "join teams t on t.fm_uid = i.team_uid left join clubs c on c.fm_uid = t.club_uid" + (f" where p.team_uid in ({teams})" if teams else "") +
                             " order by p.reputation_world desc, t.reputation desc")
    found: dict[int, dict] = {}
    for r in rows:
        row = found.setdefault(r["person_uid"], {"person_uid": r["person_uid"], "name": r["name"], "team_uid": r["team_uid"],
                                                  "reputation_world": r["reputation_world"], "suitors": []})
        row["suitors"].append({"team_uid": r["suitor_uid"], "name": r["suitor"], "reputation": r["suitor_reputation"], "rumoured": bool(r["rumoured"]),
                               "nation_uid": r["suitor_nation"], "list_size": r["suitor_list"], "offered_wage": r["offered_wage"], "wage": r["wage"]})
    return sorted(found.values(), key=lambda r: (-(r["reputation_world"] or 0), -len(r["suitors"])))[:limit]


def player_games(connection: Connection, uid: int, limit: int = 20) -> list[dict]:
    """A tracked player's recent games (the game's "Recent games for <team>" rows), by team, newest first within a team:
    each row with the opponent, the score when the fixture is known, and his figures. Teams in the order of their newest
    game."""
    rows = _rows(connection, "select g.*, t.name team, t.nation_uid team_nation_uid, o.name opponent, f.home_uid, f.home_goals, f.away_goals, "
                             "f.home_goals_after_extra_time, f.away_goals_after_extra_time, c.name competition "
                             "from player_games g join teams t on t.fm_uid = g.team_uid left join teams o on o.fm_uid = g.opponent_uid "
                             "left join fixtures f on f.id = g.fixture_id left join competitions c on c.fm_uid = g.competition_uid "
                             "where g.person_uid = :p order by g.played_on desc", p=uid)
    teams: dict[int, dict] = {}
    for r in rows:
        if r["home_goals"] is not None:
            home, away = (r["home_goals_after_extra_time"] if r["home_goals_after_extra_time"] is not None else r["home_goals"],
                          r["away_goals_after_extra_time"] if r["away_goals_after_extra_time"] is not None else r["away_goals"])
            ours, theirs = (home, away) if r["home_uid"] == r["team_uid"] else (away, home)
            r["score"], r["result"] = f"{ours}-{theirs}", "W" if ours > theirs else "D" if ours == theirs else "L"
            r["at_home"] = r["home_uid"] == r["team_uid"]
        else:
            r["score"] = r["result"] = None
            r["at_home"] = None
        r["status_word"] = {"played": "played", "unused": "unused substitute"}.get(r["status"], r["status"] if r["status"] in ("injured", "suspended") else "did not play")
        group = teams.setdefault(r["team_uid"], {"team_uid": r["team_uid"], "team": r["team"], "national": r["team_nation_uid"] is not None, "games": []})
        if len(group["games"]) < limit:
            group["games"].append(r)
    return list(teams.values())


def fixture_player_games(connection: Connection, fixture_id: int) -> list[list[dict]]:
    """The tracked players' own rows of a fixture (player_games), by side: what the archive knows of a game whose full
    report is gone. Each row has the player's name; those who played first, the best rated first."""
    f = _one(connection, "select home_uid, away_uid from fixtures where id = :f", f=fixture_id)
    if f is None:
        return [[], []]
    rows = _rows(connection, "select g.*, p.name from player_games g join people p on p.fm_uid = g.person_uid where g.fixture_id = :f "
                             "order by g.status != 'played', g.rating desc, p.name", f=fixture_id)
    for r in rows:
        r["status_word"] = {"played": "played", "unused": "unused substitute"}.get(r["status"], r["status"] if r["status"] in ("injured", "suspended") else "did not play")
    return [[r for r in rows if r["team_uid"] == f["home_uid"]], [r for r in rows if r["team_uid"] == f["away_uid"]]]


def wage_words(wage: int | None) -> str | None:
    """A weekly wage as the game shows it: rounded to the nearest £1,000 below £100,000, £10,000 below a million, £100,000
    above (237,663 shows as £240,000; 1,444,770 as £1,400,000; 7,087 as £7,000)."""
    if not wage:
        return None
    step = 1_000 if wage < 100_000 else 10_000 if wage < 1_000_000 else 100_000
    return f"£{int(round(wage / step) * step):,} a week"


def person_contracts(connection: Connection, uid: int) -> list[dict]:
    """A person's deals as the saves had them, newest first, each with the club's name and the wage in the game's words."""
    rows = _rows(connection, "select c.*, t.name team from contracts c join teams t on t.fm_uid = c.team_uid where c.person_uid = :p "
                             "order by c.as_of desc, coalesce(c.signed, c.first_seen) desc", p=uid)
    for r in rows:
        r["wage_words"] = wage_words(r["wage"])
    return rows


def current_contract(connection: Connection, uid: int) -> dict | None:
    """The person's deal at his current club from the newest save, if the archive has one."""
    person = _one(connection, "select team_uid, staff_team_uid from people where fm_uid = :p", p=uid)
    if person is None:
        return None
    for team in (person["team_uid"], person["staff_team_uid"]):
        if team:
            found = [r for r in person_contracts(connection, uid) if r["team_uid"] == team]
            if found:
                return found[0]
    return None
