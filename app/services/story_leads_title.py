"""A league title that is already won (9 Nov 2026: Chelsea were 24 points clear with five games left and the papers still wrote of
leaders who "can be caught"). The game names a champion only when the season ends, so the sum is done here, from the fixtures.

Only for a plain league: every club plays every other twice, and the last season was the same, with its top club as champion.
A league that splits in two, or ends in play-offs, or has had points taken off this season, is never called settled.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy.engine import Connection

from app.services import world_queries as q
from app.services.story_leads_now import current_leagues

CLINCHED_SIZE = 16  # x the league's weight (2 to 5): more than a round of games, less than the end of a final
LEAGUES_NAMED = 2  # a story about clubs of several leagues: the facts of this many


def _fixtures(c: Connection, league: int, start: str, end: str) -> list[dict]:
    return q._rows(c, "select date, home_uid, away_uid, home_goals, away_goals from fixtures where competition_uid = :c and date between :s and :e "
                      "order by date", c=league, s=start, e=end)


def _is_double_round(games: list[dict]) -> int | None:
    """The games each club plays when every club plays every other twice; None for any other league."""
    count: dict[int, int] = defaultdict(int)
    for g in games:
        count[g["home_uid"]] += 1
        count[g["away_uid"]] += 1
    total = 2 * (len(count) - 1)
    return total if total > 0 and set(count.values()) == {total} else None


def _points(games: list[dict]) -> tuple[dict[int, int], dict[int, int]]:
    points: dict[int, int] = defaultdict(int)
    played: dict[int, int] = defaultdict(int)
    for g in games:
        if g["home_goals"] is None:
            continue
        for team, scored, conceded in ((g["home_uid"], g["home_goals"], g["away_goals"]), (g["away_uid"], g["away_goals"], g["home_goals"])):
            played[team] += 1
            points[team] += 3 if scored > conceded else 1 if scored == conceded else 0
    return points, played


def _window(c: Connection, league: int, day: date) -> tuple[str, str]:
    start, end = q.league_window(c, league, day.year if day.month >= 7 else day.year - 1)
    return q.league_window(c, league, day.year) if start.endswith("-01-01") else (start, end)


def _last_season_was_plain(c: Connection, league: int, start: str) -> bool:
    """The last season had the same shape, and the club with the most points was its champion: no split and no play-offs."""
    first = int(start[:4]) - 1
    games = [g for g in _fixtures(c, league, f"{first}{start[4:]}", start) if str(g["date"])[:10] < start]
    if not _is_double_round(games):
        return False
    champion = q._rows(c, "select p.team_uid from edition_places p join competition_editions e on e.competition_uid = p.competition_uid and e.year = p.year "
                          "where e.competition_uid = :c and p.place = 1 and e.decided_on < :s order by e.decided_on desc limit 1", c=league, s=start)
    points = _points(games)[0]
    return bool(champion) and points.get(champion[0]["team_uid"]) == max(points.values())


def _docked(c: Connection, league: int, start: str) -> bool:
    if not q._rows(c, "select 1 from sqlite_master where name = 'point_deductions'"):
        return False
    return bool(q._rows(c, "select 1 from point_deductions where competition_uid = :c and as_of >= :s limit 1", c=league, s=start))


def settled(c: Connection, league: int, day: date) -> dict | None:
    """The champions of a league whose title cannot change any more, as of ``day``: None while it can, or when the league is not plain."""
    start, end = _window(c, league, day)
    games = _fixtures(c, league, start, end)
    total = _is_double_round(games)
    if not total:
        return None
    known = [g for g in games if str(g["date"])[:10] <= str(day)]
    points, played = _points(known)
    teams = {g["home_uid"] for g in games} | {g["away_uid"] for g in games}
    leader = max(teams, key=lambda t: points[t])

    def best_of_others(points: dict[int, int], played: dict[int, int]) -> tuple[int, int]:
        return max(((points[t] + 3 * (total - played[t]), t) for t in teams if t != leader))

    if points[leader] <= best_of_others(points, played)[0] or _docked(c, league, start) or not _last_season_was_plain(c, league, start):
        return None
    clinched, left_then = None, None
    for last in sorted({str(g["date"])[:10] for g in known}):
        points_then, played_then = _points([g for g in known if str(g["date"])[:10] <= last])
        if points_then[leader] > best_of_others(points_then, played_then)[0]:
            clinched, left_then = last, total - played_then[leader]
            break
    second = max((t for t in teams if t != leader), key=lambda t: points[t])
    return {"league": league, "season_start": start, "champions_uid": leader, "points": points[leader], "games_left": total - played[leader],
            "second_uid": second, "second_points": points[second], "second_can_reach": points[second] + 3 * (total - played[second]),
            "best_any_other_can_reach": best_of_others(points, played)[0], "clinched_on": clinched, "games_left_when_won": left_then}


def _team_name(c: Connection, uid: int) -> str | None:
    found = q._rows(c, "select name from teams where fm_uid = :t", t=uid)
    return found[0]["name"] if found else None


def writer_facts(c: Connection, refs: list[str], day: date) -> list[dict]:
    """For a story's notebook: the titles already won in the leagues it touches, so that no writer tells of a race that is over."""
    leagues = [int(r.split(":")[1]) for r in refs if r.startswith("competition:") and r.split(":")[1].isdigit()]
    if not leagues:
        teams = [int(r.split(":")[1]) for r in refs if r.startswith("club:")]
        teams += [int(side) for r in refs if r.startswith("match:") for side in r.split(":")[2:4]]
        for team in dict.fromkeys(teams):
            found = q._rows(c, "select f.competition_uid from fixtures f join competitions k on k.fm_uid = f.competition_uid where k.scope = 'league' "
                               "and (f.home_uid = :t or f.away_uid = :t) and f.date <= :d order by f.date desc limit 1", t=team, d=str(day))
            leagues += [found[0]["competition_uid"]] if found else []
    out = []
    for league in list(dict.fromkeys(leagues))[:LEAGUES_NAMED]:
        s = settled(c, league, day)
        name = q._rows(c, "select name from competitions where fm_uid = :c", c=league)
        if s and name:
            champions = _team_name(c, s["champions_uid"])
            out.append({"league": name[0]["name"], "already_champions": champions, "their_points": s["points"], "their_games_left": s["games_left"],
                        "most_points_any_other_club_can_reach": s["best_any_other_can_reach"], "title_won_on": s["clinched_on"],
                        "use": f"The title race in this league is over: {champions} have won it. Never write of it as open, of the leaders being caught or of a challenge for first place."})
    return out


