"""The writers' questions to the archive (27 Oct 2026): a writer who wants a fact the notebook lacks asks for it, the way a
reporter rings the statistician, instead of guessing ("two points from safety" was a guess that happened to be right).

Ready-made questions answer the common facts with the archive's own rules (a league table counts only its main stage; a
club's story; a head-to-head; a player's season); free SQL, read-only, answers the rest. Every answer is bounded by the
edition's date, goes into the notebook, and so counts for the fact check: a number looked up passes, an invented one does not.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from app.services import world_queries as q

MAX_QUESTIONS = 6  # per article: the cost is one more round trip each
ANSWER_CHARS = 3500  # an answer is cut to this, so a wide question does not flood the conversation
SQL_ROWS = 30

SQL_GUIDE = (
    "The archive is SQLite. Main tables (ids are the game's uids; dates are ISO text):\n"
    "fixtures(id, date, competition_uid, stage, home_uid, away_uid, home_goals, away_goals, home_goals_after_extra_time, "
    "away_goals_after_extra_time, home_penalties, away_penalties, attendance); a game not yet played has null goals.\n"
    "teams(fm_uid, name, club_uid, nation_uid, age_limit, reputation 0-10000); a club's first team has age_limit null and "
    "usually fm_uid = club_uid; a nation's team has club_uid null.\n"
    "competitions(fm_uid, name, scope: league/cup/continental/national_teams/youth/friendly, nation_uid, level: 1 top division).\n"
    "people(fm_uid, name, birth_date, nation_uid, team_uid, reputation_world 0-10000, staff_job, staff_team_uid, staff_since).\n"
    "player_seasons(person_uid, team_uid, season_start (2063 is 2063/64), kind: 0 friendly 1 league 2 cup 3 continental "
    "4 international, league_uid, starts, substitute_appearances, goals, assists, rated_appearances, rating_total (tenths: "
    "rating_total / rated_appearances / 10 is the average rating), clean_sheets, yellow_cards, red_cards, minutes).\n"
    "goals(fixture_id, scorer_uid, side: 0 home 1 away, minute, added_time, kind: 0 goal 1 own goal 2 penalty, 3-5 do not count).\n"
    "match_players(fixture_id, side, person_uid, played (minutes, 0 unused), rating, xg, goals, assists, shots, "
    "shots_on_target, key_passes); match_reports(fixture_id, stadium, player_of_the_match_uid).\n"
    "transfers(person_uid, date, from_uid, to_uid, fee); injuries(person_uid, injury, injured_on, days, team_uid, current); "
    "manager_spells(team_uid, person_uid, started, ended, won, drawn, lost); contracts(person_uid, team_uid, wage, expires); "
    "edition_places(competition_uid, year, place, team_uid) with competition_editions(competition_uid, year, season_label): "
    "final places, place 1 is the winner.\n"
    "Traps: a league table from fixtures must use only the league's main stage (the stage with the most games), never "
    "play-offs; prefer the league_table question for tables. player_seasons mixes kinds: filter kind. Nothing after the "
    "edition's date has happened yet."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "league_table",
        "description": "A league's table on the edition's date (or an earlier date): place, club, played, won, drawn, lost, goal difference, points.",
        "parameters": {"type": "object", "properties": {
            "competition": {"type": "string", "description": "The league's name, as in the notebook."},
            "on_date": {"type": "string", "description": "Optional ISO date, not after the edition's date."}},
            "required": ["competition"]}}},
    {"type": "function", "function": {
        "name": "club_story",
        "description": "A club's league seasons in the archive's years (with the climbs and falls), its honours, legends, rivals, "
                       "longest servants, top scorers and manager.",
        "parameters": {"type": "object", "properties": {"club": {"type": "string"}}, "required": ["club"]}}},
    {"type": "function", "function": {
        "name": "head_to_head",
        "description": "Two clubs' meetings before the edition's date, newest first, with the record counted.",
        "parameters": {"type": "object", "properties": {"club": {"type": "string"}, "other_club": {"type": "string"}},
                       "required": ["club", "other_club"]}}},
    {"type": "function", "function": {
        "name": "recent_results",
        "description": "A club's last results before the edition's date, in all competitions, newest first.",
        "parameters": {"type": "object", "properties": {"club": {"type": "string"},
                                                       "count": {"type": "integer", "description": "1-12, default 6"}},
                       "required": ["club"]}}},
    {"type": "function", "function": {
        "name": "player",
        "description": "A player's club, age and position, this season by competition (games, goals, assists, average rating, "
                       "expected goals) and his career's clubs.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"},
                                                       "club": {"type": "string", "description": "Optional, to tell two players apart."}},
                       "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "regulars",
        "description": "A club's regular starters this season, by position, from its team sheets: the players who matter to the "
                       "team, with their starts. Ask this for a club's key men, never list them from the whole registered squad, "
                       "which holds youth players and reserves.",
        "parameters": {"type": "object", "properties": {"club": {"type": "string"}}, "required": ["club"]}}},
    {"type": "function", "function": {
        "name": "sql",
        "description": "A read-only SQL SELECT on the archive, for anything the other questions do not answer. At most 30 rows. "
                       + SQL_GUIDE,
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
]

PROMPT_NOTE = (
    " Before you write, you can ask the archive questions with the tools. Look at what your piece will say: a club's place "
    "in its table or the points between clubs, a run of results, past meetings, a club's past, a player's numbers. When the "
    "notebook does not give it, ask for it instead of writing it from memory or leaving it vague; a reader of a club story "
    "wants to know where the club stands. Use the competition and club names exactly as the notebook writes them. Ask only "
    f"what the piece needs, at most {MAX_QUESTIONS}; the answers are facts like the notebook's. When the archive has no "
    "answer, leave the fact out. A result you look up says nothing of who played in it: a player's part in a game is only "
    "what the notebook or the player question gives for him."
)


class Archive:
    """The questions for one article, answered from the world archive as it stood on the edition's date."""

    def __init__(self, connection, until: date) -> None:
        from app.services.story_leads import _World, followed_clubs

        self.c = connection
        self.until = until
        self.w = _World(connection, until - timedelta(days=14), until, followed_clubs())

    # -- names ------------------------------------------------------------------------------------------------------------
    def team(self, name: str) -> int | None:
        """A club's first team by name: the exact name first, then a name that contains it; the best known wins."""
        for sql in ("lower(name) = lower(:n)", "lower(name) like lower(:p)"):
            rows = q._rows(self.c, f"select fm_uid from teams where {sql} and age_limit is null order by coalesce(reputation, 0) desc limit 1",
                           n=name.strip(), p=f"%{name.strip()}%")
            if rows:
                return rows[0]["fm_uid"]
        return None

    def competition(self, name: str) -> int | None:
        """A competition by its exact name only: "Premier League" could be Israel's as easily as England's."""
        rows = q._rows(self.c, "select fm_uid from competitions where lower(name) = lower(:n) order by coalesce(reputation, 0) desc limit 1",
                       n=name.strip())
        return rows[0]["fm_uid"] if rows else None

    def competition_names(self, name: str) -> list[str]:
        """The archive's names that share a word with the one asked for, best known first, to ask again with."""
        words = [w for w in re.findall(r"\w{3,}", name.lower()) if w not in {"the", "league", "cup"}] or re.findall(r"\w{3,}", name.lower())
        found = {}
        for word in words:
            for r in q._rows(self.c, "select name, coalesce(reputation, 0) rep from competitions where lower(name) like :p "
                                     "and scope in ('league', 'cup', 'continental', 'national_teams') order by rep desc limit 8", p=f"%{word}%"):
                found[r["name"]] = max(found.get(r["name"], 0), r["rep"])
        return sorted(found, key=lambda n: -found[n])[:8]

    def person(self, name: str, club: str | None = None) -> int | None:
        team = self.team(club) if club else None
        for sql in ("lower(name) = lower(:n)", "lower(name) like lower(:p)"):
            rows = q._rows(self.c, f"select fm_uid from people where {sql} order by (team_uid = :t) desc, coalesce(reputation_world, 0) desc "
                                   f"limit 1", n=name.strip(), p=f"%{name.strip()}%", t=team or -1)
            if rows:
                return rows[0]["fm_uid"]
        return None

    # -- the questions ----------------------------------------------------------------------------------------------------
    def answer(self, name: str, arguments: dict) -> str:
        try:
            result = getattr(self, f"_{name}")(**arguments) if name in {t["function"]["name"] for t in TOOLS} else {"error": f"no question {name}"}
        except TypeError as exc:
            result = {"error": f"the question's arguments were wrong: {exc}"}
        except Exception as exc:  # noqa: BLE001 - the writer is told and writes without it
            result = {"error": str(exc)[:300]}
        text = json.dumps(result, ensure_ascii=False, default=str)
        return text if len(text) <= ANSWER_CHARS else text[:ANSWER_CHARS] + " ... (cut)"

    def _league_table(self, competition: str, on_date: str | None = None) -> dict:
        from app.services.story_leads_now import _window

        uid = self.competition(competition)
        if uid is None:
            return {"error": f"no competition is called exactly '{competition}'; ask again with one of these names",
                    "names": self.competition_names(competition)}
        day = min(date.fromisoformat(on_date[:10]), self.until) if on_date else self.until
        w = self.w if day == self.until else type(self.w)(self.c, day - timedelta(days=14), day, tuple(self.w.followed_clubs))
        start, _ = _window(w, uid)
        table = q.league_table(self.c, uid, start, str(day))
        return {"competition": w.comp(uid).get("name"), "on": str(day), "teams": len(table),
                "table": [f"{r['position']}. {self.w.name(r['team_uid'])}: P{r['played']} W{r['won']} D{r['drawn']} L{r['lost']} "
                          f"GD{r['difference']:+d} {r['points']} pts" for r in table]}

    def _club_story(self, club: str) -> dict:
        from app.services import features

        uid = self.team(club)
        if uid is None:
            return {"error": f"no club called {club}"}
        return features.club_story(self.w, uid, self.w.team(uid).get("club_uid"))

    def _head_to_head(self, club: str, other_club: str) -> dict:
        from app.services import features

        a, b = self.team(club), self.team(other_club)
        if not a or not b:
            return {"error": "one of the clubs is not known"}
        f = {"date": str(self.until + timedelta(days=1)), "home_uid": a, "away_uid": b}
        rows = self.w.rows("select date, home_uid, away_uid, home_goals, away_goals from fixtures where home_goals is not null and date <= :d "
                           "and ((home_uid = :h and away_uid = :a) or (home_uid = :a and away_uid = :h)) order by date desc limit 10",
                           d=str(self.until), h=a, a=b)
        if not rows:
            return {"meetings": "none in the archive"}
        lines = features._meetings(self.w, f)  # the counted record and the last six
        if len(rows) > 6:
            lines += [f"{str(r['date'])[:10]}: {self.w.name(r['home_uid'])} {r['home_goals']}-{r['away_goals']} {self.w.name(r['away_uid'])}"
                      for r in rows[6:]]
        return {"meetings": lines}

    def _recent_results(self, club: str, count: int = 6) -> dict:
        from app.services.story_leads_now import _result_line

        uid = self.team(club)
        if uid is None:
            return {"error": f"no club called {club}"}
        rows = self.w.rows("select * from fixtures where (home_uid = :t or away_uid = :t) and home_goals is not null and date <= :d "
                           "order by date desc limit :n", t=uid, d=str(self.until), n=max(1, min(int(count or 6), 12)))
        return {"club": self.w.name(uid), "results": [f"{_result_line(self.w, f)} ({self.w.comp(f['competition_uid']).get('name') or 'friendly'})"
                                                      for f in rows]}

    def _player(self, name: str, club: str | None = None) -> dict:
        from app.services.story_leads_now import position_words

        uid = self.person(name, club)
        if uid is None:
            return {"error": f"no player called {name}"}
        p = q._rows(self.c, "select name, birth_date, team_uid, reputation_world from people where fm_uid = :u", u=uid)[0]
        season = self.w.season
        rows = q._rows(self.c, "select team_uid, kind, league_uid, starts, substitute_appearances, goals, assists, rating_total, "
                               "rated_appearances from player_seasons where person_uid = :u and season_start = :s and kind > 0", u=uid, s=season)
        xg = {(r["team"], r["competition_uid"]): r for r in q._rows(  # by club and competition: a loan player has two clubs in one league
            self.c, "select case mp.side when 0 then f.home_uid else f.away_uid end team, f.competition_uid, round(sum(mp.xg), 2) xg, "
                    "sum(mp.shots) shots from match_players mp join fixtures f on f.id = mp.fixture_id "
                    "where mp.person_uid = :u and mp.played > 0 and f.date >= :s and f.date <= :d group by 1, 2", u=uid, s=f"{season}-07-01", d=str(self.until))}
        kinds = {1: "league", 2: "cup", 3: "continental", 4: "international"}
        this = []
        for r in rows:
            line = {"team": self.w.name(r["team_uid"]), "kind": kinds.get(r["kind"], "other"),
                    "competition": self.w.comp(r["league_uid"]).get("name") if r["kind"] == 1 else None,
                    "games": r["starts"] + r["substitute_appearances"], "starts": r["starts"], "goals": r["goals"], "assists": r["assists"],
                    "average_rating": round(r["rating_total"] / r["rated_appearances"] / 10, 2) if r["rated_appearances"] else None}
            found = xg.get((r["team_uid"], r["league_uid"])) if r["kind"] == 1 else None
            if found:
                line["expected_goals"], line["shots"] = found["xg"], found["shots"]
            this.append({k: v for k, v in line.items() if v is not None})
        career = q._rows(self.c, "select team_uid, min(season_start) first, max(season_start) last, sum(starts + substitute_appearances) games, "
                                 "sum(goals) goals from player_seasons where person_uid = :u and kind = 1 group by team_uid order by first", u=uid)
        return {"player": p["name"], "age": q.age_on(p["birth_date"], self.until) if p["birth_date"] else None,
                "club": self.w.name(p["team_uid"]), "position": position_words(q.main_positions(self.c, [uid]).get(uid)),
                "this_season": this or "no games this season", "in_his_team": self._standing(uid, p["team_uid"]),
                "league_career": [f"{self.w.name(r['team_uid'])}, {q.season_label(r['first'])}"
                                  f"{' to ' + q.season_label(r['last']) if r['last'] != r['first'] else ''}: {r['games']} games, {r['goals']} goals"
                                  for r in career]}

    def _standing(self, uid: int, team: int | None) -> str | None:
        """Whether a player is one of his club's regulars this season, from the team sheets."""
        regulars = q.regulars(self.c, team, self.until) if team else None
        if regulars is None:
            return None
        return q.regular_words(regulars[uid]) if uid in regulars else "not a regular starter in the first team this season"

    def _regulars(self, club: str) -> dict:
        uid = self.team(club)
        if uid is None:
            return {"error": f"no club called {club}"}
        regulars = q.regulars(self.c, uid, self.until)
        if regulars is None:
            return {"regulars": "the archive has too few of their team sheets this season to tell"}
        by_position: dict[str, list[str]] = {}
        for p in sorted(regulars.values(), key=lambda p: -p["starts"]):
            by_position.setdefault(p["position"], []).append(f"{p['name']} ({p['starts']} starts)")
        games = next(iter(regulars.values()))["games"] if regulars else 0
        return {"team_sheets_this_season": games, "regulars_by_position": by_position}

    def _sql(self, query: str) -> dict:
        query = query.strip().rstrip(";")
        if not re.match(r"(?is)^\s*(select|with)\b", query) or ";" in query:
            return {"error": "only one SELECT is allowed"}
        connection = self.c.connection.driver_connection  # the sqlite3 connection under this one
        _as_of_views(connection, self.until)
        connection.execute("pragma query_only = on")  # read-only while the writer's query runs: it can look, never touch
        connection.set_progress_handler(_too_long(), 200_000)
        try:
            cursor = connection.execute(query)
            names = [d[0] for d in cursor.description or []]
            rows = cursor.fetchmany(SQL_ROWS + 1)
        finally:
            connection.set_progress_handler(None, 0)
            connection.execute("pragma query_only = off")
            _drop_as_of_views(connection)
        return {"columns": names, "rows": [list(r) for r in rows[:SQL_ROWS]], "more_rows": len(rows) > SQL_ROWS}


