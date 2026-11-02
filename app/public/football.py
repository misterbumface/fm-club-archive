"""The football data of World Sport: scores, fixtures, tables and club pages, from the world database (read only)."""

from __future__ import annotations

from datetime import date, timedelta

from flask import url_for
from sqlalchemy.engine import Connection

from app.extensions import db
from app.models import world as world_models
from app.services import world_queries as q

LEAGUES = 8  # the strongest top divisions on the scores and tables pages, after the followed clubs' leagues
CLUB_RESULTS, CLUB_FIXTURES = 8, 5


def connect() -> Connection:
    return db.engines[world_models.BIND].connect()


def followed() -> tuple[int, ...]:
    from app.services.story_leads import followed_clubs

    return followed_clubs()


def league_of(c: Connection, team: int) -> int | None:
    """The league a club played in last."""
    row = q._one(c, "select f.competition_uid from fixtures f join competitions c on c.fm_uid = f.competition_uid "
                    "where c.scope = 'league' and (f.home_uid = :t or f.away_uid = :t) and f.home_goals is not null "
                    "order by f.date desc limit 1", t=team)
    return row["competition_uid"] if row else None


def main_leagues(c: Connection) -> list[int]:
    """The followed clubs' leagues, then the strongest top divisions with games this season."""
    leagues = [league for team in followed() if (league := league_of(c, team))]
    today = today_of(c)
    since = str(date(today.year - 1, 7, 1)) if today else "1900-01-01"
    for r in q._rows(c, "select c.fm_uid from competitions c where c.scope = 'league' and c.level = 1 and c.reputation is not null "
                        "and exists (select 1 from fixtures f where f.competition_uid = c.fm_uid and f.date >= :s and f.home_goals is not null) "
                        "order by c.reputation desc limit :n", s=since, n=LEAGUES + len(leagues)):
        if r["fm_uid"] not in leagues:
            leagues.append(r["fm_uid"])
    return leagues[:LEAGUES + 2]


def crest(team: dict | None) -> str | None:
    from app.services.logo_assets import resolve_logo_path

    if not team:
        return None
    if team.get("nation_uid") and resolve_logo_path("nation", team["nation_uid"], "normal"):
        return url_for("public.logo", entity_type="nation", fm_uid=team["nation_uid"])
    uid = team.get("club_uid") or team.get("fm_uid")
    return url_for("public.logo", entity_type="club", fm_uid=uid) if uid and resolve_logo_path("club", uid, "normal") else None


def _games(c: Connection, rows: list[dict]) -> list[dict]:
    """Fixtures with their teams' names, crests and the score in words."""
    names = q.teams(c, {t for f in rows for t in (f["home_uid"], f["away_uid"])})
    comps = q.competitions(c, {f["competition_uid"] for f in rows})
    games = []
    for f in rows:
        home, away = names.get(f["home_uid"], {}), names.get(f["away_uid"], {})
        played = f["home_goals"] is not None
        extra = f.get("home_goals_after_extra_time") is not None
        score = (f"{f['home_goals_after_extra_time']}-{f['away_goals_after_extra_time']}" if extra else f"{f['home_goals']}-{f['away_goals']}") if played else None
        note = None
        if played and f.get("home_penalties") is not None:
            note = f"{f['home_penalties']}-{f['away_penalties']} pens"
        elif extra:
            note = "aet"
        games.append({"id": f["id"], "date": date.fromisoformat(str(f["date"])[:10]), "home": home, "away": away, "home_uid": f["home_uid"], "away_uid": f["away_uid"],
                      "home_crest": crest(home), "away_crest": crest(away), "score": score, "note": note,
                      "competition": comps.get(f["competition_uid"], {}).get("name"), "competition_uid": f["competition_uid"]})
    return games


def league_box(c: Connection, uid: int) -> dict | None:
    """A league as it stands: its table, the latest round and the next games."""
    comp = q.competition(c, uid)
    if comp is None:
        return None
    now = q.competition_now(c, uid)
    names = q.teams(c, {r["team_uid"] for r in now["table"]})
    table = [{**r, "team": names.get(r["team_uid"], {}), "crest": crest(names.get(r["team_uid"]))} for r in now["table"]]
    return {"uid": uid, "name": comp["name"], "season": now["season"], "table": table, "recent": _games(c, now["recent"]),
            "upcoming": _games(c, now["upcoming"])}


