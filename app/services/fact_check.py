"""The editor: every hard fact in an article, checked against the archive before it goes in the paper (2 Nov 2026).

A report of Chelsea 2-1 Newcastle said "the champions' only league defeat this season came at Manchester City in
November"; Chelsea had won 4-0 there and lost at Rotherham. The year and score checks could not see it: no score was
written. So one more model call lists the article's hard facts (results, records, places, a player's numbers in the game,
ages, a career at a club, past meetings) in fixed fields, and this code, not the model, checks each one in the archive as
it stood on the article's day. A wrong fact goes back to the writer with the truth; a hard fact the archive cannot check
and the notebook does not give goes. Colour ("a low drive", "the crowd rose") is never a claim: the archive cannot know it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from flask import current_app

from app.services import world_queries as q

KINDS = ("result", "record", "form", "place", "fixture", "player_game", "goal_kind", "player_season", "player_age", "player_club",
         "meetings", "title_run", "other")
GAME_STATS = ("goals", "assists", "shots", "shots_on_target", "passes", "passes_completed", "key_passes", "tackles", "tackles_won",
              "interceptions", "headers_won", "dribbles", "fouls", "yellow_cards", "minutes", "conceded")
RECORD_FIELDS = ("played", "won", "drawn", "lost", "points", "unbeaten_run", "winning_run")

PROMPT = (
    "You are the paper's fact checker. The user message has the notebook the writer worked from and the article. List "
    "every hard fact in the article that the archive of results and statistics could check, each as one claim in fixed "
    "fields. A hard fact is a result, a win, draw or defeat, a team's record or run, a place or points in a table, a "
    "player's numbers in a game (goals, assists, passes, tackles...), an age, a player's games or goals for a club, past "
    "meetings between two clubs, or any other fact with a number, a date or a count (a title count, 'first since 2058'). "
    "Leave out colour and opinion, which no archive records: how a goal was scored, a mood, a quote, a description. "
    'Answer with JSON: {"claims": [{"sentence": "...", "kind": "...", ...}]}. "sentence" is the sentence that makes the '
    "claim, copied exactly, character for character. The kinds and their fields (teams and players by the names the article "
    "uses; numbers as numbers). Give only the fields the sentence itself states: a venue only when it says home, away, "
    "'at X' or 'travel to', a day only when it gives one; never fill a field from the notebook or from what you suppose:\n"
    '- "result": a game\'s outcome. team, opponent, outcome ("won", "drawn" or "lost", for team), score ("2-1", team\'s goals '
    'first), venue ("home" or "away", for team), month ("YYYY-MM") or day ("YYYY-MM-DD"), competition, '
    'this_season (true when the article places it this season). "Their only defeat came at X" is a result: team lost away at X.\n'
    '- "record": a team\'s league record this season. team, and any of: played, won, drawn, lost, points, unbeaten_run '
    "(league games without defeat up to now), winning_run (league wins in a row up to now).\n"
    '- "form": a team\'s last league games. team, last (how many games), won, drawn, lost (how many of them).\n'
    '- "fixture": a game still to come. team, opponent, day ("YYYY-MM-DD"), venue ("home" or "away", for team), competition.\n'
    '- "place": a team\'s league place now. team, position, points, and gap with other_team (points ahead of it, '
    "negative when behind).\n"
    '- "player_game": a player\'s numbers in the game the article reports. player, team, stat (one of '
    + ", ".join(GAME_STATS) + "), value. \"X made Y's goal\" or \"from X's pass\" is X with assists, value 1.\n"
    '- "goal_kind": how a goal in the game the article reports was scored. player, team, shot ("header", "volley", "drive" for '
    'a low hard strike, "shot" for any other strike with the foot). "Headed in" is a header.\n'
    '- "player_season": a player\'s numbers this season. player, team, competition ("league" or "all"), appearances, goals, '
    'assists. "His second league goal this season" is goals 2, competition "league".\n'
    '- "player_age": player, team, age.\n'
    '- "player_club": a player\'s whole career at a club, over all his seasons there. player, club, appearances, goals.\n'
    '- "meetings": the last games between two teams. team, opponent, last (how many meetings), won (how many of them team won), '
    "drawn, lost.\n"
    '- "title_run": league titles won in a row. team, won_in_a_row (the titles already won one after another up to now; '
    '"on course for a 42nd title in a row" is 41).\n'
    '- "other": any other hard fact. in_notebook: true when the notebook gives this fact, false when it does not.\n'
    "List each fact once. When the article has no hard fact, answer {\"claims\": []}."
)


@dataclass
class Finding:
    sentence: str
    claim: dict
    verdict: str  # "right", "wrong", "unknown" (kept) or "unsupported" (it goes)
    truth: str = ""


class Checker:
    """The claims of one article, checked in the archive as it stood on ``until``; ``game`` is the fixture it reports."""

    def __init__(self, connection, until: date, game: dict | None = None) -> None:
        from app.services.archive_questions import Archive

        self.archive = Archive(connection, until)
        self.c, self.w, self.until, self.game = connection, self.archive.w, until, game

    def check(self, claim: dict) -> Finding:
        kind = str(claim.get("kind") or "")
        sentence = str(claim.get("sentence") or "").strip()
        try:
            verdict, truth = getattr(self, f"_{kind}")(claim) if kind in KINDS else ("unknown", "")
        except (TypeError, ValueError, KeyError):
            verdict, truth = "unknown", ""
        return Finding(sentence, claim, verdict, truth)

    # -- the kinds ---------------------------------------------------------------------------------------------------
    def _result(self, claim):
        team, other = self._team(claim.get("team")), self._team(claim.get("opponent"))
        if not team or not other:
            return "unknown", ""
        games = self.w.rows("select * from fixtures where ((home_uid = :t and away_uid = :o) or (home_uid = :o and away_uid = :t)) "
                            "and home_goals is not null and date <= :d order by date", t=team, o=other, d=str(self.until))
        if claim.get("this_season"):
            games = [f for f in games if str(f["date"]) >= f"{self.w.season}-07-01"]
        when = str(claim.get("day") or claim.get("month") or "")
        if when:
            games = [f for f in games if str(f["date"]).startswith(when)]
        competition = str(claim.get("competition") or "").lower()
        if competition:
            games = [f for f in games if self._competition_fits(f["competition_uid"], competition)]
        if not games and not (when or claim.get("this_season")):
            return "unknown", ""  # a meeting further back than the archive, or a name it reads otherwise
        fits = [f for f in games if self._game_fits(f, team, claim)]
        if fits:
            return "right", ""
        season = self.w.rows("select * from fixtures where ((home_uid = :t and away_uid = :o) or (home_uid = :o and away_uid = :t)) "
                             "and home_goals is not null and date >= :s and date <= :d order by date",
                             t=team, o=other, s=f"{self.w.season}-07-01", d=str(self.until))
        shown = games or season
        told = "; ".join(self._line(f) for f in shown) or "none"
        return "wrong", (f"the games between {self.w.name(team)} and {self.w.name(other)}"
                         f"{' in that period' if games else ' this season'}: {told}")

    def _record(self, claim):
        team = self._team(claim.get("team"))
        league = self._league(team) if team else None
        if not league:
            return "unknown", ""
        record = self._league_record(team, league)
        wrong = [f"{field} {claim[field]} (the archive: {record[field]})" for field in RECORD_FIELDS
                 if isinstance(claim.get(field), (int, float)) and int(claim[field]) != record[field]]
        if not wrong:
            return "right", ""
        return "wrong", (f"{self.w.name(team)}'s league record this season: played {record['played']}, won {record['won']}, drawn "
                         f"{record['drawn']}, lost {record['lost']}, {record['points']} points; {record['unbeaten_run']} unbeaten, "
                         f"{record['winning_run']} wins in a row. Wrong: " + ", ".join(wrong))

    def _form(self, claim):
        team = self._team(claim.get("team"))
        league = self._league(team) if team else None
        last = claim.get("last")
        if not league or not isinstance(last, (int, float)) or last < 1:
            return "unknown", ""
        games = self.w.rows("select * from fixtures where competition_uid = :l and (home_uid = :t or away_uid = :t) and home_goals is not null "
                            "and date <= :d order by date desc limit :n", l=league, t=team, d=str(self.until), n=int(last))
        if len(games) < int(last):
            return "unknown", ""
        outcome = {"won": 0, "drawn": 0, "lost": 0}
        for f in games:
            outcome[self._outcome(f, team)] += 1
        wrong = [f"{field} {claim[field]} (the archive: {outcome[field]})" for field in outcome
                 if isinstance(claim.get(field), (int, float)) and int(claim[field]) != outcome[field]]
        if not wrong:
            return "right", ""
        return "wrong", (f"{self.w.name(team)}'s last {int(last)} league games: " + "; ".join(self._line(f) for f in games)
                         + ". Wrong: " + ", ".join(wrong))

    def _fixture(self, claim):
        team, other = self._team(claim.get("team")), self._team(claim.get("opponent"))
        if not team:
            return "unknown", ""
        games = self.w.rows("select * from fixtures where (home_uid = :t or away_uid = :t) and date > :d order by date limit 6",
                            t=team, d=str(self.until))
        if not games:
            return "unknown", ""
        day, venue = str(claim.get("day") or ""), str(claim.get("venue") or "").lower()
        for f in games:
            opponent = f["away_uid"] if f["home_uid"] == team else f["home_uid"]
            if (not other or opponent == other) and (not day or str(f["date"]).startswith(day)) and \
                    (venue not in ("home", "away") or (f["home_uid"] == team) == (venue == "home")):
                return "right", ""
        return "wrong", f"{self.w.name(team)}'s next games: " + "; ".join(
            f"{str(f['date'])[:10]}: {self.w.name(f['home_uid'])} v {self.w.name(f['away_uid'])} ({self.w.comp(f['competition_uid']).get('name') or 'friendly'})"
            for f in games)

    def _place(self, claim):
        team = self._team(claim.get("team"))
        league = self._league(team) if team else None
        if not league:
            return "unknown", ""
        table = self._table(league)
        rows = {r["team_uid"]: r for r in table}
        if team not in rows:
            return "unknown", ""
        mine, wrong = rows[team], []
        if isinstance(claim.get("position"), (int, float)) and int(claim["position"]) != mine["position"]:
            wrong.append(f"place {claim['position']} (the archive: {mine['position']})")
        if isinstance(claim.get("points"), (int, float)) and int(claim["points"]) != mine["points"]:
            wrong.append(f"{claim['points']} points (the archive: {mine['points']})")
        other = self._team(claim.get("other_team")) if claim.get("other_team") else None
        if other in rows and isinstance(claim.get("gap"), (int, float)):
            gap = mine["points"] - rows[other]["points"]
            if abs(int(claim["gap"])) != abs(gap):
                wrong.append(f"a gap of {claim['gap']} with {self.w.name(other)} (the archive: {gap:+d})")
        if not wrong:
            return "right", ""
        return "wrong", f"{self.w.name(team)} are {mine['position']} with {mine['points']} points in {len(table)}. Wrong: " + ", ".join(wrong)

    def _player_game(self, claim):
        stat = str(claim.get("stat") or "")
        if not self.game or stat not in GAME_STATS or not isinstance(claim.get("value"), (int, float)):
            return "unknown", ""
        person = self.archive.person(str(claim.get("player") or ""), claim.get("team"))
        if not person:
            return "unknown", ""
        rows = q._rows(self.c, f"select {stat} value from player_games where person_uid = :p and fixture_id = :f", p=person, f=self.game["id"])
        if not rows or rows[0]["value"] is None:
            return "unknown", ""
        real, told = rows[0]["value"], claim["value"]
        if stat == "assists" and told == 1 and real >= 1 or real == told:
            return "right", ""
        name = q._rows(self.c, "select name from people where fm_uid = :p", p=person)[0]["name"]
        return "wrong", f"{name}'s {stat.replace('_', ' ')} in this game: {real}" + (
            " (he made no goal)" if stat == "assists" and not real else "") + self._assisters(stat)

    def _goal_kind(self, claim):
        told = str(claim.get("shot") or "").lower()
        if not self.game or told not in ("header", "volley", "drive", "shot"):
            return "unknown", ""
        person = self.archive.person(str(claim.get("player") or ""), claim.get("team"))
        if not person:
            return "unknown", ""
        kinds = [r["kind"] for r in q._rows(self.c, "select kind from match_shots where fixture_id = :f and person_uid = :p and outcome = 'goal'",
                                             f=self.game["id"], p=person)]
        if not kinds or not all(kinds):
            return "unknown", ""  # a goal the game kept no event for
        # a header and a volley are told apart from any other strike; "drive" and "shot" both fit a strike with the foot
        fits = {"header": ("header",), "volley": ("volley",), "drive": ("drive", "finish"), "shot": ("drive", "finish", "volley")}[told]
        if any(kind in fits for kind in kinds):
            return "right", ""
        name = q._rows(self.c, "select name from people where fm_uid = :p", p=person)[0]["name"]
        return "wrong", f"{name}'s goal{'s' if len(kinds) > 1 else ''} in this game: " + ", ".join(
            {"finish": "a shot with the foot", "drive": "a drive", "header": "a header", "volley": "a volley"}[k] for k in kinds)

    def _player_season(self, claim):
        person = self.archive.person(str(claim.get("player") or ""), claim.get("team"))
        if not person:
            return "unknown", ""
        rows = q._rows(self.c, "select kind, sum(starts + substitute_appearances) appearances, sum(goals) goals, sum(assists) assists "
                               "from player_seasons where person_uid = :p and season_start = :s and kind > 0 group by kind", p=person, s=self.w.season)
        if not rows:
            return "unknown", ""
        league = dict(next((r for r in rows if r["kind"] == 1), {"appearances": 0, "goals": 0, "assists": 0}))
        every = {k: sum(r[k] or 0 for r in rows) for k in ("appearances", "goals", "assists")}
        # the totals are the last save's: the games after the article's day come off
        leagues = ",".join(str(u) for u, c in self.w.competitions.items() if c.get("scope") == "league") or "0"
        for r in q._rows(self.c, f"select competition_uid in ({leagues}) in_league, count(*) appearances, sum(goals) goals, sum(assists) assists "
                                 "from player_games where person_uid = :p and played_on > :d and played_on >= :s and minutes > 0 group by 1",
                         p=person, d=str(self.until), s=f"{self.w.season}-07-01"):
            for k in ("appearances", "goals", "assists"):
                every[k] -= r[k] or 0
                if r["in_league"]:
                    league[k] -= r[k] or 0
        scope = str(claim.get("competition") or "").lower()
        allowed = lambda k: {league[k]} if scope == "league" else {every[k]} if scope == "all" else {league[k], every[k]}  # noqa: E731
        wrong = [f"{claim[k]} {k}" for k in ("appearances", "goals", "assists")
                 if isinstance(claim.get(k), (int, float)) and int(claim[k]) not in allowed(k)]
        if not wrong:
            return "right", ""
        name = q._rows(self.c, "select name from people where fm_uid = :p", p=person)[0]["name"]
        return "wrong", (f"{name} this season up to this article's day: in the league {league['appearances']} games, "
                         f"{league['goals']} goals, {league['assists']} assists; in all {every['appearances']} games, {every['goals']} goals, "
                         f"{every['assists']} assists. Wrong: " + ", ".join(wrong))

    def _player_age(self, claim):
        person = self.archive.person(str(claim.get("player") or ""), claim.get("team"))
        if not person or not isinstance(claim.get("age"), (int, float)):
            return "unknown", ""
        row = q._rows(self.c, "select name, birth_date from people where fm_uid = :p", p=person)[0]
        age = q.age_on(row["birth_date"], self.until) if row["birth_date"] else None
        if age is None or age == int(claim["age"]):
            return ("unknown" if age is None else "right"), ""
        return "wrong", f"{row['name']} is {age}"

    def _player_club(self, claim):
        person = self.archive.person(str(claim.get("player") or ""))
        club = self._team(claim.get("club"))
        if not person or not club:
            return "unknown", ""
        rows = q._rows(self.c, "select kind, sum(starts + substitute_appearances) games, sum(goals) goals from player_seasons "
                               "where person_uid = :p and team_uid = :t and kind > 0 and season_start <= :s group by kind",
                       p=person, t=club, s=self.w.season)
        if not rows:
            return "unknown", ""
        league = next((r for r in rows if r["kind"] == 1), {"games": 0, "goals": 0})
        every = {"games": sum(r["games"] or 0 for r in rows), "goals": sum(r["goals"] or 0 for r in rows)}
        wrong = []
        for field, key in (("appearances", "games"), ("goals", "goals")):
            told = claim.get(field)
            if isinstance(told, (int, float)) and int(told) not in (league[key], every[key]):
                wrong.append(f"{told} {field}")
        if not wrong:
            return "right", ""
        name = q._rows(self.c, "select name from people where fm_uid = :p", p=person)[0]["name"]
        return "wrong", (f"{name} at {self.w.name(club)}: {league['games']} league games and {league['goals']} league goals; "
                         f"{every['games']} games and {every['goals']} goals in all. Wrong: " + ", ".join(wrong))

    def _meetings(self, claim):
        team, other = self._team(claim.get("team")), self._team(claim.get("opponent"))
        last = claim.get("last")
        if not team or not other or not isinstance(last, (int, float)) or last < 1:
            return "unknown", ""
        games = self.w.rows("select * from fixtures where ((home_uid = :t and away_uid = :o) or (home_uid = :o and away_uid = :t)) "
                            "and home_goals is not null and date <= :d order by date desc limit :n", t=team, o=other, d=str(self.until), n=int(last))
        if len(games) < int(last):
            return "unknown", ""
        outcome = {"won": 0, "drawn": 0, "lost": 0}
        for f in games:
            outcome[self._outcome(f, team)] += 1
        wrong = [f"{field} {claim[field]} (the archive: {outcome[field]})" for field in outcome
                 if isinstance(claim.get(field), (int, float)) and int(claim[field]) != outcome[field]]
        if not wrong:
            return "right", ""
        return "wrong", (f"the last {int(last)} games between {self.w.name(team)} and {self.w.name(other)}: "
                         + "; ".join(self._line(f) for f in games) + ". Wrong: " + ", ".join(wrong))

    def _title_run(self, claim):
        team = self._team(claim.get("team"))
        told = claim.get("won_in_a_row")
        league = self._league(team) if team else None
        if not league or not isinstance(told, (int, float)):
            return "unknown", ""
        winners = q._rows(self.c, "select year, team_uid from edition_places where competition_uid = :c and place = 1 and year <= :y "
                                  "order by year desc", c=league, y=self.until.year)
        run = 0
        for row in winners:
            if row["team_uid"] != team:
                break
            run += 1
        if not winners or run == int(told):
            return ("right" if winners else "unknown"), ""
        return "wrong", f"{self.w.name(team)} have won {run} league title{'s' if run != 1 else ''} in a row (the archive's champions, latest first)"

    def _other(self, claim):
        return ("unsupported", "") if claim.get("in_notebook") is False else ("unknown", "")

    # -- helpers -----------------------------------------------------------------------------------------------------
    def _team(self, name) -> int | None:
        return self.archive.team(str(name)) if name and str(name).strip() else None

    def _league(self, team: int) -> int | None:
        leagues = ",".join(str(u) for u, c in self.w.competitions.items() if c.get("scope") == "league") or "0"
        rows = self.w.rows(f"select competition_uid, count(*) n from fixtures where (home_uid = :t or away_uid = :t) and home_goals is not null "
                           f"and competition_uid in ({leagues}) and date >= :s and date <= :d group by 1 order by n desc limit 1",
                           t=team, s=f"{self.w.season}-07-01", d=str(self.until))
        return rows[0]["competition_uid"] if rows else None

    def _table(self, league: int) -> list[dict]:
        from app.services.story_leads_now import _window

        start, _ = _window(self.w, league)
        return q.league_table(self.c, league, start, str(self.until))

    def _league_record(self, team: int, league: int) -> dict:
        from app.services.story_leads_now import _window

        start, _ = _window(self.w, league)
        games = self.w.rows("select * from fixtures where competition_uid = :l and (home_uid = :t or away_uid = :t) and home_goals is not null "
                            "and date >= :s and date <= :d order by date", l=league, t=team, s=start, d=str(self.until))
        results = [self._outcome(f, team) for f in games]
        unbeaten = winning = 0
        for r in reversed(results):
            if r == "lost":
                break
            unbeaten += 1
        for r in reversed(results):
            if r != "won":
                break
            winning += 1
        won, drawn, lost = (results.count(x) for x in ("won", "drawn", "lost"))
        return {"played": len(results), "won": won, "drawn": drawn, "lost": lost, "points": 3 * won + drawn,
                "unbeaten_run": unbeaten, "winning_run": winning}

    def _competition_fits(self, uid: int, words: str) -> bool:
        comp = self.w.comp(uid)
        name = str(comp.get("name") or "").lower()
        if "league" in words and comp.get("scope") == "league" and "champions" not in words and "europa" not in words:
            return True
        return words in name or name in words

    def _game_fits(self, f: dict, team: int, claim: dict) -> bool:
        outcome = str(claim.get("outcome") or "").lower()
        if outcome and {"draw": "drawn", "drew": "drawn", "win": "won", "loss": "lost", "defeat": "lost"}.get(outcome, outcome) != self._outcome(f, team):
            return False
        venue = str(claim.get("venue") or "").lower()
        if venue in ("home", "away") and (f["home_uid"] == team) != (venue == "home"):
            return False
        score = re.findall(r"\d+", str(claim.get("score") or ""))
        if len(score) == 2:
            mine, theirs = self._goals(f, team)
            if sorted(map(int, score)) != sorted((mine, theirs)) or (outcome and (int(score[0]), int(score[1])) != (mine, theirs)
                                                                      and (int(score[1]), int(score[0])) != (mine, theirs)):
                return False
        return True

    @staticmethod
    def _goals(f: dict, team: int) -> tuple[int, int]:
        extra = f["home_goals_after_extra_time"] is not None
        home, away = (f["home_goals_after_extra_time"], f["away_goals_after_extra_time"]) if extra else (f["home_goals"], f["away_goals"])
        return (home, away) if f["home_uid"] == team else (away, home)

    def _outcome(self, f: dict, team: int) -> str:
        mine, theirs = self._goals(f, team)
        if mine == theirs and f["home_penalties"] is not None and f["home_penalties"] != f["away_penalties"]:
            won = (f["home_penalties"] > f["away_penalties"]) == (f["home_uid"] == team)
            return "won" if won else "lost"
        return "won" if mine > theirs else "drawn" if mine == theirs else "lost"

    def _line(self, f: dict) -> str:
        from app.services.story_leads_now import _result_line

        return f"{_result_line(self.w, f)} ({self.w.comp(f['competition_uid']).get('name') or 'friendly'})"

    def _assisters(self, stat: str) -> str:
        if stat != "assists" or not self.game:
            return ""
        rows = q._rows(self.c, "select p.name, g.assists from player_games g join people p on p.fm_uid = g.person_uid "
                               "where g.fixture_id = :f and g.assists > 0", f=self.game["id"])
        return ("; the assists in this game: " + ", ".join(f"{r['name']} {r['assists']}" for r in rows)) if rows else ""


def claims(model: str, key: str, article_text: str, notebook: dict) -> list[dict]:
    """The article's hard facts in fixed fields, from the model."""
    from app.services.story_writer import request_json

    from app.services.story_writer import StoryError

    try:
        answer = request_json(model, key, [{"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps(
            {"notebook": notebook, "article": article_text}, ensure_ascii=False, default=str)}], temperature=0.0)
    except StoryError as exc:  # the checker could not answer: the article keeps the checks it had before
        current_app.logger.warning("fact check failed: %s", exc)
        return []
    found = answer.get("claims") if isinstance(answer, dict) else None
    return [c for c in found if isinstance(c, dict) and c.get("sentence")] if isinstance(found, list) else []


