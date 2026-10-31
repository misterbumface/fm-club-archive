"""The season being played, from the match reports.

The game writes a player's season numbers into his history only when the season ends: in the season being played his
rows are empty or missing (Liverpool 2063/64 in September: 21 league rows, all with 0 games), and every page and story
that reads player_seasons saw nobody playing. The match reports have every game. After each import this fills the
current season's rows (person, team, season, kind) from them where the reports have more games than the save's rows;
when the season ends the save's own numbers, which are complete, are larger and stay.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.models import world

KINDS = {"friendly": 0, "league": 1, "cup": 2, "continental": 3, "national_teams": 4}  # competition scope -> player_seasons kind
FIGURES = ("starts", "substitute_appearances", "rated_appearances", "goals", "assists", "player_of_match", "yellow_cards",
           "red_cards", "minutes", "rating_total")


def current_seasons(game_date: date) -> tuple[int, int]:
    """The season being played in a league of July to June, and in a league of the calendar year."""
    return (game_date.year if game_date.month >= 7 else game_date.year - 1), game_date.year


def fill_current_seasons(connection: Connection, game_date: date) -> int:
    """Fill the current season's player_seasons rows from the match reports; the number of rows written."""
    summer, calendar_year = current_seasons(game_date)
    calendar = {r[0] for r in connection.execute(text(
        "select competition_uid from competition_editions e where year = (select max(year) from competition_editions x "
        "where x.competition_uid = e.competition_uid) and season_label not like '%/%'"))}
    totals: dict[tuple, Counter] = defaultdict(Counter)
    for r in connection.execute(text(
            "select m.person_uid, case when m.side = 0 then f.home_uid else f.away_uid end team, f.competition_uid, f.date, c.scope, "
            "m.minute_on, m.minute_off, m.goals, m.assists, m.rating, m.yellow_cards, coalesce(m.red_cards, 0) red_cards, "
            "coalesce(r.minutes, 90) match_minutes, r.player_of_the_match_uid = m.person_uid pom from match_players m "
            "join fixtures f on f.id = m.fixture_id join competitions c on c.fm_uid = f.competition_uid "
            "left join match_reports r on r.fixture_id = f.id where m.played = 1 and m.person_uid is not null "
            "and f.date >= :start and f.date <= :today"), {"start": str(date(min(summer, calendar_year), 1, 1)), "today": str(game_date)}).mappings():
        kind = KINDS.get(r["scope"])
        if kind is None:
            continue
        day = date.fromisoformat(str(r["date"])[:10])
        is_calendar = r["competition_uid"] in calendar
        season = day.year if is_calendar else (day.year if day.month >= 7 else day.year - 1)
        if season != (calendar_year if is_calendar else summer):
            continue
        t = totals[(r["person_uid"], r["team"], season, kind)]
        t["starts" if r["minute_on"] == 0 else "substitute_appearances"] += 1
        t["goals"] += r["goals"]
        t["assists"] += r["assists"]
        t["yellow_cards"] += 1 if r["yellow_cards"] else 0
        t["red_cards"] += 1 if r["red_cards"] else 0
        t["player_of_match"] += 1 if r["pom"] else 0
        t["minutes"] += max((r["minute_off"] or r["match_minutes"]) - r["minute_on"], 0)
        if r["rating"] is not None:
            t["rated_appearances"] += 1
            t["rating_total"] += round(r["rating"] * 10)
    if not totals:
        return 0
    seasons = ",".join(str(s) for s in {summer, calendar_year})
    saved: dict[tuple, list[dict]] = defaultdict(list)
    leagues: dict[tuple, Counter] = defaultdict(Counter)
    for r in connection.execute(text(f"select * from player_seasons where season_start in ({seasons})")).mappings():
        saved[(r["person_uid"], r["team_uid"], r["season_start"], r["kind"])].append(dict(r))
        if r["league_uid"]:
            leagues[(r["team_uid"], r["season_start"])][r["league_uid"]] += 1
    rows = []
    for key, t in totals.items():
        have = sorted(saved.get(key, []), key=lambda r: r["seq"])
        if t["starts"] + t["substitute_appearances"] <= sum(r["starts"] + r["substitute_appearances"] for r in have):
            continue
        person, team, season, kind = key
        league = leagues[(team, season)].most_common(1)
        rows.append({"person_uid": person, "team_uid": team, "season_start": season, "kind": kind, "seq": 0,
                     "league_uid": have[0]["league_uid"] if have else (league[0][0] if league else None),
                     "goals_conceded": have[0]["goals_conceded"] if have else 0, "clean_sheets": have[0]["clean_sheets"] if have else 0,
                     "as_of": game_date, **{name: t[name] for name in FIGURES}})
        for other in have[1:]:  # a second spell's row: its games are in the reports' totals now
            rows.append({**other, **{name: 0 for name in FIGURES}, "as_of": game_date})
    table = world.WorldPlayerSeason.__table__
    from app.services.save_import import _upsert

    _upsert(connection, table, rows, ["person_uid", "team_uid", "season_start", "kind", "seq"])
    return len(rows)