def club(c: Connection, team_uid: int) -> dict | None:
    """A club's page: its league place, its last results and next games in every competition."""
    team = q.teams(c, [team_uid]).get(team_uid)
    if team is None:
        return None
    recent = q._rows(c, "select * from fixtures where (home_uid = :t or away_uid = :t) and home_goals is not null "
                        "order by date desc limit :n", t=team_uid, n=CLUB_RESULTS)
    upcoming = q._rows(c, "select * from fixtures where (home_uid = :t or away_uid = :t) and home_goals is null "
                          "order by date limit :n", t=team_uid, n=CLUB_FIXTURES)
    league = league_of(c, team_uid)
    box = league_box(c, league) if league else None
    place = next((r for r in box["table"] if r["team_uid"] == team_uid), None) if box else None
    results = _games(c, recent)
    for g in results:
        mine, theirs = (g["score"] or "0-0").split("-") if g["home_uid"] == team_uid else reversed((g["score"] or "0-0").split("-"))
        g["outcome"] = "W" if int(mine) > int(theirs) else "D" if mine == theirs else "L"
    from app.services.expected import ordinal

    from app.services import team_tactics

    return {"team": team, "crest": crest(team), "league": box, "place": place, "position": ordinal(place["position"]) if place else None,
            "results": results, "upcoming": _games(c, upcoming), "system": team_tactics.current(c, team_uid),
            "systems": team_tactics.history(c, team_uid)}


GOAL_KINDS = {0: "", 1: "og", 2: "pen"}  # goals in the save's event list (3 is a red card, 4 and 5 shoot-out kicks)
STATS = (("Possession", "possession", "%"), ("Expected goals (xG)", "xg", ""), ("Shots", "shots", ""), ("Shots on target", "shots_on_target", ""),
         ("Long shots", "long_shots", ""), ("Clear-cut chances", "clear_cut_chances", ""), ("Half chances", "half_chances", ""), ("Saves", "saves", ""),
         ("Passes completed", "passes_completed", ""), ("Crosses", "crosses", ""),
         ("Tackles won", "tackles_won", ""), ("Interceptions", "interceptions", ""), ("Fouls", "fouls", ""),
         ("Yellow cards", "yellow_cards", ""), ("Red cards", "red_cards", ""))


def match(c: Connection, fixture_id: int) -> dict | None:
    """A played match: the score, the goals by side, the ground and crowd, both sides' statistics and line-ups (when the save
    brought a report)."""
    data = q.match(c, fixture_id)
    if data is None or data["fixture"]["home_goals"] is None:
        return None
    f = data["fixture"]
    game = _games(c, [f])[0]
    goals: dict[int, list[dict]] = {0: [], 1: []}
    for g in data["goals"]:
        if g["kind"] in GOAL_KINDS:
            minute = f"{g['minute']}+{g['added_time']}'" if g["added_time"] else f"{g['minute']}'"
            note = GOAL_KINDS[g["kind"]]
            goals[g["side"]].append({"uid": g["scorer_uid"], "name": g["name"] or "Unknown", "when": minute + (f" ({note})" if note else "")})
    report = data["game_report"]
    stats, lineups = [], []
    if report and None not in report["sides"]:
        home, away = report["sides"]
        for label, key, unit in STATS:
            a, b = home.get(key), away.get(key)
            if a is None or b is None:
                continue
            total = (a or 0) + (b or 0)
            stats.append({"label": label, "home": f"{a}{unit}", "away": f"{b}{unit}", "share": round(100 * a / total) if total else 50})
        for side in (home, away):
            starters = [dict(p) for p in side["players"] if p["start_position"] or (p["played"] and not p["minute_on"])]
            subs = [p for p in side["players"] if p["person_uid"] not in {s["person_uid"] for s in starters} and p["played"]]
            lineups.append({"starters": starters, "pitch": _on_the_pitch(starters), "subs": subs,
                            "unused": [p for p in side["players"] if not p["played"]], "shape": _shape(starters)})
    known = q.fixture_player_games(c, fixture_id) if not lineups else [[], []]
    return {"game": game, "fixture": f, "goals": goals, "report": report, "stats": stats, "lineups": lineups,
            "known": known if any(known) else None,
            "text": _report_text(f), "ref": f"match:{str(f['date'])[:10]}:{f['home_uid']}:{f['away_uid']}"}


