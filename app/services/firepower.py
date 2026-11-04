"""A team's firepower: how good its players are, line by line, and so what it should take from its games (4 Nov 2026: "if
England is packed with stars and they're doing badly, that's what a reporter would know").

The strength is the game's current ability of the best eleven a team can pick: a club from its own squad, a nation from
every player of its nationality at a club. Each player counts in the line he starts in most (his team sheets). Against
another team's strength it gives the points a team should expect from a game; set against the points it took, the gap
says whether a team is doing as well as its players should (a crisis, see story_leads_crisis).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from sqlalchemy.engine import Connection

from app.services import world_queries as q

LINES = {"goal": {"GK"}, "defence": {"D (C)", "D (R)", "D (L)", "SW", "WB (R)", "WB (L)"},
         "midfield": {"DM", "M (C)", "M (R)", "M (L)", "AM (C)"}, "attack": {"ST (C)", "AM (R)", "AM (L)"}}
SHAPE = {"goal": 1, "defence": 4, "midfield": 4, "attack": 2}  # the best eleven: so many from each line
ELEVEN = sum(SHAPE.values())
NATION_POOL = 80  # a nation picks from its best this many players at clubs
STARS = 3
# What a stronger eleven is worth (fitted on 4,950 games of the leagues of reputation 140 and up, 2062-64, in the live
# archive, 4 Nov 2026): points = MIDDLE + SWING * tanh((difference + HOME_EDGE) / SPREAD). Ten points of strength are about
# a third of a point a game; being at home is worth eight. The order it gives agrees with the game's own season preview
# (bookmakers' odds) at 0.87 for the 2063-64 Premier Division and 0.75 for La Liga (rank correlation).
HOME_EDGE = 8.0
SPREAD = 50.0
SWING = 1.8
MIDDLE = 1.38


def _lines(c: Connection, uids: list[int]) -> dict[int, str]:
    """Each player's line, from the position he starts in most."""
    if not uids:
        return {}
    found: dict[int, Counter] = defaultdict(Counter)
    for r in q._rows(c, f"select person_uid, start_position, count(*) n from match_players where person_uid in ({','.join(map(str, uids))}) "
                        f"and start_position is not null group by 1, 2"):
        line = next((name for name, slots in LINES.items() if r["start_position"] in slots), None)
        if line:
            found[r["person_uid"]][line] += r["n"]
    return {uid: count.most_common(1)[0][0] for uid, count in found.items()}


def _squad(c: Connection, team_uid: int) -> list[dict]:
    """The players a team picks from, best first: a nation's senior team its nationals at clubs, a club its own squad."""
    team = q._one(c, "select club_uid, nation_uid, age_limit from teams where fm_uid = :t", t=team_uid)
    if team and team["club_uid"] is None and team["nation_uid"] and not team["age_limit"]:
        nation = q._one(c, "select fm_uid from nations where fm_uid = :t", t=team_uid)
        if nation:  # a nation's senior team has the nation's own uid
            return q._rows(c, "select fm_uid, name, current_ability ability from people where nation_uid = :n and team_uid is not null "
                              f"and current_ability is not null order by current_ability desc limit {NATION_POOL}", n=team_uid)
    return q._rows(c, "select fm_uid, name, current_ability ability from people where team_uid = :t and current_ability is not null "
                      "order by current_ability desc", t=team_uid)


def strength(c: Connection, team_uid: int) -> dict | None:
    """The team's best eleven by lines: its overall strength (the eleven's average ability, 0-200), each line's and its
    stars. None when fewer than eleven of its players are rated."""
    squad = _squad(c, team_uid)
    if len(squad) < ELEVEN:
        return None
    line_of = _lines(c, [p["fm_uid"] for p in squad])
    picked: dict[str, list[dict]] = {line: [] for line in SHAPE}
    for p in squad:  # best first: each line takes its best
        line = line_of.get(p["fm_uid"])
        if line and len(picked[line]) < SHAPE[line]:
            picked[line].append(p)
    chosen = {p["fm_uid"] for found in picked.values() for p in found}
    spare = [p for p in squad if p["fm_uid"] not in chosen and p["fm_uid"] not in line_of]  # no team sheets yet: anywhere
    for line, need in SHAPE.items():
        while len(picked[line]) < need and spare:
            picked[line].append(spare.pop(0))
    eleven = [p for found in picked.values() for p in found]
    if len(eleven) < ELEVEN:
        return None
    return {"overall": round(sum(p["ability"] for p in eleven) / len(eleven), 1),
            "lines": {line: round(sum(p["ability"] for p in found) / len(found), 1) for line, found in picked.items() if found},
            "stars": [p["name"] for p in sorted(eleven, key=lambda p: -p["ability"])[:STARS]]}


class Strengths:
    """Strengths read once each."""

    def __init__(self, c: Connection) -> None:
        self.c = c
        self._found: dict[int, dict | None] = {}

    def __call__(self, team_uid: int | None) -> dict | None:
        if not team_uid:
            return None
        if team_uid not in self._found:
            self._found[team_uid] = strength(self.c, team_uid)
        return self._found[team_uid]


def expected_points(own: float, other: float, at_home: bool | None) -> float:
    """The points a side of strength ``own`` should take from a game against ``other`` (None: a neutral ground)."""
    edge = 0.0 if at_home is None else HOME_EDGE if at_home else -HOME_EDGE
    return MIDDLE + SWING * math.tanh((own - other + edge) / SPREAD)


def words(found: dict | None) -> str | None:
    """A strength for a notebook: "the best eleven rates 171 of 200 (defence 168, midfield 172, attack 176); stars: ..."."""
    if not found:
        return None
    lines = ", ".join(f"{line} {value:.0f}" for line, value in found["lines"].items() if line != "goal")
    return f"the best eleven rates {found['overall']:.0f} of 200 ({lines}); its best players: {', '.join(found['stars'])}"