def problems(findings: list[Finding]) -> list[Finding]:
    """The wrong and the unsupported, each sentence and truth once (a model lists some claims twice)."""
    seen, found = set(), []
    for f in findings:
        if f.verdict in ("wrong", "unsupported") and (f.sentence, f.truth) not in seen:
            seen.add((f.sentence, f.truth))
            found.append(f)
    return found


def fix_note(findings: list[Finding]) -> str:
    """What the writer is told: each sentence, and the truth or that it goes."""
    lines = []
    for f in findings:
        if f.verdict == "wrong":
            lines.append(f'"{f.sentence}" is wrong. The archive: {f.truth}. Correct it with these facts, or cut the claim.')
        else:
            lines.append(f'"{f.sentence}": neither the notebook nor the archive gives this fact. Cut it; keep the rest of the paragraph.')
    return ("The paper's fact checker compared your article with the archive. Change only these sentences and leave the "
            "rest as it is:\n" + "\n".join(lines))


def cut(article: dict, findings: list[Finding]) -> dict:
    """The article without the sentences that are still wrong after the writer's fix: the standfirst and body lose them."""
    article = dict(article)
    for f in findings:
        for part in ("standfirst", "body"):
            if f.sentence and f.sentence in article.get(part, ""):
                article[part] = article[part].replace(f.sentence, "")
    for part in ("standfirst", "body"):
        paragraphs = [re.sub(r"[ \t]{2,}", " ", p).strip() for p in article.get(part, "").split("\n")]
        article[part] = re.sub(r"\n{3,}", "\n\n", "\n".join(paragraphs)).strip()
    return article