FORM_GAMES, MEETINGS = 5, 5


def _form(c: Connection, team: int, before: str) -> list[dict]:
    rows = q._rows(c, "select * from fixtures where (home_uid = :t or away_uid = :t) and home_goals is not null and date < :d "
                      "order by date desc limit :n", t=team, d=before, n=FORM_GAMES)
    games = _games(c, rows)
    for g in games:
        mine, theirs = g["score"].split("-") if g["home_uid"] == team else reversed(g["score"].split("-"))
        g["outcome"] = "W" if int(mine) > int(theirs) else "D" if mine == theirs else "L"
    return games


def preview(c: Connection, fixture_id: int) -> dict | None:
    """A match still to be played: both sides' form, league places and expected elevens (their usual system and players,
    without the injured and those gone), the last meetings and the archive's written preview."""
    from app.services import tactics
    from app.services.expected import ordinal

    data = q.match(c, fixture_id)
    if data is None or data["fixture"]["home_goals"] is not None:
        return None
    f = data["fixture"]
    day = date.fromisoformat(str(f["date"])[:10])
    game = _games(c, [f])[0]
    league = league_of(c, f["home_uid"])
    box = league_box(c, league) if league and league == league_of(c, f["away_uid"]) else None
    sides = []
    for team in (f["home_uid"], f["away_uid"]):
        eleven = tactics.expected_eleven(c, team, day)
        players = [{"person_uid": uid, "name": eleven["names"].get(uid), "start_position": slot, "shirt_number": None,
                    "rating_shown": None, "goals": 0} for uid, slot in eleven["chosen"].items()] if eleven else []
        place = next((r for r in box["table"] if r["team_uid"] == team), None) if box else None
        sides.append({"form": _form(c, team, str(f["date"])[:10]), "place": place, "position": ordinal(place["position"]) if place else None,
                      "pitch": _on_the_pitch(players), "shape": eleven["shape_words"] if eleven else None,
                      "out": eleven["regulars_out"] if eleven else []})
    meetings = _games(c, data["meetings"][:MEETINGS])
    return {"game": game, "fixture": f, "sides": sides, "league": box, "meetings": meetings, "record": data["record"],
            "text": _report_text(f, kind="preview"), "ref": f"match:{str(f['date'])[:10]}:{f['home_uid']}:{f['away_uid']}"}


def _on_the_pitch(starters: list[dict]) -> list[dict]:
    """The starters with an (x, y) on a pitch attacking upwards, from the slot each started in; players of one slot side by side."""
    by_spot: dict[str, list[dict]] = {}
    for p in starters:
        by_spot.setdefault(p["start_position"] if p["start_position"] in q.PITCH_SPOTS else "M (C)", []).append(p)
    placed = []
    for spot, players in by_spot.items():
        x, y = q.PITCH_SPOTS[spot]
        for index, p in enumerate(players):
            offset = (index - (len(players) - 1) / 2) * q.PITCH_SPREAD
            placed.append({**p, "x": max(9, min(91, x + offset)), "y": y})
    return placed


def _shape(starters: list[dict]) -> str | None:
    from app.services import tactics

    slots = [p["start_position"] for p in starters if p["start_position"]]
    return tactics.formation(slots) if len(slots) == 11 else None


