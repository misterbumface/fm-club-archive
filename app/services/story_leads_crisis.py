"""Crisis mode (4 Nov 2026): a well-known club or nation taking far fewer points than its players should.

"Should" is the firepower model: each game's expected points from the two sides' strengths (app/services/firepower). A
team is in crisis while its last CRISIS_GAMES competitive games gave at least CRISIS_GAP points fewer than expected, and
it is a team that should win most games. Nothing is stored: the state is read again from the games at any date, so a
crisis has a start (the first game of the spell), lasts while the gap stays, and ends when it closes.

Three uses: a "crisis" story when a crisis starts or deepens in the cycle; a "crisis_over" story when it ends; and an
"in_crisis" fact on every other story of the season about the team, so a preview or a report has the right weather.
"""

from __future__ import annotations

from datetime import timedelta

from app.services import firepower
from app.services.story_leads_now import _manager, _news, _result_line

CRISIS_GAMES = 6  # the window: a team's last this many competitive games
CRISIS_MIN_GAMES = 4  # ... at least this many (a nation plays few)
CRISIS_GAP = 5.0  # points below the squad's expected haul over the window
CRISIS_EXPECTED = 1.6  # ... for a team that should take this many a game (a favourite most weeks)
CLUB_STANDING, NATION_STANDING = 6500, 5500  # the clubs and the nations well known enough for the papers to call it a crisis
NATION_DAYS, CLUB_DAYS = 730, 150  # a window reaches back at most this far (a nation plays few games; a club's not into last season)
HISTORY = 10  # games read before the window, to find where a crisis started
TABLE_SLACK = 1  # a club within this many places of where its squad should be is in no crisis, whatever the run
UNRATED = 130.0  # the strength of an opponent without eleven rated players (a small club)
COMPETITIVE = ("league", "cup", "continental", "national_teams")
CRISIS_SIZE, OVER_SIZE = 0.75, 0.55


def _marked() -> set[str]:
    from app.services.editions import SEASON_NEWS

    return SEASON_NEWS | {"interview", "travel_log"}


def _games(w, team: int, nation: bool) -> list[dict]:
    """The team's competitive games up to the cycle's end, newest first (friendlies and youth games are not counted)."""
    since = str(w.until - timedelta(days=NATION_DAYS if nation else CLUB_DAYS))
    return w.rows("select f.* from fixtures f join competitions c on c.fm_uid = f.competition_uid "
                  f"where (f.home_uid = :t or f.away_uid = :t) and f.home_goals is not null and f.date <= :u and f.date >= :s "
                  f"and c.scope in {COMPETITIVE} and c.name not like '%Friendl%' order by f.date desc limit :n",
                  t=team, u=str(w.until), s=since, n=CRISIS_GAMES + HISTORY)


def _points(f: dict, team: int) -> int:
    home = f["home_uid"] == team
    extra = f["home_goals_after_extra_time"] is not None
    mine, theirs = (f["home_goals_after_extra_time"], f["away_goals_after_extra_time"]) if extra else (f["home_goals"], f["away_goals"])
    mine, theirs = (mine, theirs) if home else (theirs, mine)
    return 3 if mine > theirs else 1 if mine == theirs else 0


def _expected(w, strengths, f: dict, team: int, own: float) -> float:
    other = strengths(f["away_uid"] if f["home_uid"] == team else f["home_uid"])
    neutral = w.comp(f["competition_uid"]).get("scope") == "national_teams" and "Qualif" not in (w.comp(f["competition_uid"]).get("name") or "")
    return firepower.expected_points(own, other["overall"] if other else UNRATED, None if neutral else f["home_uid"] == team)


def _window(w, strengths, games: list[dict], start: int, team: int, own: float) -> dict | None:
    """The CRISIS_GAMES games from ``start`` (newest first): their points, the expected points and whether it is a crisis."""
    window = games[start:start + CRISIS_GAMES]
    if len(window) < CRISIS_MIN_GAMES:
        return None
    points = sum(_points(f, team) for f in window)
    expected = sum(_expected(w, strengths, f, team, own) for f in window)
    two_wins = len(window) >= 2 and _points(window[0], team) == 3 and _points(window[1], team) == 3
    crisis = expected / len(window) >= CRISIS_EXPECTED and expected - points >= CRISIS_GAP and not two_wins
    return {"games": window, "points": points, "expected": expected, "crisis": crisis}


def team_state(w, strengths, team: int) -> dict | None:
    """The team's crisis state at the cycle's end and at its start, and where the present or the last spell began."""
    nation = w.team(team).get("club_uid") is None
    if (w.rep(team) or 0) < (NATION_STANDING if nation else CLUB_STANDING):
        return None
    own = strengths(team)
    games = _games(w, team, nation) if own else []
    if not games:
        return None
    windows = [_window(w, strengths, games, i, team, own["overall"]) for i in range(len(games))]
    now = windows[0]
    before_index = next((i for i, f in enumerate(games) if str(f["date"]) <= str(w.since)), None)
    before = windows[before_index] if before_index is not None else None
    if not now:
        return None
    if not nation and any(x and x["crisis"] for x in (now, before)):
        table = _squad_place(w, strengths, team)
        if table and table["place"] <= table["place_its_squad_suggests"] + TABLE_SLACK:
            return None  # a bad run, but about as high in the table as the squad should be (Inter, second, 7 points from 6): no crisis
    spell = before_index if not now["crisis"] else 0
    while spell is not None and spell + 1 < len(windows) and windows[spell + 1] and windows[spell + 1]["crisis"]:
        spell += 1
    began = windows[spell]["games"][0]["date"] if spell is not None and windows[spell] and windows[spell]["crisis"] else None
    return {"team": team, "nation": nation, "strength": own, "now": now, "before": before, "began": began,
            "new_games": [f for f in games if str(f["date"]) > str(w.since)]}


