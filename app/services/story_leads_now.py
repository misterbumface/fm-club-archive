"""The news of the season as it happens: what happened, what is happening, what will happen.

A weekly paper reports its leagues. These finders read the leagues that are playing (the top leagues and the followed
clubs' leagues) and find: the round-up of each league (the cycle's results, the table, who rose and fell), runs and
crises (winning and losing runs, unbeaten and winless starts, a big club in trouble and its manager), derbies, the
coming week's big games, injuries that hurt, players far below or above their name, clubs far from where their standing
says they belong, and the scoring pace. story_leads.find_leads adds them to the other finders' leads.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from app.services import expected, features, fm_save, moods, tactics, team_tactics, tournament_leads
from app.services import world_queries as q

NOW_LEAGUES = 14  # leagues covered: the strongest top divisions, and the followed clubs' leagues
MIN_ROUND_GAMES = 3  # a league with fewer games in the cycle has no round-up
ROUND_SIZE = 1.4  # a top league's round-up is news for World Soccer (60)
WINNING_RUN, LOSING_RUN = 4, 3
START_GAMES = 4  # unbeaten or winless after this many games is news
CRISIS_STANDING = 6500  # a club this well known losing is a crisis
PREVIEW_DAYS = 14  # until the next editions
RECORD_GAMES = 2  # games against a system before a preview gives the record
INJURY_DAYS = 21
INJURY_NAME = 5000  # a player this well known out for weeks is news
FORM_GAMES = 4  # league games before a season's form counts
BIG_NAME, SMALL_NAME = 7000, 4000  # the game's world reputation of a big name and of a small one
UNDER_RATING, OVER_RATING = 6.6, 7.5  # average match ratings
EXPECTATION_GAP = 6  # places between the table and the club's standing in its league
SCORING_PACE, SCORING_FROM = 1.0, 4  # league goals a game, from four goals on
DERBY_LEVEL = 50

POSITION_WORDS = {"GK": "goalkeeper", "D (C)": "centre-back", "D (R)": "right-back", "D (L)": "left-back", "WB (R)": "right wing-back",
                  "WB (L)": "left wing-back", "DM": "defensive midfielder", "M (C)": "central midfielder", "M (R)": "right midfielder",
                  "M (L)": "left midfielder", "AM (C)": "attacking midfielder", "AM (R)": "right winger", "AM (L)": "left winger",
                  "ST (C)": "striker"}


# Positions that compete for one place in a team.
POSITION_GROUPS = {"GK": "GK", "D (C)": "DC", "D (R)": "R", "WB (R)": "R", "D (L)": "L", "WB (L)": "L", "DM": "MC", "M (C)": "MC",
                   "AM (C)": "AMC", "M (R)": "W", "AM (R)": "W", "M (L)": "W", "AM (L)": "W", "ST (C)": "ST"}
REPLACED_DAYS = 120  # players who left this long before a signing in his position
REGULAR_STARTS = 5
SAME_PLACE_PLAYERS = 4  # the players in a position a transfer story names
CONCERN_BEFORE_DAYS = 42  # a concern that ended this long before a move still explains it
LUCK_GAMES = 6  # league games with a report before expected points are news
EURO_BIG, EURO_BOTH = 7500, 6000  # a European tie is previewed when one club is this well known, or both are this
CUP_BOTH = 6500  # a domestic cup tie between two clubs this well known (the followed clubs' ties always)
INTERNATIONAL_BOTH = 7000  # an international between two nations this well known (a covered country's games always)
PREVIEW_BASE = 0.8
PREVIEWS = 16
EURO_RESULTS = 16  # results in a European round-up
EURO_NATION_GAMES = 2  # a country's clubs' European games in the cycle before they are a story
LUCK_POINTS = 5  # points above or below expected that make a story


def position_words(code: str | None) -> str | None:
    return POSITION_WORDS.get(code or "", code)


def position_picture(w, person: int, team: int, code: str | None) -> dict:
    """Who plays in a player's position at a club, from its team sheets this season: the starters there (him left out) with
    their starts and goals and any open concern, how many the club starts there, and its formation when the save gives it.
    The first team's sheets leave the youth players out (Chelsea's strikers, 31 Oct 2026)."""
    group = POSITION_GROUPS.get(code or "")
    if not group:
        return {}
    codes = [c for c, g in POSITION_GROUPS.items() if g == group]
    since = date(w.season, 7, 1)
    players = q.position_starts(w.c, team, codes, str(since), str(w.until))
    if not players:  # a calendar-year league between seasons
        since = w.until - timedelta(days=365)
        players = q.position_starts(w.c, team, codes, str(since), str(w.until))
    others = [p for p in players if p["uid"] != person][:SAME_PLACE_PLAYERS]
    injured = q.current_injuries(w.c, [p["uid"] for p in others])
    lines = []
    for p in others:
        line = f"{p['name']}: {p['starts']} starts there since {since}, {p['goals'] or 0} goals in them"
        if p["uid"] in injured:
            line += f"; injured, out until about {injured[p['uid']]['back_on']}"
        if worries := _concerns_before(w, p["uid"], w.until):
            line += "; unhappy: " + "; ".join(worries)
        lines.append(line)
    his = next((p for p in players if p["uid"] == person), None)
    tactic = w.rows("select formation from team_tactics where team_uid = :t and formation is not null order by played_on desc, id desc limit 1", t=team)
    facts = {"club": w.name(team), "position": position_words(code),
             "his_starts_there": f"{his['starts']} starts, {his['goals'] or 0} goals since {since}" if his else None,
             "who_plays_there": lines or ["nobody has started there since " + str(since)],
             "starters_there_a_game": q.starters_a_game(w.c, team, codes, str(since), str(w.until)),
             "formation": tactic[0]["formation"] if tactic else None}
    return {k: v for k, v in facts.items() if v is not None}


def transfer_context(w, person: int, to_uid: int, day: date) -> dict:
    """Where a signing fits at his new club: his position, the players in that position who left the club in the last
    months (whom he replaces), and who plays there now (position_picture)."""
    code = q.main_positions(w.c, [person]).get(person)
    if not code:
        return {}
    group = POSITION_GROUPS.get(code)
    left = w.rows("select t.person_uid, t.to_uid, t.fee, t.kind, t.date, p.name from transfers t join people p on p.fm_uid = t.person_uid "
                  "where t.from_uid = :c and t.date > :s and t.date <= :d and t.kind in (1, 2, 3, 4, 7, 8, 9) and t.person_uid != :p",
                  c=to_uid, s=str(day - timedelta(days=REPLACED_DAYS)), d=str(w.until), p=person)
    positions = q.main_positions(w.c, [r["person_uid"] for r in left])
    marks = ",".join(str(r["person_uid"]) for r in left) or "0"
    starts = {r["person_uid"]: r["starts"] or 0 for r in w.rows(
        f"select person_uid, sum(starts) starts from player_seasons where team_uid = :c and kind = 1 and season_start in (:y, :l) "
        f"and person_uid in ({marks}) group by person_uid", c=to_uid, y=w.season, l=w.season - 1)}
    # Those who mattered: a regular (five league starts in two seasons) or sold for a fee.
    gone = [r for r in left if POSITION_GROUPS.get(positions.get(r["person_uid"])) == group
            and (starts.get(r["person_uid"], 0) >= REGULAR_STARTS or r["fee"])]
    picture = position_picture(w, person, to_uid, code)
    facts = {"position": position_words(code),
             "same_position_players_who_left": [
                 f"{r['name']} ({q.TRANSFER_KINDS.get(r['kind'], 'left').lower()} to {w.name(r['to_uid']) or 'no club'}, {r['date']}"
                 + (f", £{r['fee'] / 1e6:.1f}m" if r["fee"] else "") + f", {starts.get(r['person_uid'], 0)} league starts in the last two seasons)"
                 for r in gone] or None,
             "who_plays_there_now": picture.get("who_plays_there"),
             "starters_there_a_game": picture.get("starters_there_a_game"),
             "formation": picture.get("formation"),
             "his_concerns_before_the_move": _concerns_before(w, person, day) or None}
    return {k: v for k, v in facts.items() if v is not None}


def _concerns_before(w, person: int, day: date) -> list[str]:
    """The player's named concerns (not about team-mates) open in the weeks before a move: he wanted to move on, a new deal..."""
    rows = moods.describe(w.rows("select * from person_concerns where person_uid = :p and started <= :d and (ended is null or ended >= :s)",
                                 p=person, d=str(day), s=str(day - timedelta(days=CONCERN_BEFORE_DAYS))), w.c)
    return [f"{c['text']} (from {c['started']}" + (f" to {c['ended']})" if c["ended"] else ")")
            for c in rows if c["code_key"] != fm_save.TREATED_UNFAIRLY]


def _window(w, league: int) -> tuple[str, str]:
    """The league's current season: July to June, or the calendar year."""
    start, end = q.league_window(w.c, league, w.season)
    if start.endswith("-01-01"):
        start, end = q.league_window(w.c, league, w.until.year)
    return start, min(end, str(w.until))


def current_leagues(w) -> list[int]:
    """The leagues to report: the strongest top divisions that played in the cycle, and the followed clubs' leagues."""
    if getattr(w, "_now_leagues", None) is not None:
        return w._now_leagues
    played = {r["competition_uid"]: r["n"] for r in w.rows(
        "select competition_uid, count(*) n from fixtures where date > :s and date <= :u and home_goals is not null "
        "group by competition_uid", s=str(w.since), u=str(w.until))}
    top = sorted((uid for uid, c in w.competitions.items() if c.get("scope") == "league" and c.get("level") == 1
                  and played.get(uid, 0) >= MIN_ROUND_GAMES), key=lambda uid: -w.weight(uid))[:NOW_LEAGUES]
    followed = []
    for team in w.followed_teams:
        league, _ = w.level(team, w.season)
        if league and played.get(league, 0) >= MIN_ROUND_GAMES and league not in top + followed:
            followed.append(league)
    w._now_leagues = followed + top
    return w._now_leagues


def _score_text(f: dict) -> str:
    extra = f["home_goals_after_extra_time"] is not None
    home = f["home_goals_after_extra_time"] if extra else f["home_goals"]
    away = f["away_goals_after_extra_time"] if extra else f["away_goals"]
    text = f"{home}-{away}" + (" after extra time" if extra else "")
    if f["home_penalties"] is not None:
        text += f", {f['home_penalties']}-{f['away_penalties']} on penalties"
    return text


def _result_line(w, f: dict) -> str:
    """'date: Home 1-1 Away, a draw' or '..., Away won': the outcome in words, so a writer cannot call a draw a defeat."""
    home, away = w.name(f["home_uid"]), w.name(f["away_uid"])
    extra = f["home_goals_after_extra_time"] is not None
    scored = (f["home_goals_after_extra_time"], f["away_goals_after_extra_time"]) if extra else (f["home_goals"], f["away_goals"])
    if scored[0] != scored[1]:
        outcome = f"{home if scored[0] > scored[1] else away} won"
    elif f["home_penalties"] is not None and f["home_penalties"] != f["away_penalties"]:
        outcome = f"a draw, {home if f['home_penalties'] > f['away_penalties'] else away} won on penalties"
    else:
        outcome = "a draw"
    return f"{f['date']}: {home} {_score_text(f)} {away}, {outcome}"


def _league_record(w, team: int, league: int, day: str, until_words: str = "up to and with this game") -> str | None:
    """A club's league season up to and with one game day: the record, every defeat and draw, and the run since the last
    defeat. A report once gave Chelsea a defeat at Manchester City they had won 4-0 (6 Feb 2064); a preview said they had
    "not lost all season bar one draw" (3 Feb 2064)."""
    start, _ = _window(w, league)
    games = w.rows("select * from fixtures where competition_uid = :l and (home_uid = :t or away_uid = :t) and home_goals is not null "
                   "and date >= :s and date <= :d order by date", l=league, t=team, s=start, d=day[:10])
    if not games:
        return None
    won, drawn, lost = [], [], []
    for f in games:
        mine, theirs = (f["home_goals"], f["away_goals"]) if f["home_uid"] == team else (f["away_goals"], f["home_goals"])
        (won if mine > theirs else drawn if mine == theirs else lost).append(f)
    since = next((i for i in range(len(games) - 1, -1, -1) if games[i] in lost), None)
    run = len(games) if since is None else len(games) - 1 - since
    words = (f"{w.name(team)} in this league this season, {until_words}: played {len(games)}, won {len(won)}, drawn {len(drawn)}, "
             f"lost {len(lost)}. The defeats: {'; '.join(_result_line(w, f) for f in lost) or 'none'}. "
             f"The draws: {'; '.join(_result_line(w, f) for f in drawn) or 'none'}. ")
    words += (f"Unbeaten in all {run} league games this season." if since is None
              else f"{run} league games unbeaten since the last defeat." if run else "This game was a defeat.")
    return words + " These are all of their league results that are not wins: never tell another."


def _next_in_competition(w, f: dict) -> bool:
    """Whether a game is both clubs' next one in its competition: a preview tells the next game, with the form and the
    table as they will stand on the day (3 Feb 2064 previewed Chelsea v Aston Villa of 13 Feb, a Newcastle league game in
    between). Games in other competitions may come first."""
    return not w.rows("select 1 from fixtures where competition_uid = :c and (home_uid in (:h, :a) or away_uid in (:h, :a)) "
                      "and date > :u and date < :d and id != :f limit 1", c=f["competition_uid"], h=f["home_uid"], a=f["away_uid"],
                      u=str(w.until), d=str(f["date"]), f=f["id"])


def _manager(w, team: int) -> str | None:
    row = w.rows("select name, staff_since from people where staff_job = 'Manager' and staff_team_uid = :t limit 1", t=team)
    if not row:
        return None
    since = f", in charge since {row[0]['staff_since']}" if row[0]["staff_since"] else ""
    return f"{row[0]['name']}{since}"


def _table(w, league: int) -> tuple[list[dict], dict[int, int], dict[int, int]]:
    """The table now, each team's place now and at the start of the cycle."""
    start, end = _window(w, league)
    now = q.league_table(w.c, league, start, end)
    before = q.league_table(w.c, league, start, str(w.since)) if start <= str(w.since) else []
    return now, {r["team_uid"]: i for i, r in enumerate(now, 1)}, {r["team_uid"]: i for i, r in enumerate(before, 1)}


PHASE_TEAMS = 4  # a continental competition's stage is a league phase when its table has this many teams


def _phase_table(w, league: int, tables: dict) -> tuple[list[dict], dict[int, int], dict[int, int]]:
    """The table of a competition for a preview: a current league's, or a continental competition's league phase, counted
    once per cycle. A writer told Shamrock Rovers were in the knockout rounds, 29th of 36 with six points (1 Nov 2026)."""
    if league not in tables and w.comp(league).get("scope") == "continental":
        now = q.league_table(w.c, league, f"{w.season}-07-01", str(w.until))
        tables[league] = (now, {r["team_uid"]: i for i, r in enumerate(now, 1)}, {}) if len(now) >= PHASE_TEAMS else ([], {}, {})
    return tables.get(league, ([], {}, {}))


def _place_words(w, league: int, table: list[dict], place: dict[int, int], team: int, form: bool = True) -> str:
    row = table[place[team] - 1]
    words = f"{place[team]} of {len(table)} in the league phase" if w.comp(league).get("scope") == "continental" else f"{place[team]}"
    words += f", {row['points']} points from {row['played']} games"
    words += f", form {''.join(row['form'])}" if form else ""
    zone = q.zone_of(_zones(w, league), table, place[team])
    return words + (f"; {zone}" if zone else "")


def _zones(w, league: int) -> dict | None:
    """The league's promotion and relegation places as its past seasons show them (world_queries.league_zones), once a
    cycle. None for a continental competition or a cup."""
    cache = w.__dict__.setdefault("_league_zones", {})
    if league not in cache:
        cache[league] = q.league_zones(w.c, league, w.season) if w.comp(league).get("scope") == "league" else None
    return cache[league]


def _league_rules(w, league: int) -> str | None:
    return q.zone_words(_zones(w, league)) if w.comp(league).get("scope") == "league" else None


def _european_seasons(w, team: int) -> int:
    return w.rows("select count(distinct season_start) n from player_seasons where team_uid = :t and kind = 3 and season_start <= :s",
                  t=team, s=w.season)[0]["n"] or 0


def _row_text(w, place: int, r: dict) -> str:
    return (f"{place}. {w.name(r['team_uid'])}: {r['points']} points from {r['played']} games "
            f"({r['won']} won, {r['drawn']} drawn, {r['lost']} lost, goal difference {r['difference']:+d})")


# -- what happened --------------------------------------------------------------------------------------------------------
MATCHDAY_GAP_DAYS = 4  # a round's games lie within this many days of its first
BIG_GAMES = 6  # games told in full in a match-day article; the rest are results


def matchdays(games: list[dict]) -> list[list[dict]]:
    """A league's games of the cycle in rounds: a round ends when a club plays again (29 Dec and 1 Jan are two rounds,
    three days apart), or when a game comes more than MATCHDAY_GAP_DAYS after the round's first."""
    rounds: list[list[dict]] = []
    teams: set[int] = set()
    for f in sorted(games, key=lambda f: str(f["date"])):
        day = date.fromisoformat(str(f["date"])[:10])
        if (rounds and not {f["home_uid"], f["away_uid"]} & teams
                and (day - date.fromisoformat(str(rounds[-1][0]["date"])[:10])).days <= MATCHDAY_GAP_DAYS):
            rounds[-1].append(f)
        else:
            rounds.append([f])
            teams = set()
        teams |= {f["home_uid"], f["away_uid"]}
    return rounds


REBOUND_SECONDS = 5  # a goal this soon after the same player's blocked or saved shot is the rebound (Rodrigão, 9 Feb 2064)