def _report_text(f: dict, kind: str = "report") -> dict | None:
    """The archive's written report (or preview) of the match (match_texts), as a title and paragraphs."""
    from app.models.match_text import MatchText

    row = MatchText.query.filter_by(date=date.fromisoformat(str(f["date"])[:10]), home_uid=f["home_uid"], away_uid=f["away_uid"],
                                    kind=kind).first()
    if row is None or not row.text.strip():
        return None
    title, _, body = row.text.strip().partition("\n")
    return {"title": title.strip(), "body": body.strip()} if body.strip() else {"title": None, "body": title}


PLAYER_MATCHES = 8


def player(c: Connection, uid: int) -> dict | None:
    """A player's page: who he is, his club, this season's numbers in competitive games (the game's own season figures:
    the match reports agree with them), his last matches with their ratings, and his career by season and club."""
    person = q.person(c, uid)
    if person is None:
        return None
    today = today_of(c)
    team = q.teams(c, [person["team_uid"]]).get(person["team_uid"]) if person.get("team_uid") else None
    positions = q.player_positions(q.latest_attributes(c, uid))
    season_start = str(date(today.year if today.month >= 7 else today.year - 1, 7, 1)) if today else "1900-01-01"
    rows = q._rows(c, "select m.*, f.* , m.goals player_goals from match_players m join fixtures f on f.id = m.fixture_id "
                      "join competitions c on c.fm_uid = f.competition_uid where m.person_uid = :u and m.played = 1 "
                      "and f.date >= :s and coalesce(c.scope, '') != 'friendly' order by f.date desc", u=uid, s=season_start)
    rated = [r["rating"] for r in rows if r["rating"]]
    season = {"apps": len(rows), "starts": sum(1 for r in rows if not r["minute_on"]), "goals": sum(r["player_goals"] or 0 for r in rows),
              "assists": sum(r["assists"] or 0 for r in rows), "xg": round(sum(r["xg"] or 0 for r in rows), 2),
              "rating": round(sum(rated) / len(rated), 2) if rated else None}
    recent = []
    for r, g in zip(rows[:PLAYER_MATCHES], _games(c, [{k: r[k] for k in ("id", "date", "home_uid", "away_uid", "competition_uid", "home_goals",
                                                                        "away_goals", "home_goals_after_extra_time", "away_goals_after_extra_time",
                                                                        "home_penalties", "away_penalties")} for r in rows[:PLAYER_MATCHES]])):
        recent.append({**g, "rating": r["rating"], "player_goals": r["player_goals"], "assists": r["assists"]})
    honours = q.player_honours(c, uid)
    career = q.player_career(c, uid, honours)
    names = q.teams(c, {r["team_uid"] for r in career.rows})
    clubs = [{**r, "team": names.get(r["team_uid"], {}), "label": q.season_label(r["season"])} for r in reversed(career.rows)]
    cabinet = q.group_honours([h for h in honours if h["place"] == 1])
    comps = q.competitions(c, {card["competition_uid"] for card in cabinet})
    for card in cabinet:
        card["name"] = comps.get(card["competition_uid"], {}).get("name")
    awards = q.player_awards(c, uid)
    market = q.transfer_market(c, uid)
    for b in market["bids"]:
        b["when"] = date.fromisoformat(str(b["offered_on"])[:10]).strftime("%-d %b %Y") if b["offered_on"] else ""
        b["joins"] = date.fromisoformat(str(b["completes_on"])[:10]).strftime("%-d %b %Y") if b["status"] == "agreed" and b["completes_on"] else ""
    contract = q.current_contract(c, uid)
    if contract:
        contract["expires_words"] = date.fromisoformat(str(contract["expires"])[:10]).strftime("%B %Y") if contract["expires"] else None
    games = q.player_games(c, uid, limit=10)
    for t in games:
        for g in t["games"]:
            g["when"] = date.fromisoformat(str(g["played_on"])[:10]).strftime("%-d %b %Y")
    return {"cabinet": cabinet, "awards": awards, "market": market if (market["active"] or market["rumoured"] or market["bids"]) else None,
            "games": games, "contract": contract,
            "person": person, "team": team, "crest": crest(team), "age": q.age_on(person.get("birth_date"), today),
            "position": positions["label"] if positions else None, "moves": positions["moves"] if positions else [], "season": season, "season_label": q.season_label(int(season_start[:4])),
            "recent": recent, "career": clubs, "totals": career.totals, "trophies": sum(1 for h in honours if h["place"] == 1)}