def _since_last_win(games: list[dict], team: int) -> int:
    return next((i for i, f in enumerate(games) if _points(f, team) == 3), len(games))


def _squad_place(w, strengths, team: int) -> dict:
    """A club's league place now and the place its squad suggests (its strength's rank among the league's clubs)."""
    from app.services.story_leads_now import _table

    league, _ = w.level(team, w.season)
    if not league:
        return {}
    table, place, _ = _table(w, league)
    rated = {t: (strengths(t) or {}).get("overall") for t in place}
    if team not in place or sum(v is not None for v in rated.values()) < len(place) * 0.8:
        return {}
    suggested = sorted(place, key=lambda t: -(rated[t] or 0)).index(team) + 1
    return {"league": w.comp(league).get("name"), "place": place[team], "place_its_squad_suggests": suggested,
            "points": table[place[team] - 1]["points"], "played": table[place[team] - 1]["played"]}


def _facts(w, state: dict) -> dict:
    team, now = state["team"], state["now"]
    games = now["games"]
    facts = {"team": w.name(team), "the_last_games": [_result_line(w, f) for f in games],
             "points_from_them": f"{now['points']} of {3 * len(games)}",
             "points_a_squad_this_strong_should_take_from_them": f"about {round(now['expected'])}",
             "games_since_the_last_win": _since_last_win(games, team) if _since_last_win(games, team) < len(games) else f"no win in the last {len(games)}",
             "the_squad": firepower.words(state["strength"]),
             "the_crisis_began_with": str(state["began"]) if state["began"] else None,
             "manager": _manager(w, team) if not state["nation"] else None}
    if not state["nation"]:
        facts.update(_squad_place(w, state["strengths"], team))
        pressure = _news(w, "where n.kind = 'board_pressure' and n.team_uid = :t and n.day > :d", t=team, d=str(w.until - timedelta(days=60)))
        if pressure:
            facts["the_board"] = f"said to be losing patience with {pressure[-1]['person']} ({pressure[-1]['day']})"
    return {k: v for k, v in facts.items() if v is not None}


def crises(w) -> list:
    """A well-known team's crisis begun or deepened in the cycle (a new game in it), and one ended."""
    from app.services.story_leads import Lead

    strengths = firepower.Strengths(w.c)
    teams = {r["team"] for r in w.rows("select home_uid team from fixtures where date > :s and date <= :u and home_goals is not null "
                                       "union select away_uid from fixtures where date > :s and date <= :u and home_goals is not null",
                                       s=str(w.since), u=str(w.until))}
    leads = []
    for team in teams:
        state = team_state(w, strengths, team)
        if not state or not state["new_games"]:
            continue
        state["strengths"] = strengths
        now, before = state["now"], state["before"]
        last = state["new_games"][0]
        fame = (w.rep(team) or 0) / 8000
        weight = w.weight(last["competition_uid"])
        if now["crisis"]:
            deepened = before is not None and before["crisis"]
            size = CRISIS_SIZE + (now["expected"] - now["points"] - CRISIS_GAP) / 10 + (0 if deepened else 0.15)
            angle = "a crisis deepens" if deepened else "a crisis"
            facts = {**_facts(w, state), "the_story": "a crisis that goes on" if deepened else "a crisis begins",
                     "note": "the crisis is the papers' own reading: a squad this strong should take far more points than it has. "
                             "Tell the players, the results and the mood; never a number not in this notebook."}
            leads.append(Lead("crisis", f"{w.name(team)}: {angle} ({now['points']} points from {len(now['games'])} games)", w.until,
                              weight * 10 * size * fame, facts, [f"club:{team}"], w.beats_for(team), slug=f"{team}:{w.until}"))
        elif before is not None and before["crisis"]:
            facts = {**_facts(w, state), "the_story": "the crisis is over: the results have caught up with the squad",
                     "the_games_that_ended_it": [_result_line(w, f) for f in reversed(state["new_games"])]}
            leads.append(Lead("crisis_over", f"{w.name(team)}: the crisis is over", w.until, weight * 10 * OVER_SIZE * fame, facts,
                              [f"club:{team}"], w.beats_for(team), slug=f"{team}:{w.until}"))
    return sorted(leads, key=lambda lead: -lead.score)[:6]


def mark(w, leads: list) -> None:
    """Every other story about a team in crisis says so (in_crisis), so a preview or a report has the right weather."""
    strengths = firepower.Strengths(w.c)
    marked = _marked()
    found: dict[int, str | None] = {}
    for lead in leads:
        if lead.kind in ("crisis", "crisis_over") or lead.kind not in marked:
            continue
        teams = [int(ref.split(":")[1]) for ref in lead.refs if ref.startswith("club:") and ref.split(":")[1].isdigit()]
        teams += [int(part) for ref in lead.refs if ref.startswith("match:") for part in ref.split(":")[2:4] if part.isdigit()]
        notes = []
        for team in dict.fromkeys(teams):
            if team not in found:
                state = team_state(w, strengths, team)
                found[team] = (f"{w.name(team)} are in crisis: {state['now']['points']} points from their last {len(state['now']['games'])} "
                               f"games, far below what their players should take") if state and state["now"]["crisis"] else None
            if found[team]:
                notes.append(found[team])
        if notes:
            lead.facts["in_crisis"] = notes

