"""Expected goals and points: a league's xG table, as the game's Data Hub shows it.

A team's xG is the sum of its players' xG in each match with a report (match_players.xg), its xGA the opponents'. Both
agree with the game's xG table to the decimal (Premier Division, October 2063). Expected points are worked out per match
from the two xG values with a Poisson model (the chances of a win and a draw); the game works shot by shot, which the
save does not keep, so they come within about 0.2 of its figures (0.4 for Chelsea's one-sided games).
"""

from __future__ import annotations

import math

from sqlalchemy import text
from sqlalchemy.engine import Connection

MAX_GOALS = 12
MIN_COVER = 0.8  # of the played games with a report before a table is shown


def expected_points(xg: float, xga: float) -> float:
    """3 x the chance of scoring more than the other side + the chance of a draw, each side's goals Poisson in its xG."""
    def chances(rate: float) -> list[float]:
        return [math.exp(-rate) * rate ** k / math.factorial(k) for k in range(MAX_GOALS)]
    mine, theirs = chances(xg), chances(xga)
    win = sum(p * q for i, p in enumerate(mine) for j, q in enumerate(theirs) if i > j)
    draw = sum(p * q for p, q in zip(mine, theirs))
    return 3 * win + draw


def xg_table(connection: Connection, competition_uid: int, start: str, end: str) -> list[dict]:
    """Each team's games, goals, points, xG, xGA and expected points in the competition's played games between start and
    end that have a report, best expected points first, with the place in the real table (by points, goal difference,
    goals). Empty when too few games have a report."""
    rows = connection.execute(text(
        "select f.id, f.home_uid, f.away_uid, f.home_goals, f.away_goals, "
        "(select sum(xg) from match_players m where m.fixture_id = f.id and m.side = 0) home_xg, "
        "(select sum(xg) from match_players m where m.fixture_id = f.id and m.side = 1) away_xg "
        "from fixtures f where f.competition_uid = :c and f.date between :s and :e and f.home_goals is not null"),
        {"c": competition_uid, "s": start, "e": end}).mappings().all()
    with_xg = [r for r in rows if r["home_xg"] is not None and r["away_xg"] is not None]
    if not rows or len(with_xg) < MIN_COVER * len(rows):
        return []
    teams: dict[int, dict] = {}
    for r in with_xg:
        for team, gf, ga, xg, xga in ((r["home_uid"], r["home_goals"], r["away_goals"], r["home_xg"], r["away_xg"]),
                                      (r["away_uid"], r["away_goals"], r["home_goals"], r["away_xg"], r["home_xg"])):
            t = teams.setdefault(team, {"team_uid": team, "games": 0, "goals_for": 0, "goals_against": 0, "points": 0,
                                        "xg": 0.0, "xga": 0.0, "xpts": 0.0})
            t["games"] += 1
            t["goals_for"] += gf
            t["goals_against"] += ga
            t["points"] += 3 if gf > ga else 1 if gf == ga else 0
            t["xg"] += xg
            t["xga"] += xga
            t["xpts"] += expected_points(xg, xga)
    table = sorted(teams.values(), key=lambda t: (-t["points"], -(t["goals_for"] - t["goals_against"]), -t["goals_for"]))
    for place, t in enumerate(table, 1):
        t["place"] = place
    table.sort(key=lambda t: -t["xpts"])
    for place, t in enumerate(table, 1):
        t["xpts_place"] = place
        t["goals_vs_xg"] = t["goals_for"] - t["xg"]
        t["conceded_vs_xga"] = t["goals_against"] - t["xga"]
        t["points_vs_xpts"] = t["points"] - t["xpts"]
    return table


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def words(t: dict) -> str:
    """One team's line for the writers."""
    return (f"{t['points']} points from {t['games']} games against {t['xpts']:.1f} expected ({t['points_vs_xpts']:+.1f}); "
            f"{t['goals_for']} scored from {t['xg']:.1f} xG, {t['goals_against']} conceded from {t['xga']:.1f} xG against; "
            f"{ordinal(t['place'])} in the table, {ordinal(t['xpts_place'])} on expected points")