# -- tournaments: the UEFA competitions and the national teams' ------------------------------------------------
TOURNAMENT_SCORERS = 10
RECENT_DAYS = 120  # a competition with games this recently, or games to come, is in play
SIDEBAR_GAMES, SIDEBAR_PER_COMPETITION, SIDEBAR_DAYS = 30, 3, 10


def _season_of(day: date) -> int:
    return day.year if day.month >= 7 else day.year - 1


def today_of(c: Connection) -> date | None:
    """The game date: the newest save's, else the newest result's."""
    found = q.game_date(c)
    if found is None:
        latest = q._one(c, "select max(date) d from fixtures where home_goals is not null")
        found = date.fromisoformat(str(latest["d"])[:10]) if latest and latest["d"] else None
    return found


def in_play(c: Connection, scope: str, name_like: str | None = None, limit: int = 12) -> list[dict]:
    """The competitions of a scope (continental, national_teams) with games in the last months or to come, the most
    prestigious first."""
    today = today_of(c)
    if today is None:
        return []
    since = str(date.fromordinal(today.toordinal() - RECENT_DAYS))
    like = " and c.name like :like" if name_like else ""
    # a competition without the game's reputation (European Championship Qualifying) gets one from its teams' (0-10 000 -> 0-200)
    return q._rows(c, f"select c.fm_uid, c.name, coalesce(c.reputation, (select avg(t.reputation) / 50 from fixtures f join teams t "
                      f"on t.fm_uid = f.home_uid where f.competition_uid = c.fm_uid and f.date >= :s), 0) prestige "
                      f"from competitions c where c.scope = :scope{like} and exists "
                      f"(select 1 from fixtures f where f.competition_uid = c.fm_uid and (f.date >= :s)) "
                      f"order by prestige desc, c.name limit :n", scope=scope, like=name_like, s=since, n=limit)


def international_order(row: dict) -> tuple:
    """A European site's order of the national teams' competitions: the World Cup, the European Championship, the European
    qualifiers and leagues, then the rest by prestige."""
    name = row["name"]
    tier = 0 if name == "World Cup" else 1 if name == "European Football Championship" else 2 if "Europe" in name or "UEFA" in name else 3
    return tier, -(row.get("prestige") or 0)


def tournament(c: Connection, uid: int) -> dict | None:
    """A competition as it stands this season: its rounds from its games (groups or a league phase with their tables, the
    knockout rounds), the latest results, the next games and the top scorers."""
    comp = q.competition(c, uid)
    latest = q._one(c, "select max(date) d from fixtures where competition_uid = :c and home_goals is not null", c=uid)
    if comp is None or not latest or not latest["d"]:
        return None
    start, end = q.league_window(c, uid, _season_of(date.fromisoformat(str(latest["d"])[:10])))
    rows = q._rows(c, "select * from fixtures where competition_uid = :c and date between :s and :e order by date, id", c=uid, s=start, e=end)
    stages: dict[int, list[dict]] = {}
    for f in rows:
        stages.setdefault(f["stage"], []).append(f)
    rounds = q.round_names(sorted(stages.values(), key=lambda games: str(games[0]["date"])))
    teams = q.teams(c, {t for f in rows for t in (f["home_uid"], f["away_uid"])})
    groups, knockout = [], []
    first_group = next((i for i, r in enumerate(rounds) if (r.get("name") or "").startswith("Group") or r.get("name") == "League phase"), None)
    qualifying = 0
    for index, r in enumerate(rounds):
        name = r.get("name") or "Round"
        if first_group is not None and index < first_group and not name.startswith("Group"):
            qualifying += 1
            name = f"Qualifying round {qualifying}"  # the rounds before the groups or the league phase
        if name.startswith("Group") or name == "League phase":
            table = [{**row, "team": teams.get(row["team_uid"], {}), "crest": crest(teams.get(row["team_uid"]))}
                     for row in q.table_from(r["games"])]
            for team in {t for f in r["games"] for t in (f["home_uid"], f["away_uid"])} - {row["team_uid"] for row in table}:
                table.append({"team_uid": team, "team": teams.get(team, {}), "crest": crest(teams.get(team)), "played": 0, "won": 0,
                              "drawn": 0, "lost": 0, "scored": 0, "conceded": 0, "points": 0, "form": [], "difference": 0, "position": len(table) + 1})
            groups.append({"name": name, "table": table})
        else:
            knockout.append({"name": name, "games": _games(c, r["games"])})
    now = q.competition_now(c, uid)
    scorers = q._rows(c, "select g.scorer_uid, p.name, count(*) goals, max(case g.side when 0 then f.home_uid else f.away_uid end) team_uid "
                         "from goals g join fixtures f on f.id = g.fixture_id left join people p on p.fm_uid = g.scorer_uid "
                         "where f.competition_uid = :c and f.date between :s and :e and g.kind in (0, 2) and g.scorer_uid is not null "
                         "group by g.scorer_uid order by goals desc limit :n", c=uid, s=start, e=end, n=TOURNAMENT_SCORERS)
    names = q.teams(c, {s["team_uid"] for s in scorers})
    for row in scorers:
        row["team"] = names.get(row["team_uid"], {})
    label = q.season_label(int(start[:4])) if not start.endswith("-01-01") else start[:4]
    return {"uid": uid, "name": comp["name"], "label": label, "groups": groups, "knockout": list(reversed(knockout)),
            "recent": _games(c, now["recent"]), "upcoming": _games(c, now["upcoming"]), "scorers": scorers}