# The games after the article's day, hidden from the writer's SQL: temp views of the same names come before the tables
# (a report of 6 Feb 2064 told the Crewe 6-0 of 9 Feb). A later game keeps its date and teams, without a result.
_FIXTURE_RESULTS = ("home_goals", "away_goals", "home_goals_after_extra_time", "away_goals_after_extra_time", "home_penalties",
                    "away_penalties", "attendance", "away_fans")
_BY_FIXTURE = ("goals", "match_reports", "match_teams", "match_players", "match_shots")
_BY_DAY = ("player_games", "team_tactics")


def _as_of_views(connection, until: date) -> None:
    _drop_as_of_views(connection)
    day = str(until)
    tables = {r[0] for r in connection.execute("select name from main.sqlite_master where type = 'table'")}
    if "fixtures" not in tables:
        return
    columns = [r[1] for r in connection.execute("pragma main.table_info(fixtures)")]
    picked = ", ".join(f"case when date <= '{day}' then {c} end {c}" if c in _FIXTURE_RESULTS else c for c in columns)
    connection.execute(f"create temp view fixtures as select {picked} from main.fixtures")
    for table in _BY_FIXTURE:
        if table in tables:
            connection.execute(f"create temp view {table} as select * from main.{table} "
                               f"where fixture_id in (select id from main.fixtures where date <= '{day}')")
    for table in _BY_DAY:
        if table in tables:
            connection.execute(f"create temp view {table} as select * from main.{table} where played_on <= '{day}'")


def _drop_as_of_views(connection) -> None:
    for table in ("fixtures", *_BY_FIXTURE, *_BY_DAY):
        connection.execute(f"drop view if exists temp.{table}")


def _too_long():
    """Stop a query after about two seconds of work (sqlite calls this every 200 000 steps; non-zero stops it)."""
    import time

    end = time.monotonic() + 2.0
    return lambda: int(time.monotonic() > end)