def clinched(w) -> list:
    """A title won with games to spare, told once a season, in the first edition that can see it."""
    from app.services.story_leads import Lead

    leads = []
    for league in current_leagues(w):
        s = settled(w.c, league, w.until)
        if not s or not s["games_left"]:
            continue
        team, comp = s["champions_uid"], w.comp(league)
        titles = w.rows("select count(*) n from edition_places where competition_uid = :c and team_uid = :t and place = 1", c=league, t=team)[0]["n"]
        facts = {"competition": comp["name"], "champions": w.name(team), "points_now": s["points"], "games_left_now": s["games_left"],
                 "games_left_when_it_was_won": s["games_left_when_won"],
                 "title_won_on": s["clinched_on"], "second": w.name(s["second_uid"]), "points_clear_of_second": s["points"] - s["second_points"],
                 "most_points_any_other_club_can_reach": s["best_any_other_can_reach"], "league_titles_before_this_one": titles}
        leads.append(Lead("title_clinched", f"{w.name(team)} are champions of the {comp['name']} with {s['games_left_when_won']} games to spare",
                          date.fromisoformat(s["clinched_on"]), w.weight(league) * CLINCHED_SIZE, facts, [f"club:{team}", f"competition:{league}", f"club:{s['second_uid']}"],
                          w.beats_for(team, competition_uid=league), slug=f"{league}:{s['season_start'][:4]}"))
    return leads