SCORE_DAYS_BACK = 320  # the day pages reach back this far (a season); each day is one page of the export
DAY_PER_COMPETITION = 12


def score_competitions(c: Connection) -> list[int]:
    """The competitions the scores follow, in the order shown: the top league, the UEFA competitions, the other top leagues,
    the national teams in play, the lower leagues."""
    leagues = main_leagues(c)
    levels = {r["fm_uid"]: r["level"] for r in q._rows(c, f"select fm_uid, level from competitions where fm_uid in ({','.join(map(str, leagues)) or '0'})")}
    top = [u for u in leagues if levels.get(u) == 1][:6]
    lower = [u for u in leagues if levels.get(u) != 1]
    uefa = [r["fm_uid"] for r in in_play(c, "continental", "UEFA%", 3)]
    national = [r["fm_uid"] for r in sorted(in_play(c, "national_teams", None, 12), key=international_order)[:4]]
    return list(dict.fromkeys(top[:1] + uefa + top[1:] + national + lower))


def match_days(c: Connection, order: list[int] | None = None) -> list[date]:
    """The days with results in the followed competitions, oldest first, as far back as SCORE_DAYS_BACK."""
    today = today_of(c)
    if today is None:
        return []
    order = order if order is not None else score_competitions(c)
    since = str(date.fromordinal(today.toordinal() - SCORE_DAYS_BACK))
    rows = q._rows(c, f"select distinct date from fixtures where competition_uid in ({','.join(map(str, order)) or '0'}) "
                      "and home_goals is not null and date >= :s and date <= :t order by date", s=since, t=str(today))
    return [date.fromisoformat(str(r["date"])[:10]) for r in rows]


def day_results(c: Connection, day: date, order: list[int] | None = None, per_competition: int = DAY_PER_COMPETITION) -> list[dict]:
    """One day's results in the followed competitions, grouped, the biggest names first in each."""
    order = order if order is not None else score_competitions(c)
    rows = q._rows(c, f"select * from fixtures where competition_uid in ({','.join(map(str, order)) or '0'}) and home_goals is not null "
                      "and date = :d", d=str(day))
    reputation = {r["fm_uid"]: r["reputation"] or 0 for r in q._rows(c, f"select fm_uid, reputation from teams where fm_uid in "
                                                                     f"({','.join(str(t) for f in rows for t in (f['home_uid'], f['away_uid'])) or '0'})")}
    found = []
    for uid in order:
        games = sorted((f for f in rows if f["competition_uid"] == uid), key=lambda f: -(reputation.get(f["home_uid"], 0) + reputation.get(f["away_uid"], 0)))
        if games:
            found.append({"uid": uid, "name": (q.competition(c, uid) or {}).get("name"), "games": _games(c, games[:per_competition]), "more": max(0, len(games) - per_competition)})
    return found