def _how_goals_went_in(w, f: dict) -> list[str] | None:
    """Each goal's kind of shot and where it went in, from the match's shot records (docs/goal-labels.md): the kind from
    the game's highlight events (header, volley, drive, finish), the height and line from where the ball crossed the line,
    a rebound from the scorer's own blocked or saved shot just before. None for a match without shot records."""
    shots = w.rows("select s.person_uid, s.minute, s.second, s.outcome, s.across, s.height, s.kind, s.range, s.outside_box, s.side, p.name from match_shots s "
                   "left join people p on p.fm_uid = s.person_uid where s.fixture_id = :f order by s.minute, s.second, s.seq", f=f["id"])
    lines = []
    for shot in (s for s in shots if s["outcome"] == "goal"):
        at = shot["minute"] * 60 + shot["second"]
        rebound = any(o["person_uid"] == shot["person_uid"] and o["outcome"] in ("blocked", "saved")
                      and 0 <= at - (o["minute"] * 60 + o["second"]) <= REBOUND_SECONDS for o in shots)
        height, across = shot["height"], abs(shot["across"])
        where = ("along the ground" if height < 0.25 else "low" if height < 0.8 else "at mid height" if height < 1.6
                 else "high, just under the bar" if height >= 2.1 else "high")
        where += ", through the middle" if across < 1.2 else ", near a post" if across >= 3.0 else ""
        kind = {"header": "a header", "volley": "a volley", "drive": "a drive (struck low and hard)", "finish": "a shot with the foot"}.get(
            shot["kind"] or "", "the kind of shot is not recorded")
        if shot["range"] == "close":
            kind += " from close range"
        elif shot["outside_box"] == 1:
            kind += " from outside the box"
        team = w.name(f["home_uid"] if shot["side"] == 0 else f["away_uid"])
        lines.append(f"{shot['name'] or 'unknown'} ({team}), {shot['minute'] + 1}' on the match clock: {kind}, {where}"
                     + ("; from the rebound of his own blocked or saved shot" if rebound else ""))
    if not lines:
        return None
    return lines + ["From the match's shot records: say only these about how a goal went in (the kind of shot, close range or "
                    "outside the box when given, its height, the middle or a post). No exact distance in metres or yards, and "
                    "the build-up (a cross, a free kick) is not recorded: never tell them. A goal with no range given: say "
                    "nothing of its range."]


def _game_in_full(w, f: dict, place: dict[int, int]) -> dict:
    goals = w.rows("select g.minute, g.added_time, g.side, g.kind, p.name from goals g left join people p on p.fm_uid = g.scorer_uid "
                   "where g.fixture_id = :f and g.kind in (0, 1, 2) order by g.minute, g.seq", f=f["id"])
    best = w.rows("select p.name from match_reports r join people p on p.fm_uid = r.player_of_the_match_uid where r.fixture_id = :f", f=f["id"])
    game = {"result": _result_line(w, f),
            "goals": [f"{('own goal by ' if g['kind'] == 1 else '') + (g['name'] or 'unknown')} {g['minute']}"
                      f"{'+' + str(g['added_time']) if g['added_time'] else ''}'{' (pen)' if g['kind'] == 2 else ''} "
                      f"({w.name(f['home_uid'] if g['side'] == 0 else f['away_uid'])})" for g in goals] or None,
            "how_the_goals_went_in": _how_goals_went_in(w, f),
            "player_of_the_match": best[0]["name"] if best else None, "attendance": f["attendance"] or None,
            "places_now": f"{w.name(f['home_uid'])} {place.get(f['home_uid'], '?')}, {w.name(f['away_uid'])} {place.get(f['away_uid'], '?')}"}
    return {k: v for k, v in game.items() if v is not None}