def top_results(c: Connection) -> dict | None:
    """The front page's scores: the latest match day's results (not a week mixed together), a few per competition, with the
    day before it to step back to (26 Oct 2026: the box showed the week's biggest names and looked the same every day)."""
    order = score_competitions(c)
    days = match_days(c, order)
    if not days:
        return None
    found, shown = [], 0
    for comp in day_results(c, days[-1], order, SIDEBAR_PER_COMPETITION):
        if shown >= SIDEBAR_GAMES:
            break
        found.append(comp)
        shown += len(comp["games"])
    return {"day": days[-1], "before": days[-2] if len(days) > 1 else None, "competitions": found}


# -- a major international tournament in play: the site's tournament mode ---------------------------------------------------
MAJOR_TOURNAMENTS = ("World Cup", "European Football Championship", "Copa América", "Africa Cup of Nations", "Asian Cup", "Gold Cup",
                     "Confederations Cup", "Nations League Finals")
TOURNAMENT_LEAD_DAYS, TOURNAMENT_TAIL_DAYS = 7, 3  # the mode starts a week before the first game and ends three days after the final


def major_tournament(c: Connection) -> dict | None:
    """The senior international tournament the site is in the middle of (the World Cup, a continental championship...), from
    its finals' fixtures: in play from a week before the first game to three days after the last. None when there is none."""
    today = today_of(c)
    if today is None:
        return None
    since, until = str(today - timedelta(days=TOURNAMENT_TAIL_DAYS + 60)), str(today + timedelta(days=TOURNAMENT_LEAD_DAYS + 60))
    found = []
    for comp in q._rows(c, "select fm_uid, name, reputation from competitions where scope = 'national_teams' and name not like '%Qualif%' "
                           "and name not like 'Under%' and name not like 'U1%' and name not like 'U2%' and name not like '%Youth%'"):
        if not any(comp["name"] == m or comp["name"].startswith(m) for m in MAJOR_TOURNAMENTS):
            continue
        span = q._one(c, "select min(date) first, max(date) last, sum(home_goals is not null) played, count(*) games from fixtures "
                         "where competition_uid = :c and date between :s and :e", c=comp["fm_uid"], s=since, e=until)
        if not span or not span["first"]:
            continue
        first, last = (date.fromisoformat(str(span[k])[:10]) for k in ("first", "last"))
        if not (first - timedelta(days=TOURNAMENT_LEAD_DAYS) <= today <= last + timedelta(days=TOURNAMENT_TAIL_DAYS)):
            continue
        day = (today - first).days + 1
        phase = "about to start" if today < first else "over" if today > last else "in play"
        found.append({"uid": comp["fm_uid"], "name": comp["name"], "label": str(first.year), "first": first, "last": last, "today": today,
                      "day": day if phase == "in play" else None, "days_to_go": (first - today).days if phase == "about to start" else None,
                      "phase": phase, "played": span["played"], "games": span["games"],
                      "order": international_order({"name": comp["name"], "prestige": comp["reputation"]})})
    return min(found, key=lambda t: t["order"]) if found else None


def our_players_at(c: Connection, uid: int, start: str, end: str) -> list[dict]:
    """The followed clubs' players at a tournament: each with his nation, his games (the game's own rows for tracked players,
    the match reports for the rest), minutes, goals, assists and average rating. The most used first."""
    clubs = ",".join(str(int(u)) for u in followed()) or "0"
    people = {r["fm_uid"]: r for r in q._rows(c, f"select p.fm_uid, p.name, p.team_uid club_uid from people p join teams t on t.fm_uid = p.team_uid "
                                                 f"where t.club_uid in ({clubs})")}
    if not people:
        return []
    ids = ",".join(str(u) for u in people)
    rows = q._rows(c, f"select g.person_uid, g.team_uid, g.fixture_id, g.status, g.minutes, g.rating, g.goals, g.assists from player_games g "
                      f"join fixtures f on f.id = g.fixture_id where f.competition_uid = :c and f.date between :s and :e and g.person_uid in ({ids})",
                   c=uid, s=start, e=end)
    seen = {(r["person_uid"], r["fixture_id"]) for r in rows}
    for r in q._rows(c, f"select m.person_uid, case m.side when 0 then f.home_uid else f.away_uid end team_uid, m.fixture_id, m.played, "
                        f"m.minute_on, m.minute_off, m.rating, m.goals, m.assists from match_players m join fixtures f on f.id = m.fixture_id "
                        f"where f.competition_uid = :c and f.date between :s and :e and m.person_uid in ({ids})", c=uid, s=start, e=end):
        if (r["person_uid"], r["fixture_id"]) in seen:
            continue
        minutes = ((r["minute_off"] or 90) - (r["minute_on"] or 0)) if r["played"] else None
        rows.append({"person_uid": r["person_uid"], "team_uid": r["team_uid"], "fixture_id": r["fixture_id"], "status": "played" if r["played"] else "unused",
                     "minutes": minutes, "rating": r["rating"], "goals": r["goals"], "assists": r["assists"]})
    by: dict[int, dict] = {}
    for r in rows:
        p = by.setdefault(r["person_uid"], {"person_uid": r["person_uid"], "name": people[r["person_uid"]]["name"], "club_uid": people[r["person_uid"]]["club_uid"],
                                            "team_uid": r["team_uid"], "apps": 0, "squad": 0, "minutes": 0, "goals": 0, "assists": 0, "ratings": []})
        p["squad"] += 1
        if r["status"] == "played":
            p["apps"] += 1
            p["minutes"] += r["minutes"] or 0
            p["goals"] += r["goals"] or 0
            p["assists"] += r["assists"] or 0
            if r["rating"]:
                p["ratings"].append(r["rating"])
    nations = q.teams(c, {p["team_uid"] for p in by.values()})
    out = []
    for p in by.values():
        p["rating"] = round(sum(p["ratings"]) / len(p["ratings"]), 2) if p["ratings"] else None
        p["nation"] = nations.get(p["team_uid"], {})
        p["crest"] = crest(p["nation"])
        del p["ratings"]
        out.append(p)
    return sorted(out, key=lambda p: (-p["minutes"], -p["squad"], p["name"]))


def tournament_hub(c: Connection) -> dict | None:
    """The front page's tournament box when a major international tournament is in play: where it stands, today's games,
    yesterday's results, the next games, the followed clubs' players there and the top scorers."""
    t = major_tournament(c)
    if t is None:
        return None
    board = tournament(c, t["uid"])
    if board is None:
        return None
    today = t["today"]
    games = q._rows(c, "select * from fixtures where competition_uid = :c and date between :s and :e order by date, id",
                    c=t["uid"], s=str(today - timedelta(days=1)), e=str(today + timedelta(days=2)))
    by_day: dict[str, list[dict]] = {}
    for g in _games(c, games):
        by_day.setdefault(str(g["date"])[:10], []).append(g)
    start, end = str(t["first"]), str(t["last"])
    return {**t, "board": board, "today_games": by_day.get(str(today), []), "yesterday": by_day.get(str(today - timedelta(days=1)), []),
            "tomorrow": by_day.get(str(today + timedelta(days=1)), []), "ours": our_players_at(c, t["uid"], start, end),
            "scorers": board["scorers"][:5], "round": _round_now(board, today)}


def _round_now(board: dict, today: date) -> str | None:
    """The round the tournament is in: the knockout round with the latest game on or before tomorrow, else the group stage."""
    current, latest = None, None
    for r in board["knockout"]:
        days = [g["date"] for g in r["games"] if g["date"] <= today + timedelta(days=1)]
        if days and (latest is None or max(days) > latest):
            current, latest = r["name"], max(days)
    return current or ("Group stage" if board["groups"] else None)