def round_ups(w) -> list:
    """One long article per league and match day (27 Oct 2026: one piece on all the big games of a round, not a dozen small
    ones): the big games in full (goals, player of the match, places), the other results, and the table after it. The
    small stories of its games (a performance, a sending-off, a big win) are folded in (story_leads.fold_into_matchdays)."""
    from app.services.story_leads import Lead

    leads = []
    for league in current_leagues(w):
        comp = w.comp(league)
        start, end = _window(w, league)
        games = w.rows("select * from fixtures where competition_uid = :c and date > :s and date <= :u and home_goals is not null "
                       "order by date", c=league, s=max(start, str(w.since)), u=end)
        table, place, before = _table(w, league)
        if not table:
            continue
        for day_games in matchdays(games):
            if len(day_games) < min(MIN_ROUND_GAMES, len(table) // 2):  # a round of a small league has fewer games
                continue
            ranked = sorted(day_games, key=lambda f: -((w.rep(f["home_uid"]) or 0) + (w.rep(f["away_uid"]) or 0)
                                                       + 3000 * (w.followed(f["home_uid"], f["away_uid"]))
                                                       + 400 * ((f["home_goals"] or 0) + (f["away_goals"] or 0))))
            first, last = str(day_games[0]["date"])[:10], str(day_games[-1]["date"])[:10]
            moves = [(before[t] - place[t], t) for t in place if t in before]
            leader, leader_before = table[0]["team_uid"], next((t for t, p in before.items() if p == 1), None)
            facts = {"competition": comp["name"], "season": q.season_label(int(start[:4])) if not start.endswith("01-01") else start[:4],
                     "match_day": first if first == last else f"{first} to {last}",
                     "big_games": [_game_in_full(w, f, place) for f in ranked[:BIG_GAMES]],
                     "other_results": [_result_line(w, f) for f in ranked[BIG_GAMES:]] or None,
                     "games_played_by_the_leaders": table[0]["played"],
                     "league_rules": _league_rules(w, league),
                     "table_top": [_row_text(w, i, r) for i, r in enumerate(table[:5], 1)],
                     "table_bottom": [_row_text(w, len(table) - 2 + i, r) for i, r in enumerate(table[-3:])],
                     "leader": w.name(leader), "leader_a_cycle_ago": w.name(leader_before) if leader_before and leader_before != leader else None,
                     "climbed": [f"{w.name(t)} from {before[t]} to {place[t]}" for gain, t in sorted(moves, reverse=True) if gain >= 3][:4] or None,
                     "fell": [f"{w.name(t)} from {before[t]} to {place[t]}" for gain, t in sorted(moves) if gain <= -3][:4] or None,
                     "unbeaten": [w.name(r["team_uid"]) for r in table if r["played"] >= 3 and not r["lost"]] or None,
                     "without_a_win": [w.name(r["team_uid"]) for r in table if r["played"] >= 3 and not r["won"]] or None}
            score = w.weight(league) * 10 * ROUND_SIZE
            leads.append(Lead("league_round", f"{comp['name']}, match day of {first}: {_result_line(w, ranked[0]).split(': ', 1)[1]}",
                              date.fromisoformat(last), score, {k: v for k, v in facts.items() if v is not None},
                              [f"competition:{league}", f"club:{leader}"] + [f"match:{f['date']}:{f['home_uid']}:{f['away_uid']}" for f in day_games],
                              w.beats_for(*(r["team_uid"] for r in table), competition_uid=league), slug=f"{league}:{first}"))
    return leads


def derbies(w) -> list:
    """Every derby of the cycle in a senior competition: the clubs count each other rivals (50 or more, either way)."""
    from app.services.story_leads import SENIOR, Lead

    senior = ",".join(str(u) for u, c in w.competitions.items() if c.get("scope") in SENIOR) or "0"
    leads = []
    for f in w.rows(f"select f.*, max(r.level) rivalry from fixtures f join club_relations r on r.relation = 'rival' and "
                    f"((r.club_uid = f.home_uid and r.subject_uid = f.away_uid) or (r.club_uid = f.away_uid and r.subject_uid = f.home_uid)) "
                    f"where f.competition_uid in ({senior}) and f.date > :s and f.date <= :u and f.home_goals is not null "
                    f"group by f.id having rivalry >= :d", s=str(w.since), u=str(w.until), d=DERBY_LEVEL):
        comp = w.comp(f["competition_uid"])
        goals = w.rows("select g.minute, g.added_time, g.side, g.kind, p.name from goals g left join people p on p.fm_uid = g.scorer_uid "
                       "where g.fixture_id = :f and g.kind in (0, 1, 2) order by g.minute, g.seq", f=f["id"])
        rivalry = q.rivalry(w.c, f["home_uid"], f["away_uid"]) or q.rivalry(w.c, f["away_uid"], f["home_uid"]) or {}
        meetings = w.rows("select * from fixtures where ((home_uid = :h and away_uid = :a) or (home_uid = :a and away_uid = :h)) "
                          "and home_goals is not null and date < :d order by date desc limit 4", h=f["home_uid"], a=f["away_uid"], d=f["date"])
        facts = {"competition": comp["name"], "date": str(f["date"]), "home": w.name(f["home_uid"]), "away": w.name(f["away_uid"]),
                 "score": _score_text(f), "rivalry": f"{rivalry.get('level', f['rivalry'])}/100" + (f", {rivalry['reason'].lower()}" if rivalry.get("reason") else ""),
                 "goals": [f"{('own goal by ' if g['kind'] == 1 else '') + (g['name'] or 'unknown')} {g['minute']}{'+' + str(g['added_time']) if g['added_time'] else ''}'{' (pen)' if g['kind'] == 2 else ''} "
                           f"({w.name(f['home_uid'] if g['side'] == 0 else f['away_uid'])})" for g in goals] or None,
                 "how_the_goals_went_in": _how_goals_went_in(w, f),
                 "earlier_meetings": [_result_line(w, m) for m in meetings] or None}
        # the rivalry's level, the bigger club's standing (Liverpool v Everton over a lower-league derby) and an upset
        # (the away side or the smaller club wins): Liverpool 1-3 Everton (15 Dec 2063) scored 55 and lost to a hat-trick
        big = max(w.rep(f["home_uid"]) or 0, w.rep(f["away_uid"]) or 0)
        home_bigger = (w.rep(f["home_uid"]) or 0) >= (w.rep(f["away_uid"]) or 0)
        upset = (f["home_goals"] < f["away_goals"]) if home_bigger else (f["home_goals"] > f["away_goals"])
        score = w.weight(f["competition_uid"]) * 10 * (0.75 + f["rivalry"] / 250) * (1 + big / 20_000) * (1.25 if upset else 1.0)
        leads.append(Lead("derby", f"Derby: {w.name(f['home_uid'])} {_score_text(f)} {w.name(f['away_uid'])} ({comp['name']})",
                          date.fromisoformat(str(f["date"])), score, {k: v for k, v in facts.items() if v is not None},
                          [f"match:{f['date']}:{f['home_uid']}:{f['away_uid']}", f"club:{f['home_uid']}", f"club:{f['away_uid']}"],
                          w.beats_for(f["home_uid"], f["away_uid"], competition_uid=f["competition_uid"])))
    return leads


# -- what is happening ----------------------------------------------------------------------------------------------------
def runs(w) -> list:
    """Clubs on a winning run, a well-known club on a losing run or in the bottom three (and its manager under pressure),
    unbeaten and winless starts: each extended in this cycle."""
    from app.services.story_leads import Lead

    leads = []
    for league in current_leagues(w):
        start, end = _window(w, league)
        games = w.rows("select * from fixtures where competition_uid = :c and date >= :s and date <= :u and home_goals is not null "
                       "order by date", c=league, s=start, u=end)
        by_team: dict[int, list] = defaultdict(list)
        for f in games:
            for team in (f["home_uid"], f["away_uid"]):
                by_team[team].append(f)
        table, place, _ = _table(w, league)
        comp = w.comp(league)
        for team, played in by_team.items():
            if str(played[-1]["date"]) <= str(w.since):
                continue  # nothing new in this cycle
            results = [q.result_for(f, team)[2] for f in played]
            last = results[-1]
            streak = next((i for i, r in enumerate(reversed(results)) if r != last), len(results))
            fame = (w.rep(team) or 3000) / 8000
            angle = None
            if last == "W" and streak >= WINNING_RUN:
                angle, size = f"{streak} league wins in a row", 0.45 + 0.08 * streak
            elif last == "L" and streak >= LOSING_RUN and ((w.rep(team) or 0) >= CRISIS_STANDING or w.followed(team)):
                angle, size = f"{streak} league defeats in a row", 0.6 + 0.1 * streak
            elif len(results) >= START_GAMES + 1 and "L" not in results:
                angle, size = f"unbeaten in the first {len(results)} league games", 0.4 + 0.05 * len(results)
            elif len(results) >= START_GAMES and "W" not in results:
                angle, size = f"no win in the first {len(results)} league games", 0.45 + 0.05 * len(results)
            elif place.get(team, 0) > len(table) - 3 and len(results) >= START_GAMES and (w.rep(team) or 0) >= CRISIS_STANDING:
                angle, size = f"in the bottom three after {len(results)} games", 0.7
            if angle is None:
                continue
            row = table[place[team] - 1] if team in place else None
            facts = {"club": w.name(team), "competition": comp["name"], "run": angle, "form": "".join(results[-6:]),
                     "place": place.get(team), "points": row and row["points"], "played": len(results),
                     "recent_results": [_result_line(w, f) for f in played[-5:]], "manager": _manager(w, team)}
            if last == "L":
                facts["pressure_on_the_manager"] = True
            leads.append(Lead("run", f"{w.name(team)}: {angle} ({comp['name']})", w.until, w.weight(league) * 10 * size * fame,
                              {k: v for k, v in facts.items() if v is not None}, [f"club:{team}", f"competition:{league}"],
                              w.beats_for(team, competition_uid=league), slug=f"{team}:{w.until}"))
    return sorted(leads, key=lambda lead: -lead.score)[:16]


def expectations(w) -> list:
    """Clubs far above or below the place their standing gives them in their league (the game's reputation), after a few
    rounds: the surprise packages and the giants in trouble."""
    from app.services.story_leads import Lead

    leads = []
    for league in current_leagues(w):
        table, place, _ = _table(w, league)
        if not table or table[0]["played"] < FORM_GAMES:
            continue
        expected = {t: i for i, t in enumerate(sorted(place, key=lambda t: -(w.rep(t) or 0)), 1)}
        found = []
        for team, actual in place.items():
            gap = expected[team] - actual
            if abs(gap) >= EXPECTATION_GAP:
                found.append((abs(gap), gap, team))
        for _, gap, team in sorted(found, reverse=True)[:2]:
            row = table[place[team] - 1]
            angle = "far above" if gap > 0 else "far below"
            facts = {"club": w.name(team), "competition": _comp_name(w, league), "place": place[team],
                     "place_its_standing_suggests": expected[team], "points": row["points"], "played": row["played"],
                     "form": "".join(row["form"]), "manager": _manager(w, team)}
            leads.append(Lead("expectations", f"{w.name(team)} {angle} expectations: {place[team]} in the {_comp_name(w, league)}, "
                                              f"their standing says {expected[team]}", w.until,
                              w.weight(league) * 10 * (0.45 + 0.03 * abs(gap)),
                              {k: v for k, v in facts.items() if v is not None}, [f"club:{team}", f"competition:{league}"],
                              w.beats_for(team, competition_uid=league) | {"numbers"}, slug=f"{team}:{w.until}"))
    return leads


CHECKIN_WEIGHT = 4.5  # the higher league of the move this strong (the big European leagues): the clubs worth checking on
CHECKIN_POINTS = ((0.3, "about a third of the season played"), (0.55, "just past halfway"), (0.8, "the run-in"))
CHECKINS = 2  # at most this many check-ins in a cycle: flavour, not a column every week
FALLEN_SEASONS = 3  # a big club stays a fallen giant this many seasons below its old level
FALLEN_YEARS, FALLEN_ABOVE = 10, 6  # ... when it spent this many of the last ten seasons above its level now
FALLEN_RANK = 3  # ... and is among the best known of its new league
NEW_BOYS_RANK = 3  # a promoted club among the least known of its new league
NEW_BOYS_SEASONS = 2  # ... that played at this level at most this many of the last ten seasons


def check_ins(w) -> list:
    """Every so often, how a big club that went down is doing below, or a small club that came up is doing among the
    big ones: once at each of three points of the league season (a third, past halfway, the run-in), at most a couple
    in a cycle. The table, the form, last season, the manager, the scorers and the regulars."""
    from app.services.story_leads import Lead

    seasons = [w.season - k for k in range(FALLEN_YEARS + 1)]
    for season in seasons:
        w.level(0, season)
    now = w._levels[w.season]
    by_league: dict[int, list[int]] = defaultdict(list)
    for team, (league, _) in now.items():
        by_league[league].append(team)
    found = []
    for team, (league, level) in now.items():
        if not level or not league:
            continue
        history = [w._levels[s].get(team, (None, None))[1] for s in seasons[1:]]
        last = history[0]
        known = sorted(by_league[league], key=lambda t: -(w.rep(t) or 0))
        rank = known.index(team) + 1
        if last and last < level:  # down: when, from where, and was it a big club there
            down_for = 1
        else:
            down_for = next((k for k, lv in enumerate(history[:FALLEN_SEASONS], 1) if lv and lv < level and all(
                x == level for x in history[:k - 1])), None)
        angle = None
        if down_for and sum(1 for lv in history if lv and lv < level) >= FALLEN_ABOVE and rank <= FALLEN_RANK:
            old_league = w._levels[w.season - down_for].get(team, (None, None))[0]
            if w.weight(old_league) >= CHECKIN_WEIGHT:
                angle = ("fallen", old_league, down_for)
        elif last and last > level and rank > len(known) - NEW_BOYS_RANK and w.weight(league) >= CHECKIN_WEIGHT:
            here_before = sum(1 for lv in history[1:] if lv and lv <= level)
            if here_before <= NEW_BOYS_SEASONS:
                angle = ("new_boys", w._levels[w.season - 1].get(team, (None, None))[0], here_before)
        if angle is None:
            continue
        start, end = _window(w, league)
        table = q.league_table(w.c, league, start, end)
        before = {r["team_uid"]: r for r in q.league_table(w.c, league, start, str(w.since))} if start <= str(w.since) else {}
        place = {r["team_uid"]: i for i, r in enumerate(table, 1)}
        if team not in place:
            continue
        row, total = table[place[team] - 1], 2 * (len(table) - 1)
        played_before = before.get(team, {}).get("played", 0)
        point = next(((i, words) for i, (share, words) in enumerate(CHECKIN_POINTS) if played_before < share * total <= row["played"]), None)
        if point is None:
            continue
        found.append((team, league, angle, point, table, place, row, total))
    leads = []
    for team, league, (kind, other, count), (index, stage), table, place, row, total in found:
        club, comp = w.name(team), _comp_name(w, league)
        if kind == "fallen":
            story = (f"a big club relegated from the {_comp_name(w, other)}: {'their first' if count == 1 else _nth(count)} season "
                     f"in the {comp}")
        else:
            story = (f"promoted from the {_comp_name(w, other)} last season, among the least known clubs in the {comp}; "
                     f"{count} of the previous ten seasons at this level")
        facts = {"club": club, "competition": comp, "the_story": story, "how_far_in": f"{stage}: {row['played']} of {total} games",
                 "place": _place_words(w, league, table, place, team), "points_behind_first": table[0]["points"] - row["points"],
                 "points_above_last": row["points"] - table[-1]["points"],
                 "league_rules": _league_rules(w, league),
                 "last_season": _last_finish(w, team), "manager": _manager(w, team),
                 "top_scorers": _team_scorers(w, team, league), "regulars": _regular_lines(w, team)}
        size = 0.55 + (0.15 if w.followed(team) else 0) + 0.05 * index
        leads.append(Lead("check_in", f"{club}: {stage} in the {comp}, {place[team]} of {len(table)}", w.until,
                          w.weight(league) * 10 * size, {k: v for k, v in facts.items() if v not in (None, [], "")},
                          [f"club:{team}", f"competition:{league}"], w.beats_for(team, competition_uid=league),
                          slug=f"{team}:{w.season}:{index}"))
    return sorted(leads, key=lambda lead: -lead.score)[:CHECKINS]


def _nth(n: int) -> str:
    return {2: "second", 3: "third", 4: "fourth"}.get(n, f"{n}th")


def _last_finish(w, team: int) -> str | None:
    """Where the club finished its league last season."""
    league, _ = w.level(team, w.season - 1)
    if not league:
        return None
    start, end = q.league_window(w.c, league, w.season - 1)
    table = q.league_table(w.c, league, start, end)
    spot = next((i for i, r in enumerate(table, 1) if r["team_uid"] == team), None)
    return f"{spot} of {len(table)} in the {_comp_name(w, league)}" if spot else None


def _team_scorers(w, team: int, league: int) -> list[str]:
    rows = w.rows("select p.name, s.goals, s.starts + s.substitute_appearances games from player_seasons s join people p on p.fm_uid = s.person_uid "
                  "where s.team_uid = :t and s.league_uid = :l and s.season_start = :y and s.kind = 1 and s.goals > 0 order by s.goals desc limit 3",
                  t=team, l=league, y=w.season)
    return [f"{r['name']}: {r['goals']} league goals in {r['games']} games" for r in rows]


def _regular_lines(w, team: int) -> list[str]:
    """The team's regulars from its team sheets, most starts first; empty when the archive cannot tell."""
    regulars = q.regulars(w.c, team, w.until) or {}
    return [f"{p['name']} ({q.regular_words(p)})" for p in sorted(regulars.values(), key=lambda p: -p["starts"])][:8]


def form_players(w) -> list:
    """Big names far below their reputation this season, and small names far above it: league average ratings after a few
    games in a covered league."""
    from app.services.story_leads import Lead

    leagues = ",".join(str(u) for u in current_leagues(w)) or "0"
    leads = []
    for r in w.rows(f"select s.person_uid, s.team_uid, s.league_uid, s.starts + s.substitute_appearances games, s.goals, s.assists, "
                    f"s.rating_total, s.rated_appearances, p.name, p.birth_date, p.reputation_world from player_seasons s "
                    f"join people p on p.fm_uid = s.person_uid where s.kind = 1 and s.season_start = :y and s.league_uid in ({leagues}) "
                    f"and s.rated_appearances >= :n", y=w.season, n=FORM_GAMES):
        rating = r["rating_total"] / r["rated_appearances"] / 10
        star = r["reputation_world"] or 0
        if star >= BIG_NAME and rating <= UNDER_RATING:
            kind, size = "underperformer", 0.5 + (UNDER_RATING - rating) + (star - BIG_NAME) / 6000
        elif 0 < star <= SMALL_NAME and rating >= OVER_RATING:
            kind, size = "overperformer", 0.45 + (rating - OVER_RATING) + (SMALL_NAME - star) / 8000
        else:
            continue
        position = position_words(q.main_positions(w.c, [r["person_uid"]]).get(r["person_uid"]))
        facts = {"player": r["name"], "age": q.age_on(r["birth_date"], w.until), "club": w.name(r["team_uid"]),
                 "competition": w.comp(r["league_uid"]).get("name"), "position": position, "league_games": r["games"],
                 "goals": r["goals"], "assists": r["assists"], "average_rating": round(rating, 2),
                 "standing": "a big name" if kind == "underperformer" else "little known"}
        what = "below his name" if kind == "underperformer" else "far above his name"
        leads.append(Lead(kind, f"{r['name']} ({w.name(r['team_uid'])}) {what}: {rating:.2f} average after {r['rated_appearances']} games",
                          w.until, w.weight(r["league_uid"]) * 10 * size, {k: v for k, v in facts.items() if v is not None},
                          [f"person:{r['person_uid']}", f"club:{r['team_uid']}"],
                          w.beats_for(r["team_uid"], competition_uid=r["league_uid"]) | {"numbers"}))
    best = {}
    for lead in sorted(leads, key=lambda lead: -lead.score):
        best.setdefault(lead.kind, []).append(lead)
    return [lead for kind in best for lead in best[kind][:3]]


def stat_watch(w) -> list:
    """The scoring pace: each covered league's top scorer when he scores a goal a game or more, from four goals on."""
    from app.services.story_leads import Lead

    leads = []
    for league in current_leagues(w):
        start, end = _window(w, league)
        top = w.rows("select g.scorer_uid, p.name, count(*) goals, max(f.date) last from goals g join fixtures f on f.id = g.fixture_id "
                     "join people p on p.fm_uid = g.scorer_uid where f.competition_uid = :c and f.date >= :s and f.date <= :u "
                     "and g.kind in (0, 2) group by g.scorer_uid order by goals desc limit 3", c=league, s=start, u=end)
        if not top or top[0]["goals"] < SCORING_FROM or str(top[0]["last"]) <= str(w.since):
            continue
        r = top[0]
        games = w.rows("select sum(starts + substitute_appearances) n, max(team_uid) team from player_seasons where person_uid = :p "
                       "and kind = 1 and season_start = :y and league_uid = :c", p=r["scorer_uid"], y=w.season, c=league)[0]
        if not games["n"] or r["goals"] / games["n"] < SCORING_PACE:
            continue
        facts = {"player": r["name"], "club": w.name(games["team"]), "competition": _comp_name(w, league), "league_goals": r["goals"],
                 "league_games": games["n"], "goals_a_game": round(r["goals"] / games["n"], 2),
                 "chasers": [f"{x['name']} {x['goals']}" for x in top[1:]] or None}
        leads.append(Lead("stat_watch", f"{r['name']}: {r['goals']} {_comp_name(w, league)} goals in {games['n']} games", w.until,
                          w.weight(league) * 10 * (0.5 + 0.15 * r["goals"] / games["n"]), {k: v for k, v in facts.items() if v is not None},
                          [f"person:{r['scorer_uid']}", f"club:{games['team']}"],
                          w.beats_for(games["team"], competition_uid=league) | {"numbers"}, slug=f"{r['scorer_uid']}:{w.until}"))
    return leads


def injury_blows(w) -> list:
    """Well-known players injured in the cycle and out for weeks: how long, and how many of the club's games they miss."""
    from app.services.story_leads import Lead

    leads = []
    for r in w.rows("select i.*, p.name, p.team_uid, p.reputation_world from injuries i join people p on p.fm_uid = i.person_uid "
                    "where i.current = 1 and i.injured_on > :s and i.injured_on <= :u and i.days >= :d",
                    s=str(w.since), u=str(w.until), d=INJURY_DAYS):
        star = r["reputation_world"] or 0
        team = r["team_uid"]
        if not team or (star < INJURY_NAME and not w.followed(team)):
            continue
        back = date.fromisoformat(str(r["injured_on"])[:10]) + timedelta(days=r["days"])
        missed = w.rows("select count(*) n from fixtures where (home_uid = :t or away_uid = :t) and date > :u and date < :b",
                        t=team, u=str(w.until), b=str(back))[0]["n"]
        season = w.rows("select sum(starts + substitute_appearances) games, sum(goals) goals from player_seasons where person_uid = :p "
                        "and season_start = :y and kind in (1, 2, 3)", p=r["person_uid"], y=w.season)[0]
        position = position_words(q.main_positions(w.c, [r["person_uid"]]).get(r["person_uid"]))
        facts = {"player": r["name"], "club": w.name(team), "position": position, "injury": r["injury"], "injured_on": str(r["injured_on"]),
                 "expected_out_days": r["days"], "back_about": str(back), "club_games_he_misses": missed,
                 "this_season": f"{season['games'] or 0} games, {season['goals'] or 0} goals"}
        league, _ = w.level(team, w.season)
        leads.append(Lead("injury_blow", f"{r['name']} ({w.name(team)}) out about {r['days']} days: {r['injury']}", w.until,
                          w.weight(league) * 10 * (0.35 + star / 20000 + min(r["days"], 120) / 300),
                          {k: v for k, v in facts.items() if v is not None}, [f"person:{r['person_uid']}", f"club:{team}"],
                          w.beats_for(team, competition_uid=league)))
    return sorted(leads, key=lambda lead: -lead.score)[:8]


# -- what will happen -----------------------------------------------------------------------------------------------------
def previews(w) -> list:
    """The next two weeks' big games in the covered leagues and continental competitions: the followed clubs' games, derbies,
    and games between two clubs of the top six, with the table, the form, who is injured and the last meetings."""
    from app.services.story_leads import Lead

    tables = {league: _table(w, league) for league in current_leagues(w)}
    continental = _continental(w)
    cups = [u for u, c in w.competitions.items() if c.get("scope") == "cup"]
    internationals = [u for u, c in w.competitions.items() if c.get("scope") == "national_teams"]
    covered = {w.comp(league).get("nation_uid") for league in tables} - {None}
    comps = ",".join(str(u) for u in list(tables) + continental + cups + internationals) or "0"
    leads, knockout_ties = [], defaultdict(list)
    # the paper's own day counts: its games are still to play when it comes out (26 Feb 2064: the two second legs of that
    # Tuesday fell out of the Champions League preview, and no paper previewed them)
    for f in w.rows(f"select * from fixtures where competition_uid in ({comps}) and home_goals is null and date >= :u and date <= :e "
                    f"order by date", u=str(w.until), e=str(w.until + timedelta(days=PREVIEW_DAYS))):
        home, away, league = f["home_uid"], f["away_uid"], f["competition_uid"]
        table, place, _ = _phase_table(w, league, tables)
        derby = max((q.rivalry(w.c, home, away) or {}).get("level", 0), (q.rivalry(w.c, away, home) or {}).get("level", 0))
        top = place.get(home, 99) <= 6 and place.get(away, 99) <= 6
        bottom = _bottom_clash(w, league, table, place, home, away)
        big, both = max(w.rep(home) or 0, w.rep(away) or 0), min(w.rep(home) or 0, w.rep(away) or 0)
        european = league in continental and (big >= EURO_BIG or both >= EURO_BOTH)
        national = league in internationals and w.team(home).get("age_limit") is None and (
            {w.team(home).get("nation_uid"), w.team(away).get("nation_uid")} & covered or both >= INTERNATIONAL_BOTH)
        knockout = _tie(w, f)
        if not (w.followed(home, away) or derby >= DERBY_LEVEL or top or bottom or european or national or knockout
                or (league in cups and both >= CUP_BOTH)):
            continue
        if not _next_in_competition(w, f):
            continue
        facts = {"competition": _comp_name(w, league), "date": str(f["date"]), "home": w.name(home), "away": w.name(away),
                 "derby": derby >= DERBY_LEVEL or None, "league_rules": _league_rules(w, league) if league in tables else None,
                 **_own_league_facts(w, league, (("home", home), ("away", away)))}
        for side, team in (("home", home), ("away", away)):
            if team in place:
                facts[f"{side}_place"] = _place_words(w, league, table, place, team)
            if league in tables:
                facts[f"{side}_league_season"] = _league_record(w, team, league, str(w.until), "so far")
            standing = next((t for t in xg_table(w, league) if t["team_uid"] == team), None) if league in tables else None
            if standing and standing["games"] >= LUCK_GAMES:
                facts[f"{side}_expected_goals_and_points"] = expected.words(standing)
            injured = _injured_regulars(w, team)
            if injured:
                facts[f"{side}_injured"] = injured
        meetings = w.rows("select * from fixtures where ((home_uid = :h and away_uid = :a) or (home_uid = :a and away_uid = :h)) "
                          "and home_goals is not null order by date desc limit 3", h=home, a=away)
        if meetings:
            facts["last_meetings"] = [_result_line(w, m) for m in meetings]
        size = PREVIEW_BASE + (0.25 if derby >= DERBY_LEVEL else 0) + (0.15 if top else 0) + (0.2 if league in continental else 0)
        if knockout:  # every knockout game of a big cup, the later rounds bigger
            facts.update(knockout)
            size += 0.3 * KNOCKOUT_SIZE.get(knockout["round"].split(", ")[1], 1.0)
        bonus, drama = _drama(w, f, table, place)
        size += bonus
        if (date.fromisoformat(str(f["date"])[:10]) - w.until).days > NEAR_DAYS:
            size *= FAR_GAME  # the next cycle previews it closer to the day
        facts.update(drama)
        if knockout and knockout["round"].split(", ")[1] not in SOLO_ROUNDS:  # the round's ties in one piece (knockout_round_preview)
            near = (date.fromisoformat(str(f["date"])[:10]) - w.until).days <= NEAR_DAYS  # later ties: the next edition's piece
            if near and not w.followed(home, away):  # the followed club's tie has its own club preview too
                knockout_ties[league].append((f, {k: v for k, v in facts.items() if v is not None}, w.weight(league) * 10 * size))
            continue
        if knockout:
            facts["region"] = region_of(_comp_name(w, league))
        leads.append(Lead("tie_preview" if knockout else "preview", f"Coming up: {w.name(home)} v {w.name(away)}, {f['date']} ({_comp_name(w, league)})",
                          w.until, w.weight(league) * 10 * size, {k: v for k, v in facts.items() if v is not None},
                          [f"match:{f['date']}:{home}:{away}", f"club:{home}", f"club:{away}"],
                          w.beats_for(home, away, competition_uid=league)))
    leads = sorted(leads, key=lambda lead: -lead.score)[:PREVIEWS]
    for lead in leads:  # the expected elevens, from the team sheets
        _, day, home, away = lead.refs[0].split(":")
        elevens = {}
        for side, team, other in (("home", home, away), ("away", away, home)):
            big = tactics.big_game_system(w.c, int(team), int(other), date.fromisoformat(day))
            if big:  # the shape they play against opponents like this one: the expected eleven in it
                lead.facts[f"{side}_system_against_{big['kind']}_sides"] = big["words"]
            elevens[side] = tactics.expected_eleven(w.c, int(team), date.fromisoformat(day), big["shape"] if big else None)
        meeting = tactics.last_meeting(w.c, int(home), int(away), date.fromisoformat(day),
                                       {int(home): elevens["home"], int(away): elevens["away"]})
        if meeting:
            lead.facts["last_meeting_with_team_sheets"] = meeting
        named = _news(w, "where n.kind = 'danger_man' and n.match_day = :d and n.team_uid in (:h, :a)", d=day, h=int(home), a=int(away))
        if named:  # ex-players and pundits naming the man to watch (the game's news)
            lead.facts["the_man_to_watch_say_the_pundits"] = list(dict.fromkeys(f"{x['person']} singled out {x['other_person']} ({x['team']})" for x in named))[:4]
        pressure = _news(w, "where n.kind = 'board_pressure' and n.team_uid in (:h, :a) and n.other_team_uid in (:h, :a)", h=int(home), a=int(away))
        if pressure:
            lead.facts["a_manager_under_pressure"] = [f"{x['person']} ({x['team']}): the board are said to be losing patience; this game could decide his fate"
                                                      for x in pressure][:2]
        for side, team in (("home", home), ("away", away)):
            gone = {uid for uid, _ in (elevens[side] or {}).get("out", {}).items()}
            form = tactics.danger_and_form(w.c, int(team), date.fromisoformat(day), gone)
            for key, value in form.items():
                if value:
                    lead.facts[f"{side}_{key}"] = value
        for side, eleven in elevens.items():
            if eleven:
                lead.facts[f"{side}_expected_shape"] = f"{eleven['shape_words']} ({eleven['share']} of their last {eleven['games']} games)"
                lead.facts[f"{side}_expected_line_up"] = eleven["line_up"]
                if eleven["regulars_out"]:
                    lead.facts[f"{side}_regulars_out"] = eleven["regulars_out"]
                fitness = moods.fitness_notes(w.c, eleven["names"], date.fromisoformat(day))
                if fitness:
                    lead.facts[f"{side}_fitness"] = fitness
        for side, team in (("home", home), ("away", away)):  # the system from the tactics screen (the human club only)
            system = team_tactics.current(w.c, int(team))
            if system:
                lead.facts[f"{side}_system_on_the_tactics_screen"] = team_tactics.words(system)
        if all(elevens.values()):
            duels = tactics.key_duels(elevens["home"], elevens["away"])
            if duels:
                lead.facts["key_duels"] = duels
            matchups = tactics.attribute_duels(w.c, elevens["home"], elevens["away"])
            if matchups:
                lead.facts["attribute_duels"] = matchups
            # each side's record over the last year with the formation it is expected to play, and against the other's
            year = (str(date.fromisoformat(day) - timedelta(days=365)), day)
            for side, team, other in (("home", home, "away"), ("away", away, "home")):
                records = tactics.formation_records(w.c, int(team), *year)
                used = {row["shape"]: row for row in records["used"]}.get(elevens[side]["shape"])
                faced = {row["shape"]: row for row in records["faced"]}.get(elevens[other]["shape"])
                if used and used["games"] >= RECORD_GAMES:
                    lead.facts[f"{side}_record_with_their_{elevens[side]['shape']}"] = tactics.record_words(used)
                if faced and faced["games"] >= RECORD_GAMES:
                    lead.facts[f"{side}_record_against_a_{elevens[other]['shape']}"] = tactics.record_words(faced)
    for league, games in knockout_ties.items():  # one preview of each big cup's coming knockout games, a paragraph each
        name = _comp_name(w, league)
        stage = games[0][1]["round"].split(", ")[1]
        facts = {"competition": name, "region": region_of(name), "round": stage,
                 "ties": [{k: v for k, v in game.items() if k not in ("competition", "league_rules")} for _, game, _ in games]}
        leads.append(Lead("knockout_preview", f"{name}, {stage}: the ties to come", w.until, max(score for _, _, score in games) * 1.2, facts,
                          [f"competition:{league}"] + [f"match:{f['date']}:{f['home_uid']}:{f['away_uid']}" for f, _, _ in games],
                          {"world"}, slug=f"{league}:{min(f['date'] for f, _, _ in games)}"))  # the round, not the day found
    return leads


def _continental(w) -> list[int]:
    return [u for u, c in w.competitions.items() if c.get("scope") == "continental"]


def _european_games(w) -> list[dict]:
    """The cycle's played games of the continental competitions, read once."""
    if getattr(w, "_euro_games", None) is None:
        marks = ",".join(str(u) for u in _continental(w)) or "0"
        w._euro_games = w.rows(f"select * from fixtures where competition_uid in ({marks}) and home_goals is not null "
                               f"and date > :s and date <= :u order by date", s=str(w.since), u=str(w.until))
    return w._euro_games


def _group_lines(w, uid: int, teams: set[int]) -> list[str] | None:
    """The competition's group or league-phase tables as they stand, when nothing has been played after the cycle: groups with
    a team of the cycle, the top eight of a single league phase and the followed clubs' places."""
    latest = (w.rows("select max(date) d from fixtures where competition_uid = :c and home_goals is not null", c=uid) or [{}])[0].get("d")
    if not latest or str(latest)[:10] > str(w.until):
        return None
    groups = q.competition_now(w.c, uid).get("groups") or []
    lines = []
    for group in groups:
        rows = group["table"]
        if len(groups) == 1:
            shown = [(i, r) for i, r in enumerate(rows, 1) if i <= 8 or w.followed(r["team_uid"])]
        elif any(r["team_uid"] in teams for r in rows):
            shown = list(enumerate(rows, 1))
        else:
            continue
        lines.append(f"{group['name']}: " + "; ".join(f"{i} {w.name(r['team_uid'])} {r['points']} pts from {r['played']}" for i, r in shown))
    return lines or None


# -- knockout ties of the big continental cups (2 Nov 2026) -------------------------------------------------------------------
KNOCKOUT_COMP_WEIGHT = 4.2  # the cups whose every knockout game is covered: the Champions League, the Libertadores, the Europa League
SOLO_ROUNDS = ("Semi-finals", "Final")  # a tie of these has its own preview and report; earlier rounds share one piece
KNOCKOUT_SIZE = {"Play-off round": 1.0, "Last 64": 0.9, "Last 32": 1.0, "Last 16": 1.2, "Quarter-finals": 1.4, "Semi-finals": 1.7, "Final": 2.2}
_REGIONS = (("South America", ("libertadores", "sudamericana", "south american", "conmebol")), ("Europe", ("uefa", "europa", "european")),
            ("Asia", ("asian", "afc")), ("Africa", ("african", "caf")), ("North America", ("north american", "concacaf")))


def region_of(name: str | None) -> str:
    """The part of the world a continental competition belongs to, from its name: Europe for UEFA's, South America for the
    Libertadores and the Sudamericana, "World" when the name does not tell."""
    low = (name or "").lower()
    return next((region for region, words in _REGIONS if any(word in low for word in words)), "World")


def _knockout_rounds(w, uid: int) -> list[dict]:
    """This season's knockout rounds of a continental cup, after its league phase or groups, in order, each named as the
    last finished edition named its round in that place (counting back from a final not yet drawn would call the play-off
    round the last 16)."""
    cache = w.__dict__.setdefault("_knockout_rounds", {})
    if uid in cache:
        return cache[uid]
    stages: dict[int, list[dict]] = defaultdict(list)
    for f in w.rows("select * from fixtures where competition_uid = :c and date >= :s order by date, id", c=uid, s=f"{w.season}-06-01"):
        stages[f["stage"]].append(f)
    rounds = q.round_names(sorted(stages.values(), key=lambda games: str(games[0]["date"])))
    grouped = [i for i, r in enumerate(rounds) if r["name"].startswith("Group") or r["name"] == "League phase"]
    knockout = [r for r in rounds[grouped[-1] + 1:]] if grouped else []
    names: list[str] = []
    done = q._rows(w.c, "select max(year) y from competition_editions where competition_uid = :c and decided_on is not null "
                        "and decided_on < :d", c=uid, d=f"{w.season}-07-01")
    if knockout and done and done[0]["y"]:
        last = q.edition(w.c, uid, done[0]["y"])
        if last:
            past = [i for i, r in enumerate(last["rounds"]) if r["name"].startswith("Group") or r["name"] == "League phase"]
            names = [r["name"] for r in last["rounds"][past[-1] + 1:]] if past else []
    order = list(q.KNOCKOUT_ROUNDS)
    for i, r in enumerate(knockout):
        r["name"] = names[i] if i < len(names) else r["name"]
        r["next"] = (names[i + 1] if i + 1 < len(names) else  # else the fixed order: the semi-finals lead to the final
                     order[order.index(r["name"]) + 1] if r["name"] in order[:-1] else None)
    cache[uid] = knockout
    return knockout


def _tie(w, f: dict) -> dict | None:
    """A knockout game of a big continental cup in its tie: the round and leg, the first leg's score, the aggregate as it
    stands on the day (after this game when it is played by then), who goes through and what the winner reaches. None for
    any other game. A league-phase game keeps its own stage words."""
    uid = f["competition_uid"]
    if w.comp(uid).get("scope") != "continental" or w.weight(uid) < KNOCKOUT_COMP_WEIGHT:
        return None
    r = next((r for r in _knockout_rounds(w, uid) if any(g["id"] == f["id"] for g in r["games"])), None)
    if r is None:
        return None
    pair = {f["home_uid"], f["away_uid"]}
    legs = sorted((g for g in r["games"] if {g["home_uid"], g["away_uid"]} == pair), key=lambda g: (str(g["date"]), g["id"]))
    day = min(str(w.until), str(f["date"])[:10]) if f["home_goals"] is not None else str(w.until)
    played = [g for g in legs if g["home_goals"] is not None and str(g["date"])[:10] <= day]
    comp, number = _comp_name(w, uid), next(i for i, g in enumerate(legs, 1) if g["id"] == f["id"])
    a, b = legs[0]["home_uid"], legs[0]["away_uid"]
    goals = {a: 0, b: 0}
    for g in played:
        home, away = q._goals_of(g)
        goals[g["home_uid"]] += home
        goals[g["away_uid"]] += away
    words = {"round": f"{comp}, {r['name']}" + (f", leg {number} of {len(legs)}" if len(legs) > 1 else ", one game"),
             "what_the_winner_reaches": f"the {r['next'].lower()}" if r["next"] else ("the trophy" if r["name"] == "Final" else None)}
    if len(legs) > 1 and number == 1:
        second = legs[1]
        words["the_tie"] = f"the first of two legs; the second is at {w.name(second['home_uid'])} on {str(second['date'])[:10]}"
    if len(legs) > 1 and number == 2 and legs[0]["home_goals"] is not None:
        words["first_leg"] = _result_line(w, legs[0])
    if len(played) == len(legs) and played:
        last = played[-1]
        if goals[a] != goals[b]:
            winner = a if goals[a] > goals[b] else b
            how = "on aggregate" if len(legs) > 1 else ("after extra time" if last["home_goals_after_extra_time"] is not None else "")
        elif last["home_penalties"] is not None:
            winner = last["home_uid"] if last["home_penalties"] > last["away_penalties"] else last["away_uid"]
            how = f"on penalties ({max(last['home_penalties'], last['away_penalties'])}-{min(last['home_penalties'], last['away_penalties'])})"
        else:
            winner, how = None, ""
        total = f"{w.name(a)} {goals[a]}-{goals[b]} {w.name(b)}" + (" on aggregate" if len(legs) > 1 else "")
        words["the_tie"] = total + (f": {w.name(winner)} go through {how}".rstrip() if winner else "")
    elif len(legs) > 1 and number == 2:
        words["the_tie"] = (f"after the first leg: {w.name(a)} {goals[a]}-{goals[b]} {w.name(b)}; the second leg decides it"
                            if played else "the second of two legs")
    return {k: v for k, v in words.items() if v}


def continental_rounds(w) -> list:
    """Each European competition's games of the cycle: the results (the biggest clubs' first) and the groups or the league
    phase as they stand. For World Soccer; each country's clubs get their own story (european_nights)."""
    from app.services.story_leads import Lead

    by_comp: dict[int, list[dict]] = defaultdict(list)
    for f in _european_games(w):
        by_comp[f["competition_uid"]].append(f)
    leads = []
    for uid, games in by_comp.items():
        ties = {f["id"]: tie for f in games if (tie := _tie(w, f))}
        if len(games) < MIN_ROUND_GAMES and not ties:  # a big cup's knockout games are news however few
            continue
        games.sort(key=lambda f: -((w.rep(f["home_uid"]) or 0) + (w.rep(f["away_uid"]) or 0)))
        teams = {t for f in games for t in (f["home_uid"], f["away_uid"])}
        name = _comp_name(w, uid)
        facts = {"competition": name, "region": region_of(name), "games_this_cycle": len(games),
                 "results": [_result_line(w, f) + (f" ({ties[f['id']]['round'].split(', ', 1)[1]}; {ties[f['id']]['the_tie']})"
                                                   if f["id"] in ties and ties[f["id"]].get("the_tie") else "") for f in games[:EURO_RESULTS]],
                 "groups_now": None if ties else _group_lines(w, uid, teams)}
        followed = [_result_line(w, f) for f in games if w.followed(f["home_uid"], f["away_uid"])]
        if followed:
            facts["the_followed_clubs"] = followed
        beats = {"world", "home"} if followed else {"world"}
        size = ROUND_SIZE * max((KNOCKOUT_SIZE.get(t["round"].split(", ")[1], 1.0) for t in ties.values()), default=1.0)
        if ties:  # the knockout games, each in full: a paragraph each in one long piece
            facts.pop("results")
            facts["games"] = [{"result": _result_line(w, f), **{k: v for k, v in _game_in_full(w, f, {}).items() if k not in ("result", "places_now")},
                               **ties[f["id"]]} if f["id"] in ties else {"result": _result_line(w, f)} for f in games]
            leads.append(Lead("knockout_round", f"{name}, {next(iter(ties.values()))['round'].split(', ')[1]}: {len(games)} games",
                              w.until, w.weight(uid) * 10 * size, {k: v for k, v in facts.items() if v is not None},
                              [f"competition:{uid}"] + [f"match:{f['date']}:{f['home_uid']}:{f['away_uid']}" for f in games], beats,
                              slug=f"{uid}:{w.until}"))
            continue
        leads.append(Lead("continental_round", f"{name}: the cycle's {len(games)} games", w.until, w.weight(uid) * 10 * size,
                          {k: v for k, v in facts.items() if v is not None}, [f"competition:{uid}"], beats, slug=f"{uid}:{w.until}"))
    return leads


def knockout_reports(w) -> list:
    """A report of every knockout game of a big continental cup played in the cycle, for World Soccer: the game in full and
    the tie (the round, the leg, the aggregate, who goes through). The followed clubs' games have their own club reports."""
    from app.services.story_leads import Lead

    leads = []
    for f in _european_games(w):
        tie = _tie(w, f)
        if not tie or w.followed(f["home_uid"], f["away_uid"]) or tie["round"].split(", ")[1] not in SOLO_ROUNDS:
            continue  # the earlier rounds are told game by game in the knockout round piece
        uid = f["competition_uid"]
        name = _comp_name(w, uid)
        facts = {"competition": name, "region": region_of(name), "date": str(f["date"]), "home": w.name(f["home_uid"]),
                 "away": w.name(f["away_uid"]), **_game_in_full(w, f, {}), **tie,
                 **_own_league_facts(w, uid, (("home", f["home_uid"]), ("away", f["away_uid"])))}
        facts.pop("places_now", None)  # a knockout game has no table
        size = 0.9 * KNOCKOUT_SIZE.get(tie["round"].split(", ")[1], 1.0)
        leads.append(Lead("tie_report", f"{name}, {tie['round'].split(', ', 1)[1]}: {_result_line(w, f).split(': ', 1)[1]}",
                          date.fromisoformat(str(f["date"])[:10]), w.weight(uid) * 10 * size, facts,
                          [f"match:{f['date']}:{f['home_uid']}:{f['away_uid']}", f"competition:{uid}", f"club:{f['home_uid']}", f"club:{f['away_uid']}"],
                          {"world"} | w.beats_for(f["home_uid"], f["away_uid"], competition_uid=uid)))
    return leads


def european_nights(w) -> list:
    """Each covered country's clubs in Europe in the cycle: their results in every continental competition and their places
    in the groups, for that country's paper."""
    from app.services.story_leads import Lead

    nations = {w.comp(league).get("nation_uid") for league in current_leagues(w)} - {None}
    by_nation: dict[int, list[tuple[dict, int]]] = defaultdict(list)
    for f in _european_games(w):
        for team in (f["home_uid"], f["away_uid"]):
            nation = w.team(team).get("league_nation_uid") or w.team(team).get("club_nation_uid")
            if nation in nations:
                by_nation[nation].append((f, team))
    leads = []
    for nation, games in by_nation.items():
        if len(games) < EURO_NATION_GAMES:
            continue
        games.sort(key=lambda g: (-w.weight(g[0]["competition_uid"]), -(w.rep(g[1]) or 0)))
        clubs = {team for _, team in games}
        won = sum(q.result_for(f, team)[2] == "W" for f, team in games)
        drawn = sum(q.result_for(f, team)[2] == "D" for f, team in games)
        country = w.nations.get(nation, {}).get("name", "")
        # a knockout leg carries its tie: the round, the aggregate and who goes through (25 Feb 2064: "Cluj 1-0 Napoli, Cluj
        # won" alone, and the paper sent Napoli out on 3-2 on aggregate in their favour)
        ties = {f["id"]: tie for f, _ in games if (tie := _tie(w, f))}
        facts = {"country": country, "record": f"{len(games)} games: {won} won, {drawn} drawn, {len(games) - won - drawn} lost",
                 "results": list(dict.fromkeys(  # a game between two of the country's clubs once
                     f"{_comp_name(w, f['competition_uid'])}, {_result_line(w, f)}"
                     + (f" ({ties[f['id']]['round'].split(', ', 1)[1]}; {ties[f['id']]['the_tie']})" if ties.get(f["id"], {}).get("the_tie") else "")
                     for f, _ in games))}
        groups = []
        for uid in {f["competition_uid"] for f, _ in games if f["id"] not in ties}:  # a knockout week has no table to tell
            for line in _group_lines(w, uid, clubs) or []:
                groups.append(f"{_comp_name(w, uid)}, {line}")
        if groups:
            facts["groups_now"] = groups
        weight = max(w.weight(f["competition_uid"]) for f, _ in games)
        biggest = max((f for f, _ in games), key=lambda f: w.weight(f["competition_uid"]))
        facts["region"] = region_of(_comp_name(w, biggest["competition_uid"]))
        beats = {f"nation:{nation}"} | ({"home"} if any(w.followed(team) for team in clubs) else set())
        where = "Europe" if facts["region"] == "Europe" else "continental competition"
        leads.append(Lead("european_nights", f"{country}'s clubs in {where}: {facts['record']}", w.until, weight * 10 * 1.1,
                          facts, [f"nation:{nation}"] + [f"club:{team}" for team in sorted(clubs, key=lambda t: -(w.rep(t) or 0))[:4]],
                          beats, slug=f"{nation}:{w.until}"))
    return leads


NATIONAL_SCORERS = 6


def _national_games(w) -> list[dict]:
    """The cycle's played games of the national teams' competitions (senior), read once."""
    if getattr(w, "_national_games", None) is None:
        marks = ",".join(str(u) for u, c in w.competitions.items() if c.get("scope") == "national_teams") or "0"
        w._national_games = w.rows(f"select f.* from fixtures f join teams h on h.fm_uid = f.home_uid where f.competition_uid in ({marks}) "
                                   f"and f.home_goals is not null and f.date > :s and f.date <= :u and h.age_limit is null order by f.date",
                                   s=str(w.since), u=str(w.until))
    return w._national_games


def _scorers(w, games: list[dict], side_team: int | None = None) -> list[str]:
    """The scorers in these games (of one team when given), most goals first."""
    if not games:
        return []
    ids = ",".join(str(f["id"]) for f in games)
    rows = w.rows(f"select p.name, count(*) goals, max(case g.side when 0 then f.home_uid else f.away_uid end) team from goals g "
                  f"join fixtures f on f.id = g.fixture_id join people p on p.fm_uid = g.scorer_uid where g.fixture_id in ({ids}) "
                  f"and g.kind in (0, 2) group by g.scorer_uid order by goals desc")
    return [f"{r['name']} {r['goals']}" for r in rows if side_team is None or r["team"] == side_team][:NATIONAL_SCORERS]


def international_rounds(w) -> list:
    """Each national-team competition's games of the cycle (World Cup and European Championship qualifiers, Nations
    Leagues, tournaments): the results, the biggest nations first, the groups as they stand and the top scorers."""
    from app.services.story_leads import Lead

    by_comp: dict[int, list[dict]] = defaultdict(list)
    for f in _national_games(w):
        by_comp[f["competition_uid"]].append(f)
    leads = []
    for uid, games in by_comp.items():
        if len(games) < MIN_ROUND_GAMES:
            continue
        games.sort(key=lambda f: -((w.rep(f["home_uid"]) or 0) + (w.rep(f["away_uid"]) or 0)))
        teams = {t for f in games for t in (f["home_uid"], f["away_uid"])}
        name = _comp_name(w, uid)
        facts = {"competition": name, "games_this_cycle": len(games), "results": [_result_line(w, f) for f in games[:EURO_RESULTS]],
                 "groups_now": _group_lines(w, uid, teams), "scorers": _scorers(w, games) or None}
        nations = {n for t in teams for n in [w.team(t).get("nation_uid")] if n}
        weight = w.weight(uid) if w.comp(uid).get("reputation") else 3.5
        leads.append(Lead("international_round", f"{name}: the cycle's {len(games)} games", w.until, weight * 10 * ROUND_SIZE,
                          {k: v for k, v in facts.items() if v is not None}, [f"competition:{uid}"], {"world"} | {f"nation:{n}" for n in nations},
                          slug=f"{uid}:{w.until}"))
    return leads


def _stage_words(table: list[dict]) -> str:
    """A continental league phase, said plainly: what its places earn is not known, so no draw, knockout round, dead rubber
    or favourites (a report of Chelsea 3-1 Shamrock Rovers had all four, 2 Nov 2026)."""
    return (f"the league phase: one table of {len(table)} teams; what each place earns at its end, who goes through, whether "
            "anything was at stake and what comes next are not known here, so say nothing of them")


def _own_league_facts(w, league: int, sides) -> dict:
    """For a continental game: each club's place in its own league, as background only. The game does not count there
    (a Champions League report led with Chelsea "seventeen points clear" in England, 3 Feb 2064). Empty otherwise."""
    if w.comp(league).get("scope") != "continental":
        return {}
    facts = {}
    for side, team in sides:
        domestic, _ = w.level(team, w.season)
        if not domestic or w.comp(domestic).get("scope") != "league":
            continue
        start, end = _window(w, domestic)
        table = q.league_table(w.c, domestic, start, end)
        place = {r["team_uid"]: i for i, r in enumerate(table, 1)}
        if team in place:
            facts[f"{side}_in_their_own_league"] = (f"{_comp_name(w, domestic)}: {_place_words(w, domestic, table, place, team)}. Background "
                                                    "only: another competition, which this game does not count in")
    return facts


def _comp_name(w, uid: int) -> str:
    """The competition's name, or "a friendly" for one the world does not know (an international friendly has no scope)."""
    return w.comp(uid).get("name") or "a friendly"


def national_teams(w) -> list:
    """Each covered country's national team in the cycle: its results in every competition, its scorers, where it stands
    in its group and its next game, for that country's paper."""
    from app.services.story_leads import Lead

    nations = {w.comp(league).get("nation_uid") for league in current_leagues(w)} - {None}
    leads = []
    for nation in nations:
        senior = w.rows("select fm_uid from teams where nation_uid = :n and age_limit is null and club_uid is null limit 1", n=nation)
        if not senior:
            continue
        team = senior[0]["fm_uid"]
        games = [f for f in _national_games(w) if team in (f["home_uid"], f["away_uid"])]
        if not games:
            continue
        won = sum(q.result_for(f, team)[2] == "W" for f in games)
        drawn = sum(q.result_for(f, team)[2] == "D" for f in games)
        country = w.nations.get(nation, {}).get("name", "")
        upcoming = w.rows("select * from fixtures where (home_uid = :t or away_uid = :t) and home_goals is null and date > :u order by date limit 2",
                          t=team, u=str(w.until))
        groups = []
        for uid in {f["competition_uid"] for f in games}:
            for line in _group_lines(w, uid, {team}) or []:
                groups.append(f"{_comp_name(w, uid)}, {line}")
        facts = {"country": country, "record": f"{len(games)} games: {won} won, {drawn} drawn, {len(games) - won - drawn} lost",
                 "results": [f"{_comp_name(w, f['competition_uid'])}, {_result_line(w, f)}" for f in games],
                 "scorers": _scorers(w, games, team) or None, "groups_now": groups or None,
                 "next": [f"{_comp_name(w, f['competition_uid'])}, {f['date']}: {w.name(f['home_uid'])} v {w.name(f['away_uid'])}" for f in upcoming] or None}
        weight = max((w.weight(f["competition_uid"]) if w.comp(f["competition_uid"]).get("reputation") else 3.5) for f in games)
        leads.append(Lead("national_team", f"{country}: {facts['record']}", w.until, weight * 10 * 1.2,
                          {k: v for k, v in facts.items() if v is not None}, [f"club:{team}", f"nation:{nation}"], {f"nation:{nation}", "world"},
                          slug=f"{nation}:{w.until}"))
    return leads


def xg_table(w, league: int) -> list[dict]:
    """The league's xG table up to the cycle's end (expected.xg_table), read once per cycle."""
    cache = w.__dict__.setdefault("_xg_tables", {})
    if league not in cache:
        start, end = _window(w, league)
        cache[league] = expected.xg_table(w.c, league, start, min(end, str(w.until)))
    return cache[league]


def expected_points(w) -> list:
    """Teams far above or below the points their chances deserve (expected points from each game's xG): who is riding their
    luck or their finishing, and who is paying for wasted chances or a leaky goal. At most once a month a team."""
    from app.services.story_leads import Lead

    leads = []
    for league in current_leagues(w):
        table = xg_table(w, league)
        if not table or max(t["games"] for t in table) < LUCK_GAMES:
            continue
        top = sorted(table, key=lambda t: -abs(t["points_vs_xpts"]))[:2]
        for t in top:
            gap = t["points_vs_xpts"]
            if abs(gap) < LUCK_POINTS:
                continue
            team = t["team_uid"]
            facts = {"competition": _comp_name(w, league), "club": w.name(team), "expected": expected.words(t),
                     "finishing": f"{t['goals_vs_xg']:+.1f} goals against their xG", "defending": f"{t['conceded_vs_xga']:+.1f} conceded against the xG they allowed",
                     "the_league_on_expected_points": [f"{expected.ordinal(r['xpts_place'])} {w.name(r['team_uid'])} {r['xpts']:.1f} ({r['points']} points)" for r in table[:6]],
                     "manager": _manager(w, team)}
            size = 0.7 + min(abs(gap), 12) / 15 + (w.rep(team) or 0) / 20000
            leads.append(Lead("expected_points", f"{w.name(team)} {gap:+.0f} points against their expected", w.until, w.weight(league) * 10 * size,
                              {k: v for k, v in facts.items() if v is not None}, [f"club:{team}", f"competition:{league}"],
                              w.beats_for(team, competition_uid=league), slug=f"{league}:{team}:{w.until:%Y-%m}"))
    return sorted(leads, key=lambda lead: -lead.score)[:4]


# -- the drama of a game: why it matters beyond the names ----------------------------------------------------------------
LATE_SEASON = 0.6  # of the league games played before a top-two meeting is a title decider
TITLE_GAP = 3  # points between the top two, at most
RELEGATION_SEASON = 0.5  # of the games played before a bottom-four meeting is a six-pointer
UNBEATEN_AT_STAKE = 10  # league games unbeaten
LOSING_AT_STAKE = 4  # league defeats in a row
RETURN_GAMES = 50  # games for the old club that make a player's return a story (one a side, the longest-serving)
RECORD_WITHIN = 1  # goals from the club's scoring record
RECORD_FLOOR = 50  # goals: a smaller record is not one
NEAR_DAYS, FAR_GAME = 7, 0.85  # a game further away than this counts less in a preview's score
TOP_CLASH = 4  # two clubs this high meet: a top-of-the-table clash
CLASH_GAP = 6  # ... worth more with at most these points between them
CLASH_TEAMS = 10  # a league this big has a bottom four worth a six-pointer when its past places are not known
DRAMA = {"title_decider": 0.3, "final": 0.3, "semi_final": 0.2, "relegation_six_pointer": 0.35, "top_clash": 0.35, "close_clash": 0.1,
         "manager_return": 0.2,
         "player_return": 0.1, "run_at_stake": 0.15, "record_chance": 0.15}
DRAMA_CAP = 0.6


def _bottom_clash(w, league: int, table: list[dict], place: dict[int, int], home: int, away: int) -> bool:
    """Two clubs at the bottom meet from halfway through the season: both in the places that went down in past seasons
    or within one place of them, or both in the bottom four when those places are not known."""
    if home not in place or away not in place or not table or w.comp(league).get("scope") != "league":
        return False
    total, n = 2 * (len(table) - 1), len(table)
    played = min(table[place[home] - 1]["played"], table[place[away] - 1]["played"])
    zones = _zones(w, league)
    if zones and zones["down"] and zones["teams"] == n:
        line = n - zones["down"] - 1
    elif n >= CLASH_TEAMS:
        line = n - 4
    else:
        return False  # in a small league the bottom four is half the table
    return min(place[home], place[away]) > line and played >= RELEGATION_SEASON * total


def _nth_place(n: int) -> str:
    return q._place(n)


def _drama(w, f: dict, table: list[dict], place: dict[int, int]) -> tuple[float, dict]:
    """The bonus and the facts of a game's drama: a title decider, a relegation six-pointer, a cup final or semi-final, a
    run at stake, a manager's or a player's return to his old club, a scoring record or a hundredth cap within reach."""
    home, away, league = f["home_uid"], f["away_uid"], f["competition_uid"]
    facts: dict = {}
    bonus = 0.0
    if home in place and away in place and table:
        total = 2 * (len(table) - 1)
        played = min(table[place[home] - 1]["played"], table[place[away] - 1]["played"])
        first, second = sorted((place[home], place[away]))
        if (first, second) == (1, 2) and played >= LATE_SEASON * total:
            gap = table[0]["points"] - table[1]["points"]
            if gap <= TITLE_GAP:
                facts["title_decider"] = f"first against second, {gap} point{'s' if gap != 1 else ''} apart, {total - played} games left"
                bonus += DRAMA["title_decider"]
        gap = abs(table[first - 1]["points"] - table[second - 1]["points"])
        if second <= TOP_CLASH and "title_decider" not in facts and played >= FORM_GAMES:
            facts["top_clash"] = (f"{_nth_place(first)} against {_nth_place(second)}, {gap} point{'s' if gap != 1 else ''} apart, "
                                  f"{total - played} games left")
            bonus += DRAMA["top_clash"] + (DRAMA["close_clash"] if gap <= CLASH_GAP else 0)
        if _bottom_clash(w, league, table, place, home, away):
            zones = _zones(w, league)
            where = (f"both in or near the relegation zone (the bottom {zones['down']})" if zones and zones["down"]
                     else "both in the bottom four")
            facts["relegation_six_pointer"] = f"{where}: {place[home]} and {place[away]} of {len(table)}, {total - played} games left"
            bonus += DRAMA["relegation_six_pointer"]
    comp = w.comp(league)
    if comp.get("scope") in ("cup", "continental") and f["stage"] is not None:
        day = date.fromisoformat(str(f["date"])[:10])
        ties = w.rows("select count(distinct min(home_uid, away_uid) || '-' || max(home_uid, away_uid)) n from fixtures where competition_uid = :c "
                      "and stage = :s and date >= :a and date <= :b", c=league, s=f["stage"], a=str(day - timedelta(days=200)),
                      b=str(day + timedelta(days=200)))[0]["n"]
        if ties == 1:
            facts["round"], bonus = "the final", bonus + DRAMA["final"]
        elif ties == 2:
            facts["round"], bonus = "a semi-final", bonus + DRAMA["semi_final"]
    for side, team, other in (("home", home, away), ("away", away, home)):
        run = _run_at_stake(w, team)
        if run and comp.get("scope") == "continental":
            run += " in their own league (background only: this game does not count there)"
        if run:
            facts[f"{side}_run_at_stake"], bonus = run, bonus + DRAMA["run_at_stake"]
        back, kind = _returns(w, team, other)
        if back:
            facts[f"{side}_returns"], bonus = back, bonus + DRAMA[kind]
        record = _record_chance(w, team, league, comp)
        if record:
            facts[f"{side}_record_chance"], bonus = record, bonus + DRAMA["record_chance"]
    return min(bonus, DRAMA_CAP), facts


def _run_at_stake(w, team: int) -> str | None:
    """A long unbeaten run or a losing run in the team's league this season."""
    leagues = ",".join(str(u) for u in current_leagues(w)) or "0"
    found = w.rows(f"select competition_uid from fixtures where competition_uid in ({leagues}) and (home_uid = :t or away_uid = :t) "
                   f"and date <= :u order by date desc limit 1", t=team, u=str(w.until))
    if not found:
        return None
    league = found[0]["competition_uid"]
    start, end = _window(w, league)
    played = w.rows("select * from fixtures where competition_uid = :c and (home_uid = :t or away_uid = :t) and home_goals is not null "
                    "and date >= :s and date <= :u order by date", c=league, t=team, s=start, u=end)
    results = [q.result_for(x, team)[2] for x in played]
    if not results:
        return None
    unbeaten = next((i for i, r in enumerate(reversed(results)) if r == "L"), len(results))
    losing = next((i for i, r in enumerate(reversed(results)) if r != "L"), len(results))
    if unbeaten >= UNBEATEN_AT_STAKE:
        return f"unbeaten in {unbeaten} league games" + (" all season" if unbeaten == len(results) else "")
    if losing >= LOSING_AT_STAKE:
        return f"{losing} league defeats in a row"
    return None


INJURED_TOLD = 3


def _injured_regulars(w, team: int) -> list[str]:
    """The team's injured players who matter: its regulars on the team sheets, most starts first, with how much they
    play. A team without enough sheets falls back to its best-known injured players."""
    regulars = q.regulars(w.c, team, w.until)
    if regulars is None:
        return [f"{x['name']} ({x['injury']})" for x in w.rows(
            "select p.name, i.injury from injuries i join people p on p.fm_uid = i.person_uid where i.current = 1 "
            "and p.team_uid = :t and coalesce(p.reputation_world, 0) >= 3000 order by p.reputation_world desc limit :n", t=team, n=INJURED_TOLD)]
    hurt = w.rows("select p.fm_uid, p.name, i.injury from injuries i join people p on p.fm_uid = i.person_uid where i.current = 1 "
                  "and p.team_uid = :t", t=team)
    hurt = sorted((x for x in hurt if x["fm_uid"] in regulars), key=lambda x: -regulars[x["fm_uid"]]["starts"])
    return [f"{x['name']} ({x['injury']}; {q.regular_words(regulars[x['fm_uid']])})" for x in hurt[:INJURED_TOLD]]


def _returns(w, team: int, other: int) -> tuple[list[str] | None, str]:
    """The team's manager or a player who used to be at the other club, and which of the two it is."""
    found = []
    boss = w.rows("select p.fm_uid, p.name from people p where p.staff_job = 'Manager' and p.staff_team_uid = :t limit 1", t=team)
    if boss:
        spells = w.rows("select started, ended from manager_spells where person_uid = :p and team_uid = :o order by started", p=boss[0]["fm_uid"], o=other)
        if spells:
            when = ", ".join(f"{str(sp['started'])[:4]} to {str(sp['ended'])[:4] if sp['ended'] else 'now'}" for sp in spells)
            found.append(f"{boss[0]['name']}, {w.name(team)}'s manager, managed {w.name(other)} ({when})")
    players = w.rows("select p.fm_uid, p.name, sum(ps.starts + ps.substitute_appearances) games, sum(ps.goals) goals, min(ps.season_start) first, "
                     "max(ps.season_start) last from people p join player_seasons ps on ps.person_uid = p.fm_uid and ps.team_uid = :o "
                     "and ps.kind in (1, 2, 3) where p.team_uid = :t group by p.fm_uid having games >= :g order by games desc",
                     o=other, t=team, g=RETURN_GAMES)
    regulars = q.regulars(w.c, team, w.until)
    players = [r for r in players if regulars is None or r["fm_uid"] in regulars][:1]  # a returning reserve is no story
    for row in players:
        span = q.season_label(row["first"]) if row["first"] == row["last"] else f"{q.season_label(row['first'])} to {q.season_label(row['last'])}"
        found.append(f"{row['name']} returns to {w.name(other)}: {row['games']} games and {row['goals']} goals there, {span}")
    return found or None, "manager_return" if boss and len(found) == len(players) + 1 else "player_return"


def _record_chance(w, team: int, league: int, comp: dict) -> str | None:
    """A current player within a goal of the club's all-time scoring record, or a player on 99 caps in an international."""
    if comp.get("scope") == "national_teams":
        caps = w.rows("select p.name, sum(ps.starts + ps.substitute_appearances) caps from national_squads s join people p on p.fm_uid = s.person_uid "
                      "join player_seasons ps on ps.person_uid = p.fm_uid and ps.kind = 4 "  # the save counts a cap at the club he was at, so not by team
                      "where s.team_uid = :t and s.as_of = (select max(as_of) from national_squads where team_uid = :t and as_of <= :u) "
                      "group by p.fm_uid having caps = 99", t=team, u=str(w.until))
        return f"{caps[0]['name']} would win his 100th cap" if caps else None
    scorers = w.rows("select ps.person_uid, p.name, p.team_uid now_at, sum(ps.goals) goals from player_seasons ps join people p on p.fm_uid = ps.person_uid "
                     "where ps.team_uid = :t and ps.kind in (1, 2, 3) group by ps.person_uid order by goals desc limit 2", t=team)
    if len(scorers) < 2 or scorers[0]["goals"] < RECORD_FLOOR:
        return None
    best, chaser = scorers
    if chaser["now_at"] == team and best["now_at"] != team and best["goals"] - chaser["goals"] <= RECORD_WITHIN:
        verb = "equal" if best["goals"] - chaser["goals"] == RECORD_WITHIN else "pass"
        return (f"{chaser['name']} needs one goal to {verb} the most anyone has scored for {w.name(team)} in the archive's years, "
                f"{best['goals']} ({best['name']})")
    return None


# -- the transfer market: who wants whom, and the bids ----------------------------------------------------------------------
INTEREST_PLAYER = 6000  # a player this well known with a club after him is a story anywhere; a followed club's player always
INTEREST_SUITOR = 7000  # ... or a suitor this well known
BID_FEE = 20_000_000  # a bid this big is a story anywhere; a followed club's bids always
# A club with this many players on its list is shopping, not chasing (the game has the Saudi clubs after every well-known
# player of about 30; 25 Nov 2063: Al-Ittihad 69, Al-Nassr 42, Al-Hilal 39). Its interest alone is no story, except for a
# followed club's player; the writer gets the list's size instead.
SERIAL_SUITOR = 30
SAME_MARKET = 2  # at most this many stories a cycle whose suitors all come from one country (a followed club's player is not counted)
TRANSFER_SCALE = 4  # per 1000 of the club's reputation (was 10: a Saudi list led World Soccer every cycle, 19 Dec 2063)
SHOPPING_STORIES = 1  # ... and this many whose suitors include a club shopping widely (6 Dec 2063: five of seven stories still said Saudi)


def _record_facts(w, person: int, team: int) -> dict:
    """What a transfer story may say of the player and his club: his time at the club (seasons, games, goals, all competitions)
    and the club's last five results. Without them the writer invented a defeat in Naples and 27 goals (Hilgers, 8 Dec 2063)."""
    rows = w.rows("select season_start, sum(starts + substitute_appearances) games, sum(goals) goals from player_seasons "
                  "where person_uid = :p and team_uid = :t and kind in (1, 2, 3) group by season_start order by season_start",  # league, cup, continental
                  p=person, t=team)
    results = w.rows("select f.date, f.home_uid, f.away_uid, f.home_goals, f.away_goals, c.name competition from fixtures f "
                     "left join competitions c on c.fm_uid = f.competition_uid where (f.home_uid = :t or f.away_uid = :t) "
                     "and f.date <= :u and f.home_goals is not null order by f.date desc limit 5", t=team, u=str(w.until))
    facts = {}
    if rows:
        facts["his_time_at_the_club"] = (f"since {q.season_label(rows[0]['season_start'])}: {sum(r['games'] or 0 for r in rows)} games, "
                                         f"{sum(r['goals'] or 0 for r in rows)} goals in league, cup and continental games")
    if results:
        facts["the_clubs_last_results"] = [f"{r['date']}: {w.name(r['home_uid'])} {r['home_goals']}-{r['away_goals']} {w.name(r['away_uid'])}"
                                           f" ({r['competition'] or 'friendly'})" for r in results]
        facts["nothing_else_happened"] = "these are the club's last games; no other game, defeat or injury exists"
    return facts


def _deal_facts(w, person: int) -> dict:
    """A player's current deal in words, for a story about him: his wage as the game shows it and when it ends."""
    rows = w.rows("select c.wage, c.expires from contracts c join people p on p.fm_uid = c.person_uid and p.team_uid = c.team_uid "
                  "where c.person_uid = :p and c.job = 'Player' order by c.as_of desc limit 1", p=person)
    if not rows:
        return {}
    return {k: v for k, v in {"his_wage": q.wage_words(rows[0]["wage"]), "his_deal_ends": str(rows[0]["expires"]) if rows[0]["expires"] else None}.items() if v}


def transfer_market(w) -> list:
    """Clubs after a player (a followed club's, a well-known one, or one with big suitors): told once a season per player.
    And the bids of the cycle: made, agreed, completed or collapsed, for a followed club or above BID_FEE."""
    from app.services.story_leads import Lead

    leads, one_market = [], {}
    for row in q.wanted_players(w.c, limit=80):
        team = row["team_uid"]
        suitors = [s for s in row["suitors"] if s["team_uid"] != team]
        if not suitors:
            continue
        chasing = [s for s in suitors if (s.get("list_size") or 0) < SERIAL_SUITOR]
        big = max((s["reputation"] or 0) for s in chasing) if chasing else 0
        if not (w.followed(team) or (chasing and ((row["reputation_world"] or 0) >= INTEREST_PLAYER or big >= INTEREST_SUITOR))):
            continue
        shopping = {s["name"]: f"{s['list_size']} players on their list" for s in suitors if (s.get("list_size") or 0) >= SERIAL_SUITOR}
        # the clubs shopping widely stay out of the clubs after him and of the headline: the story is the club that chases him
        active = [s["name"] for s in suitors if not s["rumoured"] and s["name"] not in shopping] or [s["name"] for s in suitors if not s["rumoured"]]
        rumoured = [s["name"] for s in suitors if s["rumoured"] and s["name"] not in shopping]
        facts = {"player": row["name"], "club": w.name(team), "clubs_after_him": active or None, "rumoured": rumoured or None,
                 "also_on_the_lists_of": shopping or None, **_record_facts(w, row["person_uid"], team),
                 # the two amounts of an entry are not wages (no wage exists before a bid is accepted; Júnior, 27 Nov 2063):
                 # unnamed until a screen names them, so none reaches a story. His own deal, from the contracts, does.
                 **_deal_facts(w, row["person_uid"])}
        # on the news scale (a derby is 55, a league round 70): interest in a Chelsea player scores about 60, not 260
        score = (w.rep(team) or 3000) / 1000 * TRANSFER_SCALE * (1.0 + 0.15 * len(suitors)) * (1.3 if w.followed(team) else 1.0)
        lead = Lead("transfer_interest", f"{', '.join(active or rumoured)} want {row['name']} ({w.name(team)})", w.until, score,
                    {k: v for k, v in facts.items() if v is not None}, [f"person:{row['person_uid']}", f"club:{team}"],
                    w.beats_for(team, suitors[0]["team_uid"]) | {"transfers"}, slug=f"{row['person_uid']}:{w.season}")
        markets = {s.get("nation_uid") for s in suitors}
        if w.followed(team):
            leads.append(lead)
        elif shopping:  # a club shopping widely among his suitors: SHOPPING_STORIES such stories a cycle at most
            one_market.setdefault("shopping", []).append(lead)
        elif len(markets) != 1:
            leads.append(lead)
        else:  # one country's clubs after him: the best SAME_MARKET such stories a cycle, so one market does not fill the papers
            one_market.setdefault(markets.pop(), []).append(lead)
    for market, found in one_market.items():
        leads.extend(sorted(found, key=lambda l: -l.score)[:SHOPPING_STORIES if market == "shopping" else SAME_MARKET])
    for b in w.rows("select o.id, o.team_uid bidder_uid, o.person_uid, coalesce(o.fee_guaranteed, o.fee) fee, o.fee_max, o.status, "
                    "o.offered_on, o.completes_on, o.status_changed_on, p.name, "
                    "p.team_uid owner_uid, t.name bidder from transfer_offers o join people p on p.fm_uid = o.person_uid "
                    f"join teams t on t.fm_uid = o.team_uid where ({_BID_NEWS} or (o.status = 'made' and {_COUNTERED})) "
                    "and coalesce(o.loan, 0) = 0 and o.status != 'counter' order by o.fee desc limit 60", s=str(w.since), u=str(w.until)):
        bidder, owner = b["bidder_uid"], b["owner_uid"]
        if not (w.followed(bidder) or (owner and w.followed(owner)) or b["fee"] >= BID_FEE):
            continue
        counter = w.rows("select fee, status_changed_on from transfer_offers where team_uid = :t and person_uid = :p and offered_on = :d "
                         "and status = 'counter' order by fee desc limit 1", t=bidder, p=b["person_uid"], d=str(b["offered_on"]))
        counter = counter[0] if counter and (not counter[0]["status_changed_on"] or str(counter[0]["status_changed_on"]) <= str(w.until)) else None
        status = "countered" if counter and b["status"] == "made" else b["status"]
        facts = {"player": b["name"], "his_club": w.name(owner) if owner else None, "bidder": b["bidder"], "fee_pounds": b["fee"],
                 "fee_euros": round(b["fee"] * 1.166), "rising_with_add_ons_to_pounds": b["fee_max"] if b["fee_max"] and b["fee_max"] > b["fee"] else None,
                 "status": status, "bid_date": str(b["offered_on"]), "status_known_since": _changed(b),
                 "his_club_asked_for_pounds": counter["fee"] if counter else None,
                 "the_counter_proposal": (f"{w.name(owner) if owner else 'his club'} answered with a counter-proposal: they asked for "
                                          f"£{counter['fee'] / 1e6:.1f}m" + (f" (known since {counter['status_changed_on']})" if counter["status_changed_on"] else ""))
                                         if counter else None,
                 "completes_on": str(b["completes_on"]) if b["status"] == "agreed" and b["completes_on"] else None}
        code = q.main_positions(w.c, [b["person_uid"]]).get(b["person_uid"])
        if code:  # how it would go: whom he would compete with, and who plays there at his club if he leaves
            moves = {"at_the_bidding_club": position_picture(w, b["person_uid"], bidder, code),
                     "at_his_club_without_him": position_picture(w, b["person_uid"], owner, code) if owner else None}
            facts["if_he_moves"] = {k: v for k, v in moves.items() if v}
        leads.append(Lead("transfer_bid", f"{b['bidder']} bid £{b['fee'] / 1e6:.1f}m for {b['name']} ({status})",
                          date.fromisoformat(str(b["offered_on"])), (w.rep(bidder) or 3000) / 1000 * 10 * (1.2 if b["status"] in ("agreed", "collapsed") else 1.0),
                          {k: v for k, v in facts.items() if v is not None}, [f"person:{b['person_uid']}", f"club:{bidder}"] + ([f"club:{owner}"] if owner else []),
                          w.beats_for(bidder, owner) | {"transfers"}, slug=f"{b['person_uid']}:{bidder}:{b['offered_on']}:{status}"))
    # loan offers for a followed club's player, or from a followed club (King's Lynn for Chelsea's Spyrou, 3 Dec 2063)
    try:
        loans = w.rows("select o.team_uid bidder_uid, o.person_uid, o.status, o.offered_on, o.completes_on, o.status_changed_on, p.name, "
                       "p.team_uid owner_uid, t.name bidder from transfer_offers o join people p on p.fm_uid = o.person_uid "
                       f"join teams t on t.fm_uid = o.team_uid where o.loan = 1 and {_BID_NEWS}", s=str(w.since), u=str(w.until))
    except Exception:  # noqa: BLE001 - an archive imported before loan offers were read
        loans = []
    for b in loans:
        bidder, owner = b["bidder_uid"], b["owner_uid"]
        if not (w.followed(bidder) or (owner and w.followed(owner))):
            continue
        known = b["status"] in ("made", "agreed", "completed", "collapsed", "withdrawn", "rejected")
        facts = {"player": b["name"], "his_club": w.name(owner) if owner else None, "the_club_asking": b["bidder"],
                 "offer_date": str(b["offered_on"]), "outcome": b["status"] if known else "not known (do not guess one)", "outcome_known_since": _changed(b),
                 "loan_until": str(b["completes_on"]) if b["completes_on"] and b["completes_on"] != b["offered_on"] else None,
                 **_record_facts(w, b["person_uid"], owner)}
        leads.append(Lead("loan_offer", f"{b['bidder']} ask to borrow {b['name']} ({w.name(owner)})", date.fromisoformat(str(b["offered_on"])),
                          (w.rep(owner) or 3000) / 1000 * TRANSFER_SCALE, {k: v for k, v in facts.items() if v},
                          [f"person:{b['person_uid']}", f"club:{bidder}"] + ([f"club:{owner}"] if owner else []),
                          w.beats_for(bidder, owner) | {"transfers"}, slug=f"{b['person_uid']}:{bidder}:{b['offered_on']}:{b['status']}"))
    return leads


# a bid is news when it is made, and again when its answer comes in a later cycle (made then, rejected now)
_ANSWERS = ("rejected", "withdrawn", "collapsed", "agreed")
_BID_NEWS = ("((o.offered_on > :s and o.offered_on <= :u) or (o.status_changed_on > :s and o.status_changed_on <= :u "
             f"and o.status in ({', '.join(repr(a) for a in _ANSWERS)})))")
# a bid whose counter-proposal a save first showed in the cycle is news again, while the bid is still open
_COUNTERED = ("exists (select 1 from transfer_offers c where c.team_uid = o.team_uid and c.person_uid = o.person_uid "
              "and c.offered_on = o.offered_on and c.status = 'counter' and c.status_changed_on > :s and c.status_changed_on <= :u)")


def _changed(bid) -> str | None:
    """The day the answer to an older bid was first seen, when that is what makes it news."""
    return str(bid["status_changed_on"]) if bid["status_changed_on"] and str(bid["status_changed_on"]) > str(bid["offered_on"]) else None


# -- the promise kept ------------------------------------------------------------------------------------------------------
WHAT_WE_SAID = ("derby", "home_place", "away_place", "home_injured", "away_injured", "home_regulars_out", "away_regulars_out",
                "home_expected_shape", "away_expected_shape", "key_duels", "last_meetings", "home_fitness", "away_fitness")
PROMISED_SIZE = 1.3  # of a derby's weight: the report always makes the paper


def published_previews() -> list:
    """The preview articles the papers have published, each with its match reference; none outside the archive app."""
    from sqlalchemy.exc import OperationalError

    from app.models.world_soccer_article import WorldSoccerArticle
    from app.models.world_soccer_issue import WorldSoccerIssue

    try:
        return (WorldSoccerArticle.query.join(WorldSoccerIssue).filter(WorldSoccerIssue.status == "published",
                                                                       WorldSoccerArticle.lead_key.like("preview:match:%")).all())
    except (RuntimeError, OperationalError):  # no app context, or no articles table (a bare world database)
        return []


def promised_reports(w) -> list:
    """The report of every game a paper previewed that has been played in this cycle: the promise kept, with the result,
    the goals, the player of the match and what the preview said, for the paper that printed the preview."""
    from app.services.story_leads import Lead

    leads = []
    for article in published_previews():
        _, _, day, home, away, *_ = article.lead_key.split(":")
        f = w.rows("select * from fixtures where date = :d and home_uid = :h and away_uid = :a and home_goals is not null "
                   "and date > :s and date <= :u", d=day, h=int(home), a=int(away), s=str(w.since), u=str(w.until))
        if not f:
            continue
        f = f[0]
        comp = w.comp(f["competition_uid"])
        goals = w.rows("select g.minute, g.added_time, g.side, g.kind, p.name from goals g left join people p on p.fm_uid = g.scorer_uid "
                       "where g.fixture_id = :f and g.kind in (0, 1, 2) order by g.minute, g.seq", f=f["id"])
        best = w.rows("select p.name from match_reports r join people p on p.fm_uid = r.player_of_the_match_uid where r.fixture_id = :f", f=f["id"])
        said = {k: v for k, v in (article.lead or {}).get("facts", {}).items() if k in WHAT_WE_SAID}
        facts = {"competition": comp["name"], "date": str(f["date"]), "home": w.name(f["home_uid"]), "away": w.name(f["away_uid"]),
                 "score": _score_text(f), **_own_league_facts(w, f["competition_uid"], (("home", f["home_uid"]), ("away", f["away_uid"]))),
                 "stage": _stage_words(phase) if not _tie(w, f) and (phase := _phase_table(w, f["competition_uid"], {})[0]) else None,
                 **(_tie(w, f) or {}),
                 "goals": [f"{('own goal by ' if g['kind'] == 1 else '') + (g['name'] or 'unknown')} {g['minute']}{'+' + str(g['added_time']) if g['added_time'] else ''}'{' (pen)' if g['kind'] == 2 else ''} "
                           f"({w.name(f['home_uid'] if g['side'] == 0 else f['away_uid'])})" for g in goals] or None,
                 "how_the_goals_went_in": _how_goals_went_in(w, f),
                 "player_of_the_match": best[0]["name"] if best else None,
                 "what_we_said": {"headline": article.headline, "standfirst": article.standfirst, "published": str(article.issue.publication_date), **said}}
        leads.append(Lead("promised_report", f"As previewed: {w.name(f['home_uid'])} {_score_text(f)} {w.name(f['away_uid'])} ({comp['name']})",
                          date.fromisoformat(str(f["date"])), w.weight(f["competition_uid"]) * 10 * PROMISED_SIZE,
                          {k: v for k, v in facts.items() if v is not None},
                          [f"match:{f['date']}:{f['home_uid']}:{f['away_uid']}", f"club:{f['home_uid']}", f"club:{f['away_uid']}"],
                          w.beats_for(f["home_uid"], f["away_uid"], competition_uid=f["competition_uid"]), outlet_id=article.issue.outlet_id))
    return leads




# -- the followed clubs' every game: a preview and a report, for their own paper ------------------------------------------------
CLUB_PREVIEW_SIZE, CLUB_REPORT_SIZE = 0.9, PROMISED_SIZE + 0.1  # the report outranks the promised report of the same game
FRIENDLY_WEIGHT = 3.0


def _first_teams(w) -> set[int]:
    """The followed clubs' first teams (not their U21 and U18 sides)."""
    if getattr(w, "_first_teams", None) is None:
        clubs = ",".join(str(int(u)) for u in w.followed_clubs) or "0"
        w._first_teams = {r["fm_uid"] for r in w.rows(f"select fm_uid from teams where club_uid in ({clubs}) and age_limit is null")}
    return w._first_teams


def _is_friendly(w, competition_uid: int | None) -> bool:
    scope = w.comp(competition_uid).get("scope")
    return scope == "friendly" or not w.comp(competition_uid).get("name")


def followed_games(w) -> list:
    """Every game of a followed club's first team: a preview of each one in the next two weeks and a report of each one
    played in the cycle, for the club's own paper (the home beat), whatever the game. A friendly is a brief. The report
    settles what the paper's preview said when there was one."""
    from app.services.story_leads import Lead

    teams = _first_teams(w)
    if not teams:
        return []
    marks = ",".join(str(t) for t in teams)
    tables = {league: _table(w, league) for league in current_leagues(w)}
    previewed = {}
    for article in published_previews():
        _, _, day, home, away, *_ = article.lead_key.split(":")
        previewed[(day, int(home), int(away))] = article
    leads = []
    for f in w.rows(f"select * from fixtures where (home_uid in ({marks}) or away_uid in ({marks})) and home_goals is not null "
                    f"and date > :s and date <= :u order by date", s=str(w.since), u=str(w.until)):
        home, away, league = f["home_uid"], f["away_uid"], f["competition_uid"]
        friendly = _is_friendly(w, league)
        goals = w.rows("select g.minute, g.added_time, g.side, g.kind, p.name from goals g left join people p on p.fm_uid = g.scorer_uid "
                       "where g.fixture_id = :f and g.kind in (0, 1, 2) order by g.minute, g.seq", f=f["id"])
        best = w.rows("select p.name from match_reports r join people p on p.fm_uid = r.player_of_the_match_uid where r.fixture_id = :f", f=f["id"])
        facts = {"competition": _comp_name(w, league), "date": str(f["date"]), "home": w.name(home), "away": w.name(away), "score": _score_text(f),
                 "goals": [f"{('own goal by ' if g['kind'] == 1 else '') + (g['name'] or 'unknown')} {g['minute']}{'+' + str(g['added_time']) if g['added_time'] else ''}'{' (pen)' if g['kind'] == 2 else ''} "
                           f"({w.name(home if g['side'] == 0 else away)})" for g in goals] or None,
                 "how_the_goals_went_in": _how_goals_went_in(w, f),
                 "player_of_the_match": best[0]["name"] if best else None,
                 "treatment": "a brief: this was a friendly" if friendly else None,
                 **_own_league_facts(w, league, (("home", home), ("away", away)))}
        table, place, _ = _phase_table(w, league, tables)
        knockout = _tie(w, f)
        if knockout:
            facts.update(knockout)
        elif w.comp(league).get("scope") == "continental" and table:
            facts["stage"] = _stage_words(table)
        for side, team in (("home", home), ("away", away)):
            if team in place:
                facts[f"{side}_place_now"] = _place_words(w, league, table, place, team, form=False)
            if league in tables:
                facts[f"{side}_league_season"] = _league_record(w, team, league, str(f["date"]))
        article = previewed.get((str(f["date"]), home, away))
        if article is not None:
            facts["what_we_said"] = {"headline": article.headline, "standfirst": article.standfirst, "published": str(article.issue.publication_date),
                                     **{k: v for k, v in (article.lead or {}).get("facts", {}).items() if k in WHAT_WE_SAID}}
        weight = FRIENDLY_WEIGHT if friendly else w.weight(league)
        leads.append(Lead("club_report", f"{w.name(home)} {_score_text(f)} {w.name(away)} ({_comp_name(w, league)})", date.fromisoformat(str(f["date"])[:10]),
                          weight * 10 * (0.5 if friendly else CLUB_REPORT_SIZE), {k: v for k, v in facts.items() if v is not None},
                          [f"match:{f['date']}:{home}:{away}", f"club:{home}", f"club:{away}"], {"home"}))
    for f in w.rows(f"select * from fixtures where (home_uid in ({marks}) or away_uid in ({marks})) and home_goals is null "
                    f"and date > :u and date <= :e order by date", u=str(w.until), e=str(w.until + timedelta(days=PREVIEW_DAYS))):
        home, away, league = f["home_uid"], f["away_uid"], f["competition_uid"]
        friendly = _is_friendly(w, league)
        if not friendly and not _next_in_competition(w, f):
            continue
        table, place, _ = _phase_table(w, league, tables)
        continental = w.comp(league).get("scope") == "continental"
        facts = {"competition": _comp_name(w, league), "date": str(f["date"]), "home": w.name(home), "away": w.name(away),
                 "treatment": "a brief: this is a friendly" if friendly else None, "league_rules": _league_rules(w, league) if place else None,
                 **_own_league_facts(w, league, (("home", home), ("away", away))),
                 "stage": _stage_words(table) if continental and table and not _tie(w, f) else None, **(_tie(w, f) or {})}
        for side, team in (("home", home), ("away", away)):
            if team in place:
                facts[f"{side}_place"] = _place_words(w, league, table, place, team)
            if league in tables:
                facts[f"{side}_league_season"] = _league_record(w, team, league, str(w.until), "so far")
            if continental:
                facts[f"{side}_seasons_in_european_competition"] = _european_seasons(w, team)
            injured = _injured_regulars(w, team)
            if injured:
                facts[f"{side}_injured"] = injured
        meetings = w.rows("select * from fixtures where ((home_uid = :h and away_uid = :a) or (home_uid = :a and away_uid = :h)) "
                          "and home_goals is not null order by date desc limit 3", h=home, a=away)
        if meetings:
            facts["last_meetings"] = [_result_line(w, m) for m in meetings]
        bonus, drama = _drama(w, f, table, place) if not friendly else (0, {})
        facts.update(drama)
        weight = FRIENDLY_WEIGHT if friendly else w.weight(league)
        leads.append(Lead("club_preview", f"Next up: {w.name(home)} v {w.name(away)}, {f['date']} ({_comp_name(w, league)})", w.until,
                          weight * 10 * ((0.5 if friendly else CLUB_PREVIEW_SIZE) + bonus), {k: v for k, v in facts.items() if v is not None},
                          [f"match:{f['date']}:{home}:{away}", f"club:{home}", f"club:{away}"], {"home"}))
    return leads


# -- the scouted: a follow-up a year after the scouting report, and another a year after that -------------------------------
FOLLOW_UP_DAYS = (365, 730)
FOLLOW_UP_SCORE = 45.0  # a planned piece: it makes the young paper whatever else is in


def published_scouting_reports() -> list:
    """The scouting reports the papers have published; none outside the archive app."""
    from sqlalchemy.exc import OperationalError

    from app.models.world_soccer_article import WorldSoccerArticle
    from app.models.world_soccer_issue import WorldSoccerIssue

    try:
        return (WorldSoccerArticle.query.join(WorldSoccerIssue).filter(WorldSoccerIssue.status == "published",
                                                                       WorldSoccerArticle.lead_key.like("scouting:person:%")).all())
    except (RuntimeError, OperationalError):
        return []


def published_follow_up_verdicts() -> dict[tuple[int, int], str]:
    """The verdict of each published scouting follow-up, by (player, stage): "right", "early" or "wrong"."""
    from sqlalchemy.exc import OperationalError

    from app.models.world_soccer_article import WorldSoccerArticle
    from app.models.world_soccer_issue import WorldSoccerIssue

    try:
        rows = (WorldSoccerArticle.query.join(WorldSoccerIssue).filter(WorldSoccerIssue.status == "published",
                                                                       WorldSoccerArticle.lead_key.like("scouting_follow_up:%")).all())
    except (RuntimeError, OperationalError):
        return {}
    found = {}
    for article in rows:
        _, person, stage = article.lead_key.split(":")  # scouting_follow_up:<person>:<stage>
        if (article.lead or {}).get("verdict"):
            found[(int(person), int(stage))] = article.lead["verdict"]
    return found


def scouting_follow_ups(w) -> list:
    """A year after a scouting report, and two years after, the same paper looks the player up again, whatever else has
    been written about him: what the report said, and what happened since (his moves, his seasons, his injuries, what he
    is now), for the paper to settle its own call. A player the paper called wrong at one year is dropped: the two-year
    piece is for the ones still worth following."""
    from app.services.story_leads import Lead, level_words

    verdicts = published_follow_up_verdicts()
    leads = []
    for article in published_scouting_reports():
        _, _, person, *_ = article.lead_key.split(":")
        person = int(person)
        printed = date.fromisoformat(str(article.issue.publication_date)[:10])
        for stage, days in enumerate(FOLLOW_UP_DAYS, start=1):
            due = printed + timedelta(days=days)
            if not (w.since < due <= w.until):
                continue
            if stage > 1 and verdicts.get((person, stage - 1)) == "wrong":
                continue
            p = w.rows("select p.*, n.adjective from people p left join nations n on n.fm_uid = p.nation_uid where p.fm_uid = :p", p=person)
            if not p:
                continue
            p = p[0]
            said = (article.lead or {}).get("facts", {})
            age = w.until.year - p["birth_date"].year - ((w.until.month, w.until.day) < (p["birth_date"].month, p["birth_date"].day)) if p["birth_date"] else None
            moves = w.rows("select t.date, t.kind, t.fee, f.name from_name, o.name to_name from transfers t left join teams f on f.fm_uid = t.from_uid "
                           "left join teams o on o.fm_uid = t.to_uid where t.person_uid = :p and t.date > :s and t.date <= :u order by t.date",
                           p=person, s=str(printed), u=str(w.until))
            seasons = w.rows("select s.season_start, t.name team, sum(s.starts + s.substitute_appearances) games, sum(s.goals) goals, sum(s.assists) assists, "
                             "sum(s.rating_total) rating_total, sum(s.rated_appearances) rated from player_seasons s join teams t on t.fm_uid = s.team_uid "
                             "where s.person_uid = :p and s.kind in (1, 2, 3) and s.season_start >= :y group by s.season_start, s.team_uid "
                             "order by s.season_start, games desc", p=person, y=printed.year - 1)
            injuries = w.rows("select injury, injured_on, days from injuries where person_uid = :p and injured_on > :s and injured_on <= :u and days >= 21 "
                              "order by injured_on", p=person, s=str(printed), u=str(w.until))
            team = w.team(p["team_uid"])
            facts = {"player": p["name"], "age_now": age, "team_now": team.get("name"), "years_since_our_report": stage,
                     "what_we_said": {"published": str(printed), "headline": article.headline, "standfirst": article.standfirst,
                                      **{k: said[k] for k in ("age", "team", "scout_view", "standout_traits", "this_season", "signed") if k in said}},
                     "moves_since": [f"{m['date']}: {q.TRANSFER_KINDS.get(m['kind'], 'moved').lower()} from {m['from_name'] or 'no club'} to {m['to_name'] or 'no club'}"
                                     + (f", £{m['fee'] / 1e6:.1f}m" if m["fee"] else "") for m in moves] or None,
                     "seasons_since": [f"{q.season_label(r['season_start'])}, {r['team']}: {r['games'] or 0} games, {r['goals'] or 0} goals, {r['assists'] or 0} assists"
                                       + (f", rated {r['rating_total'] / r['rated'] / 10:.2f}" if r["rated"] else "") for r in seasons] or None,
                     "injuries_since": [f"{i['injury']}, {i['injured_on']}, {i['days']} days out" for i in injuries] or None,
                     "level_now": level_words(age, p["current_ability"]) if age and p["current_ability"] else None}
            leads.append(Lead("scouting_follow_up", f"{p['name']}, {stage} year{'s' if stage > 1 else ''} after our scouting report", w.until, FOLLOW_UP_SCORE,
                              {k: v for k, v in facts.items() if v is not None}, [f"person:{person}"] + ([f"club:{team.get('club_uid')}"] if team.get("club_uid") else []),
                              set((article.lead or {}).get("beats") or ["world", "young"]), slug=f"{person}:{stage}", outlet_id=article.issue.outlet_id))
    return leads




# -- contracts: a king's wage abroad, a deal running down, a manager out ------------------------------------------------------
KING_WAGE = 300_000  # pounds a week: a former followed-club player paid this abroad is a story
KING_TIMES = 2.0  # ...and at least this many times the followed clubs' best-paid player
RUNNING_DOWN_DAYS = 365
RUNNING_DOWN_STARTS = 5  # league starts this season: a regular
RUNNING_DOWN_AGE = 34  # older players' deals run down without a story
MANAGER_OUT_REPUTATION = 7500  # a club this well known losing its manager is news everywhere
MANAGER_OUT_REASONS = {0: "resigned", 2: "sacked", 6: "retired"}
KING_SIZE, RUNNING_DOWN_SIZE, MANAGER_OUT_SIZE = 1.4, 1.2, 1.6


def _happiness_word(value: int | None) -> str | None:
    if value is None:
        return None
    for floor, word in fm_save.HAPPINESS_WORDS:
        if value >= floor:
            return word.lower()
    return "unhappy" if value < 0 else "content"


def _position_words(w, person: int) -> str | None:
    """The player's position as the game labels it ("Goalkeeper", "Defender (Right, Centre)"), from his latest attributes;
    None when unknown (then the writer must not guess one)."""
    found = q.player_positions(q.latest_attributes(w.c, person))
    return found.get("label") if found else None


def kings_wages(w) -> list:
    """A former followed-club player paid a king's wage abroad: Rogério José, sold by Chelsea to Al-Hilal for £41.7M in June
    2063, on £1,400,000 a week there. Once a season per player, with his years at the club and the deal that took him."""
    from app.services.story_leads import Lead

    teams = _first_teams(w)
    if not teams:
        return []
    marks = ",".join(str(t) for t in teams)
    top = w.rows(f"select max(c.wage) wage from contracts c where c.team_uid in ({marks}) and c.job = 'Player' and c.as_of = "
                 f"(select max(as_of) from contracts)")
    top_wage = (top[0]["wage"] or 0) if top else 0
    leads = []
    for r in w.rows(f"select c.person_uid, c.wage, c.expires, c.signed, p.name, t.name club, c.team_uid, p.birth_date, n.adjective "
                    f"from contracts c join people p on p.fm_uid = c.person_uid join teams t on t.fm_uid = c.team_uid "
                    f"left join nations n on n.fm_uid = p.nation_uid where c.job = 'Player' and c.as_of = (select max(as_of) from contracts) "
                    f"and c.team_uid = p.team_uid and c.team_uid not in ({marks}) and c.wage >= :k "
                    f"and c.person_uid in (select person_uid from transfers where from_uid in ({marks})) order by c.wage desc", k=KING_WAGE):
        if top_wage and r["wage"] < top_wage * KING_TIMES:
            continue
        spells = w.rows(f"select min(season_start) first, max(season_start) last, sum(starts + substitute_appearances) games, sum(goals) goals "
                        f"from player_seasons where person_uid = :p and team_uid in ({marks}) and kind in (1, 2, 3)", p=r["person_uid"])
        move = w.rows(f"select x.date, x.fee, f.name from_name, o.name to_name from transfers x left join teams f on f.fm_uid = x.from_uid "
                      f"left join teams o on o.fm_uid = x.to_uid where x.person_uid = :p and x.from_uid in ({marks}) order by x.date desc limit 1", p=r["person_uid"])
        spell = spells[0] if spells and spells[0]["games"] else None
        facts = {"player": r["name"], "position": _position_words(w, r["person_uid"]), "nationality": r["adjective"], "club_now": r["club"],
                 "wage_now": q.wage_words(r["wage"]), "deal_runs_to": str(r["expires"]) if r["expires"] else None,
                 "deal_signed": str(r["signed"]) if r["signed"] else None,
                 "his_years_at_the_club": f"{q.season_label(spell['first'])} to {q.season_label(spell['last'])}, {spell['games']} games, {spell['goals']} goals" if spell else None,
                 "the_move": f"{move[0]['date']}: {move[0]['from_name']} to {move[0]['to_name']}" + (f", £{move[0]['fee'] / 1e6:.1f}m" if move[0]["fee"] else "") if move else None,
                 "the_old_clubs_best_paid_player_earns": q.wage_words(top_wage) if top_wage else None}
        leads.append(Lead("kings_wage", f"{r['name']} on {q.wage_words(r['wage'])} at {r['club']}", w.until, 10 * 8 * KING_SIZE,
                          {k: v for k, v in facts.items() if v is not None}, [f"person:{r['person_uid']}", f"club:{r['team_uid']}"],
                          {"world", "home", "transfers"}, slug=f"{r['person_uid']}:{w.season}"))
    return leads


def contracts_running_down(w) -> list:
    """A followed club's regular, under 34, with less than a year left on his deal: the wage, the end date, his season, his
    mood. Once per deal."""
    from app.services.story_leads import Lead

    teams = _first_teams(w)
    if not teams:
        return []
    marks = ",".join(str(t) for t in teams)
    leads = []
    for r in w.rows(f"select c.person_uid, c.wage, c.expires, c.signed, c.happiness, p.name, p.birth_date, t.name club, c.team_uid "
                    f"from contracts c join people p on p.fm_uid = c.person_uid join teams t on t.fm_uid = c.team_uid "
                    f"where c.job = 'Player' and c.team_uid in ({marks}) and c.team_uid = p.team_uid and c.as_of = (select max(as_of) from contracts) "
                    f"and c.expires is not null and c.expires > :u and c.expires <= :e order by c.wage desc",
                    u=str(w.until), e=str(w.until + timedelta(days=RUNNING_DOWN_DAYS))):
        age = q.age_on(r["birth_date"], w.until) if r["birth_date"] else None
        if age and age >= RUNNING_DOWN_AGE:
            continue
        season = w.rows("select sum(starts) starts, sum(starts + substitute_appearances) games, sum(goals) goals from player_seasons "
                        "where person_uid = :p and team_uid = :t and season_start = :s and kind in (1, 2, 3)", p=r["person_uid"], t=r["team_uid"], s=w.season)
        if not season or (season[0]["starts"] or 0) < RUNNING_DOWN_STARTS:
            continue
        facts = {"player": r["name"], "position": _position_words(w, r["person_uid"]), "age": age, "club": r["club"], "wage": q.wage_words(r["wage"]),
                 "deal_ends": str(r["expires"]),
                 "deal_signed": str(r["signed"]) if r["signed"] else None, "mood": _happiness_word(r["happiness"]),
                 "this_season": f"{season[0]['games']} games, {season[0]['starts']} starts, {season[0]['goals'] or 0} goals",
                 "months_left": max(1, round((date.fromisoformat(str(r["expires"])[:10]) - w.until).days / 30))}
        leads.append(Lead("contract_running_down", f"{r['name']}'s {r['club']} deal runs out on {r['expires']}", w.until, 10 * 8 * RUNNING_DOWN_SIZE,
                          {k: v for k, v in facts.items() if v is not None}, [f"person:{r['person_uid']}", f"club:{r['team_uid']}"],
                          {"world", "home", "transfers"}, slug=f"{r['person_uid']}:{r['expires']}"))
    return leads


def managers_out(w) -> list:
    """A high-profile club's manager sacked, resigned or retired in the cycle: his spell's record and honours, the club's
    standing, the successor when one is in."""
    from app.services.story_leads import Lead

    leads = []
    for r in w.rows("select s.*, p.name, t.name club, t.reputation from manager_spells s join people p on p.fm_uid = s.person_uid "
                    "join teams t on t.fm_uid = s.team_uid where s.ended > :s and s.ended <= :u order by t.reputation desc", s=str(w.since), u=str(w.until)):
        reason = MANAGER_OUT_REASONS.get(r["reason"])
        if reason is None or ((r["reputation"] or 0) < MANAGER_OUT_REPUTATION and r["team_uid"] not in w.followed_teams):
            continue
        games = (r["won"] or 0) + (r["drawn"] or 0) + (r["lost"] or 0)
        after = w.rows("select p.name, s.started from manager_spells s join people p on p.fm_uid = s.person_uid where s.team_uid = :t "
                       "and s.started >= :e and s.person_uid != :p order by s.started limit 1", t=r["team_uid"], e=str(r["ended"]), p=r["person_uid"])
        standing = None
        league = w.rows("select league_uid from player_seasons where team_uid = :t and kind = 1 and league_uid is not null and season_start = :s limit 1",
                        t=r["team_uid"], s=w.season)
        if league:
            table, place, _ = _table(w, league[0]["league_uid"])
            if r["team_uid"] in place:
                row = table[place[r["team_uid"]] - 1]
                standing = f"{place[r['team_uid']]} of {len(table)}, {row['points']} points from {row['played']} games, form {''.join(row['form'])}"
        facts = {"manager": r["name"], "club": r["club"], "how": reason, "on": str(r["ended"]), "in_charge_since": str(r["started"]) if r["started"] else None,
                 "record": f"{games} games: {r['won']} won, {r['drawn']} drawn, {r['lost']} lost" if games else None,
                 "honours_in_the_spell": f"{r['league_wins']} league titles, {r['cup_wins']} cups" if (r["league_wins"] or r["cup_wins"]) else None,
                 "league_standing_now": standing, "successor": f"{after[0]['name']} from {after[0]['started']}" if after else None,
                 "linked_with_the_job": list(dict.fromkeys(f"{x['person']} ({x['team']})" for x in _news(
                     w, "where n.kind = 'job_link' and n.other_team_uid = :t and n.day >= :e", t=r["team_uid"], e=str(r["ended"]))))[:6] or None}
        size = MANAGER_OUT_SIZE + (0.4 if reason == "sacked" else 0) + (0.4 if r["team_uid"] in w.followed_teams else 0)
        leads.append(Lead("manager_out", f"{r['name']} {reason} at {r['club']}", date.fromisoformat(str(r["ended"])[:10]), 10 * 8 * size,
                          {k: v for k, v in facts.items() if v is not None}, [f"person:{r['person_uid']}", f"club:{r['team_uid']}"],
                          w.beats_for(r["team_uid"]) | {"world"}, slug=f"{r['person_uid']}:{r['team_uid']}:{r['ended']}"))
    return leads


def _news(w, sql: str, **params) -> list[dict]:
    """news_items rows, none when the archive has no such table yet (imported before 26 Oct 2026)."""
    try:
        return w.rows("select n.*, p.name person, o.name other_person, t.name team, ot.name other_team from news_items n "
                      "left join people p on p.fm_uid = n.person_uid left join people o on o.fm_uid = n.other_person_uid "
                      "left join teams t on t.fm_uid = n.team_uid left join teams ot on ot.fm_uid = n.other_team_uid " + sql, **params)
    except Exception:  # noqa: BLE001
        return []


PRESSURE_SIZE = 0.7
RUMOUR_FEE = 20_000_000  # a rumoured fee this big is a story anywhere; a followed club's player always


def under_pressure(w) -> list:
    """The board said to be losing patience with a manager, a coming match called decisive (the game's news, decoded)."""
    from app.services.story_leads import Lead

    leads = []
    for r in _news(w, "where n.kind = 'board_pressure' and n.day > :s and n.day <= :u", s=str(w.since), u=str(w.until)):
        nxt = w.rows("select * from fixtures where ((home_uid = :t and away_uid = :o) or (home_uid = :o and away_uid = :t)) and date >= :d "
                     "order by date limit 1", t=r["team_uid"], o=r["other_team_uid"], d=str(r["day"]))
        last = w.rows("select * from fixtures where (home_uid = :t or away_uid = :t) and date <= :u and home_goals is not null "
                      "order by date desc limit 5", t=r["team_uid"], u=str(w.until))
        facts = {"manager": r["person"], "club": r["team"], "reported_on": str(r["day"]),
                 "the_report": f"the {r['team']} board are said to be losing patience with {r['person']}; the game against {r['other_team']} could decide his fate",
                 "the_decisive_game": (f"{nxt[0]['date']}: {w.name(nxt[0]['home_uid'])} v {w.name(nxt[0]['away_uid'])}" + (f", {_score_text(nxt[0])}" if nxt[0]["home_goals"] is not None else ""))
                 if nxt else None,
                 "the_clubs_last_results": [f"{x['date']}: {w.name(x['home_uid'])} {_score_text(x)} {w.name(x['away_uid'])}" for x in last] or None}
        size = PRESSURE_SIZE * (1 + (w.rep(r["team_uid"]) or 0) / 20_000) * (1.3 if w.followed(r["team_uid"]) else 1.0)
        leads.append(Lead("under_pressure", f"{r['person']} under pressure at {r['team']}", w.until, 10 * 8 * size,
                          {k: v for k, v in facts.items() if v}, [f"person:{r['person_uid']}", f"club:{r['team_uid']}"],
                          w.beats_for(r["team_uid"]), slug=f"{r['person_uid']}:{r['day']}"))
    return leads


def vacancies(w) -> list:
    """A club without a manager and the men linked with the job in the cycle (the game's news, decoded)."""
    from app.services.story_leads import Lead

    by_club: dict[int, list[dict]] = {}
    for r in _news(w, "where n.kind = 'job_link' and n.day > :s and n.day <= :u order by n.day", s=str(w.since), u=str(w.until)):
        by_club.setdefault(r["other_team_uid"], []).append(r)
    leads = []
    for club, links in by_club.items():
        names = list(dict.fromkeys(f"{r['person']} ({r['team']})" for r in links))
        left = next((r["other_person"] for r in links if r["other_person"]), None)
        facts = {"club": links[0]["other_team"], "the_manager_who_left": left, "linked_with_the_job": names,
                 "note": "linked by the press; some have denied or said nothing. No appointment is known."}
        size = PRESSURE_SIZE * (1 + (w.rep(club) or 0) / 20_000) * (1 + 0.1 * min(len(names), 4))
        leads.append(Lead("vacancy", f"Who takes the {links[0]['other_team']} job?", w.until, 10 * 8 * size, facts,
                          [f"club:{club}"] + [f"person:{r['person_uid']}" for r in links[:4]], w.beats_for(club), slug=f"{club}:{w.until}"))
    return leads


def transfer_rumours(w) -> list:
    """A club after a player with a reported fee, first seen in the cycle: a followed club's player, or a fee of RUMOUR_FEE."""
    from app.services.story_leads import Lead

    leads = []
    for r in _news(w, "where n.kind = 'transfer_rumour' and n.day > :s and n.day <= :u", s=str(w.since), u=str(w.until)):
        own = w.rows("select team_uid from people where fm_uid = :p", p=r["person_uid"])
        club = own[0]["team_uid"] if own else None
        if not (w.followed(club, r["team_uid"]) or (r["fee"] or 0) >= RUMOUR_FEE):
            continue
        facts = {"player": r["person"], "his_club": w.name(club), "the_club_after_him": r["team"], "reported_fee": f"about £{r['fee'] / 1e6:.0f}M", "status": "a press report, no bid is known",
                 **_record_facts(w, r["person_uid"], club), **_deal_facts(w, r["person_uid"])}
        score = (w.rep(club) or 3000) / 1000 * TRANSFER_SCALE * (1.2 + min(r["fee"] or 0, 100_000_000) / 100_000_000) * (1.3 if w.followed(club) else 1.0)
        leads.append(Lead("transfer_rumour", f"{r['team']} after {r['person']} ({w.name(club)}), about £{r['fee'] / 1e6:.0f}M", w.until, score,
                          {k: v for k, v in facts.items() if v}, [f"person:{r['person_uid']}", f"club:{club}", f"club:{r['team_uid']}"],
                          w.beats_for(club, r["team_uid"]) | {"transfers"}, slug=f"{r['person_uid']}:{r['team_uid']}:{r['fee']}"))
    return leads


FEUD_DAYS = 21  # two managers' sharp answers about each other within this many game days make a war of words
SHARP = ("negative", "very negative")


def wars_of_words(w) -> list:
    """Two managers who said sharp things about each other at press conferences, at least one of them in the cycle: the
    exchanges as the game's headlines, with the match between them (Dysart and Pladevall, Reading v Liverpool, 19 Dec 2063)."""
    from app.services.story_leads import Lead

    try:
        rows = w.rows("select * from press_answers where speaker_uid is not null and tone in ('negative', 'very negative') "
                      "and headline is not null and names_subject = 1 and day > :s and day <= :u order by day",
                      s=str(w.since - timedelta(days=FEUD_DAYS)), u=str(w.until))
    except Exception:  # noqa: BLE001 - no press answers table yet
        return []
    by_pair: dict[tuple[int, int], list[dict]] = {}
    for r in rows:
        by_pair.setdefault((r["speaker_uid"], r["subject_uid"]), []).append(r)
    leads, done = [], set()
    for (a, b), said in by_pair.items():
        back = by_pair.get((b, a))
        pair = tuple(sorted((a, b)))
        if not back or pair in done:
            continue
        done.add(pair)
        exchanges = sorted(said + back, key=lambda r: str(r["day"]))
        if str(exchanges[-1]["day"]) <= str(w.since):
            continue  # nothing new in this cycle
        if exchanges[0]["speaker_uid"] != a:  # the one who spoke first is named first
            a, b, said, back = b, a, back, said
        names = {r["fm_uid"]: r["name"] for r in w.rows(f"select fm_uid, name from people where fm_uid in ({a}, {b})")}
        clubs = {a: said[-1]["team_uid"], b: back[-1]["team_uid"]}
        meeting = w.rows("select * from fixtures where ((home_uid = :x and away_uid = :y) or (home_uid = :y and away_uid = :x)) "
                         "and date <= :u and home_goals is not null order by date desc limit 1", x=clubs[a], y=clubs[b], u=str(w.until))
        facts = {"managers": [f"{names.get(a)} ({w.name(clubs[a])})", f"{names.get(b)} ({w.name(clubs[b])})"],
                 "exchanges": [f"{r['day']}: {r['headline']} ({r['tone']})" for r in exchanges][-8:],
                 "their_last_meeting": (f"{meeting[0]['date']}: {w.name(meeting[0]['home_uid'])} {_score_text(meeting[0])} "
                                        f"{w.name(meeting[0]['away_uid'])}") if meeting else None}
        big = max(w.rep(clubs[a]) or 0, w.rep(clubs[b]) or 0)
        score = 50 * (1 + big / 20_000) * (1.2 if any(r["tone"] == "very negative" for r in exchanges) else 1.0) \
            * (1.3 if w.followed(clubs[a], clubs[b]) else 1.0)
        leads.append(Lead("war_of_words", f"War of words: {names.get(a)} and {names.get(b)}", w.until, score,
                          {k: v for k, v in facts.items() if v}, [f"person:{a}", f"person:{b}", f"club:{clubs[a]}", f"club:{clubs[b]}"],
                          w.beats_for(clubs[a], clubs[b]), slug=f"{pair[0]}:{pair[1]}:{str(exchanges[-1]['day'])[:7]}"))
    return leads


# -- finishing: strikers far from their expected goals ------------------------------------------------------------------------
FINISHING_XG = 4.0  # league xG before a player's finishing is a story
MISFIRING_GAP, MISFIRING_SHARE = 3.0, 0.65  # goals at least this far below xG, and at most this share of it
CLINICAL_GAP, CLINICAL_SHARE = 3.0, 1.5  # goals at least this far above xG, and at least this share of it


def finishing(w) -> list:
    """Misfiring strikers and clinical ones (27 Oct 2026): league goals far below or far above the expected goals of their
    chances this season, from the match reports. The game's xG counts penalties, so the penalties scored are given too."""
    from app.services.story_leads import Lead

    leads = []
    for league in current_leagues(w):
        start, end = _window(w, league)
        rows = w.rows("select mp.person_uid, case mp.side when 0 then f.home_uid else f.away_uid end team, count(*) games, sum(mp.goals) goals, "
                      "sum(mp.xg) xg, sum(mp.shots) shots, sum(mp.shots_on_target) on_target, max(case when mp.goals > 0 then f.date end) last_goal, "
                      "max(f.date) last_game from match_players mp join fixtures f on f.id = mp.fixture_id "
                      "where f.competition_uid = :c and f.date >= :s and f.date <= :u and mp.played > 0 and mp.person_uid is not null "
                      "group by mp.person_uid having sum(mp.xg) >= :x", c=league, s=start, u=end, x=FINISHING_XG)
        for r in rows:
            gap = r["goals"] - r["xg"]
            if gap <= -MISFIRING_GAP and r["goals"] <= MISFIRING_SHARE * r["xg"]:
                kind = "misfiring"
            elif gap >= CLINICAL_GAP and r["goals"] >= CLINICAL_SHARE * r["xg"]:
                kind = "clinical"
            else:
                continue
            if str(r["last_game"]) <= str(w.since):
                continue  # nothing new since the last editions
            person = w.rows("select name, birth_date, reputation_world from people where fm_uid = :p", p=r["person_uid"])
            if not person:
                continue
            person = person[0]
            pens = w.rows("select count(*) n from goals g join fixtures f on f.id = g.fixture_id where g.scorer_uid = :p and g.kind = 2 "
                          "and f.competition_uid = :c and f.date >= :s and f.date <= :u", p=r["person_uid"], c=league, s=start, u=end)[0]["n"]
            since_goal = w.rows("select count(*) n from match_players mp join fixtures f on f.id = mp.fixture_id where mp.person_uid = :p "
                                "and mp.played > 0 and f.competition_uid = :c and f.date > :d and f.date <= :u",
                                p=r["person_uid"], c=league, d=str(r["last_goal"] or start), u=end)[0]["n"]
            facts = {"player": person["name"], "age": q.age_on(person["birth_date"], w.until) if person["birth_date"] else None,
                     "club": w.name(r["team"]), "competition": _comp_name(w, league),
                     "position": position_words(q.main_positions(w.c, [r["person_uid"]]).get(r["person_uid"])),
                     "league_games": r["games"], "league_goals": r["goals"], "of_them_penalties": pens or None,
                     "expected_goals": round(r["xg"], 1), "goals_against_expected": f"{gap:+.1f}",
                     "shots": r["shots"], "on_target": r["on_target"],
                     "conversion": f"{round(100 * r['goals'] / r['shots'])}% of shots" if r["shots"] else None,
                     "league_games_since_his_last_goal": since_goal if kind == "misfiring" else None,
                     "last_league_goal": str(r["last_goal"])[:10] if r["last_goal"] else "none this season",
                     "standing": "a big name" if (person["reputation_world"] or 0) >= INTEREST_PLAYER else None}
            size = 0.45 + min(abs(gap), 8) / 16 + (person["reputation_world"] or 0) / 40000 + (0.2 if w.followed(r["team"]) else 0)
            what = f"{r['goals']} goals from {r['xg']:.1f} expected" + (" and firing blanks" if kind == "misfiring" else ", finishing far above it")
            leads.append(Lead(kind, f"{person['name']} ({w.name(r['team'])}): {what}", w.until, w.weight(league) * 10 * size,
                              {k: v for k, v in facts.items() if v is not None}, [f"person:{r['person_uid']}", f"club:{r['team']}"],
                              w.beats_for(r["team"], competition_uid=league) | {"numbers"}, slug=f"{r['person_uid']}:{w.until:%Y-%m}"))
    best: dict[str, list] = {}
    for lead in sorted(leads, key=lambda lead: -lead.score):
        best.setdefault(lead.kind, []).append(lead)
    return [lead for kind in best for lead in best[kind][:4]]


# -- tired legs: fatigue, the tiredness that builds over weeks ----------------------------------------------------------------------
TIRED_FATIGUE = 300  # the game's fatigue (-500 fresh .. +500 jaded) from which a player is worn down; 400 and over is jaded
JADED_FATIGUE = 400
TIRED_GAMES = 5  # games in the last TIRED_DAYS: a player who has not played is resting or injured, not tired from playing
TIRED_DAYS = 30
TIRED_NAME = 6000  # a player this well known (a followed club's player always)
MOODS_FRESH_DAYS = 7  # the moods are the newest save's: only for a cycle that ends near it


def tired_legs(w) -> list:
    """Well-known players worn down by the games (27 Oct 2026): the game's fatigue, the slow tiredness of weeks of matches
    that a day off does not clear (unlike a game's condition), high in the newest save, after many games of late."""
    from app.services.story_leads import Lead

    newest = w.rows("select max(as_of) d from person_moods")[0]["d"]
    if not newest or abs((date.fromisoformat(str(newest)[:10]) - w.until).days) > MOODS_FRESH_DAYS:
        return []
    since = str(w.until - timedelta(days=TIRED_DAYS))
    leads = []
    for r in w.rows("select m.person_uid, m.fatigue, p.name, p.birth_date, p.team_uid, p.reputation_world from person_moods m "
                    "join people p on p.fm_uid = m.person_uid where m.fatigue >= :f and p.team_uid is not null", f=TIRED_FATIGUE):
        if (r["reputation_world"] or 0) < TIRED_NAME and not w.followed(r["team_uid"]):
            continue
        games = w.rows("select f.date, f.competition_uid, mp.rating from match_players mp join fixtures f on f.id = mp.fixture_id "
                       "where mp.person_uid = :p and mp.played > 0 and f.date > :s and f.date <= :u order by f.date",
                       p=r["person_uid"], s=since, u=str(w.until))
        if len(games) < TIRED_GAMES:
            continue
        season = w.rows("select avg(mp.rating) r from match_players mp join fixtures f on f.id = mp.fixture_id where mp.person_uid = :p "
                        "and mp.played > 0 and f.date >= :s and mp.rating > 0", p=r["person_uid"], s=f"{w.season}-07-01")[0]["r"]
        recent = [g["rating"] for g in games[-5:] if g["rating"]]
        coming = w.rows("select count(*) n from fixtures where (home_uid = :t or away_uid = :t) and date > :u and date <= :e",
                        t=r["team_uid"], u=str(w.until), e=str(w.until + timedelta(days=14)))[0]["n"]
        jaded = r["fatigue"] >= JADED_FATIGUE
        facts = {"player": r["name"], "age": q.age_on(r["birth_date"], w.until) if r["birth_date"] else None, "club": w.name(r["team_uid"]),
                 "position": position_words(q.main_positions(w.c, [r["person_uid"]]).get(r["person_uid"])),
                 "his_state": ("jaded: worn down by weeks of games; it needs a real rest, a week or two out of the side, not a day off"
                               if jaded else "tired: the games are catching up with him; a rest now would stop him becoming jaded"),
                 "games_in_the_last_30_days": len(games),
                 "competitions_played_in": sorted({_comp_name(w, g["competition_uid"]) for g in games if g["competition_uid"]}),
                 "average_rating_last_5": round(sum(recent) / len(recent), 2) if recent else None,
                 "average_rating_this_season": round(season, 2) if season else None,
                 "his_clubs_games_in_the_next_14_days": coming or None, "manager": _manager(w, r["team_uid"])}
        size = 0.5 + (r["fatigue"] - TIRED_FATIGUE) / 500 + (r["reputation_world"] or 0) / 30000 + (0.3 if w.followed(r["team_uid"]) else 0)
        leads.append(Lead("tired_legs", f"{r['name']} ({w.name(r['team_uid'])}) {'jaded' if jaded else 'tiring'} after {len(games)} games in a month",
                          w.until, 10 * 4 * size, {k: v for k, v in facts.items() if v is not None},
                          [f"person:{r['person_uid']}", f"club:{r['team_uid']}"], w.beats_for(r["team_uid"]),
                          slug=f"{r['person_uid']}:{w.until:%Y-%m}"))
    return sorted(leads, key=lambda lead: -lead.score)[:6]


FINDERS = (round_ups, knockout_reports, finishing, tired_legs, derbies, runs, expectations, check_ins, form_players, stat_watch, injury_blows, previews, promised_reports, expected_points,
           continental_rounds, european_nights, international_rounds, national_teams, transfer_market, tournament_leads.tournament_coverage,
           followed_games, scouting_follow_ups, kings_wages, contracts_running_down, managers_out, features.travel_log, features.interviews, wars_of_words, under_pressure, vacancies, transfer_rumours)
