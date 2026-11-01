"""The facts a season story, a competition story or a match report is written from
(docs/stories-plan.md). Like ``player_notebook``, each reads the world database through
``world_queries`` and returns one plain dict with names, season labels and scores written
from the subject's side, so the writer never has to look anything up.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy.engine import Connection

from app.extensions import db
from app.models.match_text import PREVIEW, REPORT, MatchText
from app.models.world_story import GENERATED as STORY_GENERATED, ClubSeasonStory, ClubText, EditionChapter, EditionStory, StoryVersion
from app.services import tactics
from app.services import world_queries as q
from app.services.player_notebook import EUROS_PER_POUND, POSITION_WORDS

COLOUR_LIMIT = 1500  # characters of an older text given as colour
BACKGROUND_LIMIT = 4500  # characters of the club's own background text (owners, money, history)


def curator_club_text(club_uid: int) -> tuple[str, date | None] | None:
    """The curator's own "About the club" text and the day it was written: the current one, or, when an AI model
    wrote the current one, the newest of the curator's earlier ones."""
    text = db.session.get(ClubText, club_uid)
    if text is not None and text.source != STORY_GENERATED:
        body = "\n\n".join(part.strip() for part in (text.profile_summary, text.historical_context) if part and part.strip())
        if body:
            return body, text.written_at.date() if text.written_at else None
    older = (db.session.query(StoryVersion).filter(StoryVersion.kind == "club", StoryVersion.subject_uid == club_uid,
                                                   StoryVersion.source != STORY_GENERATED)
             .order_by(StoryVersion.replaced_at.desc()).first())
    return (older.text, older.written_at.date() if older.written_at else None) if older else None


def club_background(club_uid: int, limit: int = BACKGROUND_LIMIT) -> dict | None:
    """The curator's texts about a club ("About the club"): its owners, money and history, which the
    saves do not hold. Dated, so a writer knows how old the figures are."""
    text = db.session.get(ClubText, club_uid)
    if text is None:
        return None
    body = "\n\n".join(part.strip() for part in (text.profile_summary, text.historical_context) if part and part.strip())
    if not body:
        return None
    if len(body) > limit:
        body = body[:limit].rsplit(" ", 1)[0] + " …"
    return {"text": body, "written": text.written_at.date().isoformat() if text.written_at else None,
            "use": "background the save does not hold (owners, finances, history); weave it in where it shapes the story; "
                   "the save's facts win where the two disagree, and figures are as of the date written"}
SQUAD_SIZE = 18


class _Lookup:
    """Team, competition and person names for one notebook, each looked up once."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.teams: dict[int, dict] = {}
        self.competitions: dict[int, dict] = {}

    def team(self, uid: int | None) -> str | None:
        if not uid:
            return None
        if uid not in self.teams:
            self.teams.update(q.teams(self.connection, [uid]))
        return self.teams.get(uid, {}).get("name")

    def competition(self, uid: int | None) -> str | None:
        if not uid:
            return None
        if uid not in self.competitions:
            self.competitions.update(q.competitions(self.connection, [uid]))
        return self.competitions.get(uid, {}).get("name")


def _goals(connection: Connection, fixture_ids: list[int]) -> dict[int, list[dict]]:
    found: dict[int, list[dict]] = defaultdict(list)
    if not fixture_ids:
        return found
    marks = ",".join(str(int(i)) for i in fixture_ids)
    for g in q._rows(connection, f"select g.*, p.name from goals g left join people p on p.fm_uid = g.scorer_uid "
                                 f"where g.fixture_id in ({marks}) and g.kind in (0, 1, 2) order by g.fixture_id, g.seq"):
        found[g["fixture_id"]].append(g)
    return found


def _minute(g: dict) -> str:
    return f"{g['minute']}+{g['added_time']}'" if g["added_time"] else f"{g['minute']}'"


def _scorer(g: dict) -> str:
    note = " (pen)" if g["kind"] == 2 else " (own goal)" if g["kind"] == 1 else ""
    return f"{g['name'] or 'unknown'} {_minute(g)}{note}"


def _result_from(f: dict, side: int, names: _Lookup, goals: list[dict] | None = None) -> dict:
    """One match from one team's side: opponent, venue, score for-against, result, scorers."""
    scored, conceded, result = q.result_for(f, side)
    home = f["home_uid"] == side
    row = {"date": str(f["date"]), "competition": names.competition(f["competition_uid"]),
           "opponent": names.team(f["away_uid"] if home else f["home_uid"]), "venue": "home" if home else "away",
           "score": f"{scored}-{conceded}", "result": {"W": "won", "D": "drawn", "L": "lost"}[result]}
    if f["home_goals_after_extra_time"] is not None:
        row["after_extra_time"] = True
    if f["home_penalties"] is not None:
        mine, theirs = (f["home_penalties"], f["away_penalties"]) if home else (f["away_penalties"], f["home_penalties"])
        row["penalties"] = f"{mine}-{theirs}"
    if goals is not None:
        own_side = 0 if home else 1
        row["scorers"] = [_scorer(g) for g in goals if g["side"] == own_side]
        row["opponent_scorers"] = [_scorer(g) for g in goals if g["side"] != own_side]
    return row


def _colour(text: str | None) -> str | None:
    if not text or not text.strip():
        return None
    text = text.strip()
    return text if len(text) <= COLOUR_LIMIT else text[:COLOUR_LIMIT].rsplit(" ", 1)[0] + " …"


def _season_label(connection: Connection, league_uid: int | None, season: int) -> tuple[str, str, str]:
    start, end = q.league_window(connection, league_uid, season) if league_uid else q.season_window(season)
    return (str(season) if start.endswith("-01-01") else q.season_label(season)), start, end


# -- a club's season ---------------------------------------------------------------------------------
RELATION_NAMES = 12  # the most a notebook lists of a club's icons or favourites


def club_relationships(connection: Connection, club_uid: int) -> dict | None:
    """The game's lists of the club's legends, icons, favourites, disliked people and rivals."""
    groups = q.club_relations(connection, club_uid)

    def people(key: str, least: int = 0) -> list[dict]:
        return [{"name": r["name"], "as": r["reason_name"] or None, "level_of_100": r["level"]}
                for r in groups[key] if r["level"] >= least][:RELATION_NAMES]

    found = {"legends": people("Legend"), "icons": people("Icon"), "favourites": people("Favoured", least=50),
             "disliked": people("Disliked"),
             "rival_clubs": [{"club": r["name"], "rivalry": r["reason_name"] or None, "level_of_100": r["level"]} for r in groups["Rival"]]}
    found = {k: v for k, v in found.items() if v}
    return found or None


CLUB_LEADERS = 10  # the club's top scorers and most-capped players in its notebook
CLUB_TEXT_LIMIT = 6000  # characters of the curator's own text given to the club's writer


def club_notebook(connection: Connection, club_uid: int) -> dict | None:
    """Everything the save says about a club over the archive's years, for its "About the club": its
    seasons, honours, leading players, legends and rivals, its ground and manager, and the curator's text."""
    team = q.teams(connection, [club_uid]).get(club_uid)
    if team is None:
        return None
    names = _Lookup(connection)
    today = q.game_date(connection)
    overview = q.club_overview(connection, club_uid)
    club_uid_ = team.get("club_uid") or club_uid
    nation = q._one(connection, "select n.name from clubs c join nations n on n.fm_uid = c.nation_uid where c.fm_uid = :c", c=club_uid_)
    ground = q._one(connection, "select r.stadium, count(*) n from match_reports r join fixtures f on f.id = r.fixture_id "
                                "where f.home_uid = :t and r.stadium is not null group by 1 order by n desc limit 1", t=club_uid)
    manager = q._one(connection, "select name, staff_since from people where staff_team_uid = :t and staff_job = 'Manager' "
                                 "order by staff_since limit 1", t=club_uid)
    old, written = curator_club_text(club_uid) or (None, None)
    if old and len(old) > CLUB_TEXT_LIMIT:
        old = old[:CLUB_TEXT_LIMIT].rsplit(" ", 1)[0] + " …"

    def leaders(rows: list[dict]) -> list[dict]:
        return [{"name": r["name"], "seasons": r["seasons"], "league_games": r["apps"], "league_goals": r["goals"]}
                for r in rows[:CLUB_LEADERS]]

    return {
        "game_date": str(today) if today else None,
        "club": team["name"], "country": nation["name"] if nation else None,
        "home_ground": ground["stadium"] if ground else None,
        "manager": {"name": manager["name"], "since": str(manager["staff_since"]) if manager["staff_since"] else None} if manager else None,
        "status": "the club's story up to the game date: it goes on, so end on where the club stands now, not on a farewell",
        "honours": [{"competition": names.competition(h["competition_uid"]), "times": h["count"], **_runs(h["seasons"]),
                     "seasons": h["seasons"]} for h in overview["honours"]],
        "seasons": [{"season": q.season_label(x["season"]), "league": names.competition(x["league_uid"]),
                     "position": x["position"], "of": x["teams_in_league"], "points": x["points"],
                     "won": [names.competition(c) for c in x["won"]] or None,
                     "top_scorer": {"name": x["top"]["name"], "league_goals": x["top"]["goals"]} if x["top"] and x["top"]["goals"] else None}
                    for x in overview["seasons"] if x["position"]],  # a season with no league games yet is left out
        "top_league_scorers": leaders(overview["top_scorers"]),
        "most_league_games": leaders(overview["most_apps"]),
        "players_used": overview["players"],
        "club_relationships": club_relationships(connection, club_uid_),
        "finances": club_finances(connection, club_uid_),
        "managers": [{"name": m["name"], "from": str(m["started"]) if m["started"] else None, "to": str(m["ended"]) if m["ended"] else "now",
                      **({} if m.get("current") else {"won": m["won"], "drawn": m["drawn"], "lost": m["lost"], "left": m["reason_name"] or None,
                                                       "league_titles": m["league_wins"] or None, "cups": m["cup_wins"] or None})}
                     for m in q.club_managers(connection, club_uid) if m.get("current") or (not m["caretaker"] and m["games"] >= 10)] or None,
        **_great_nights(connection, club_uid, names),
        "club_background": {"text": old, "written": str(written) if written else None,
                            "use": "the curator's own text: facts the save does not hold (founding, ground, owners, money, the "
                                   "years before the save); use them, but where they disagree with the fields above, those "
                                   "win; never copy its sentences. Its claims about the present ('leading the league', "
                                   "'unbeaten so far') are only true as of when it was written: the fields above tell the "
                                   "present"} if old else None,
    }


FINALS_IN_NOTEBOOK = 18  # the most dramatic finals given to the club's writer
MARGINS_IN_NOTEBOOK = 5  # biggest wins and heaviest defeats
RIVALS_IN_NOTEBOOK = 3
COMPETITIVE = "('league', 'cup', 'continental')"


def _great_nights(connection: Connection, club_uid: int, names: _Lookup) -> dict:
    """The matches a club's story is told through: its most dramatic finals (all those lost, and the won ones
    decided late, on penalties or by one goal), its biggest wins and heaviest defeats, and its record against
    its fiercest rivals, each with the scorers, the minutes and the crowd."""
    places = q._rows(connection, "select p.competition_uid, p.place, e.season_label, e.decided_on from edition_places p "
                                 "join competition_editions e on e.competition_uid = p.competition_uid and e.year = p.year "
                                 "where p.team_uid = :t and p.place <= 2 and e.decided_on is not null", t=club_uid)
    leagues = q.league_uids(connection)
    finals = []
    for x in places:
        if x["competition_uid"] in leagues:
            continue
        final = q.final_of(connection, x["competition_uid"], x["decided_on"], club_uid)
        if final and final["home_goals"] is not None:
            finals.append((final, x))
    goals = _goals(connection, [f["id"] for f, _ in finals])

    def drama(final: dict, place: dict) -> int:
        scored, conceded, _ = q.result_for(final, club_uid)
        late = any(g["minute"] >= 85 for g in goals[final["id"]])
        return (3 * (place["place"] == 2) + 3 * (final["home_penalties"] is not None) + 2 * (final["home_goals_after_extra_time"] is not None)
                + 2 * late + (abs(scored - conceded) <= 1))

    chosen = sorted(finals, key=lambda pair: -drama(*pair))[:FINALS_IN_NOTEBOOK]
    told = [{k: v for k, v in _result_from(f, club_uid, names, goals[f["id"]]).items() if k != "venue"}  # a final is on neutral ground
            | {"final_of": f"{x['season_label']} {names.competition(x['competition_uid'])}", "crowd": f["attendance"] or None}
            for f, x in sorted(chosen, key=lambda pair: str(pair[0]["date"]))]

    def margins(order: str) -> list[dict]:
        rows = q._rows(connection, "select f.* from fixtures f join competitions c on c.fm_uid = f.competition_uid "
                                   f"where :t in (f.home_uid, f.away_uid) and f.home_goals is not null and c.scope in {COMPETITIVE} "
                                   "order by (case when f.home_uid = :t then f.home_goals - f.away_goals else f.away_goals - f.home_goals end) "
                                   f"{order}, f.date desc limit {MARGINS_IN_NOTEBOOK}", t=club_uid)
        found = _goals(connection, [f["id"] for f in rows])
        return [_result_from(f, club_uid, names, found[f["id"]]) | {"crowd": f["attendance"] or None} for f in rows]

    rivals = []
    for rival in q.club_relations(connection, club_uid)["Rival"][:RIVALS_IN_NOTEBOOK]:
        games = q._rows(connection, "select f.* from fixtures f join competitions c on c.fm_uid = f.competition_uid "
                                    f"where c.scope in {COMPETITIVE} and f.home_goals is not null and "
                                    "((f.home_uid = :t and f.away_uid = :r) or (f.home_uid = :r and f.away_uid = :t))",
                        t=club_uid, r=rival["subject_uid"])
        if games:
            results = [q.result_for(f, club_uid)[2] for f in games]
            rivals.append({"rival": rival["name"], "rivalry": rival["reason_name"] or None, "games": len(games),
                           "won": results.count("W"), "drawn": results.count("D"), "lost": results.count("L")})
    return {"great_finals": told or None, "biggest_wins": margins("desc"), "heaviest_defeats": margins("asc"),
            "against_rivals": rivals or None}


def club_finances(connection: Connection, club_uid: int) -> dict | None:
    """The club's money as the newest save has it, in pounds and euros: what it has, what it may spend, what it
    earns and what it owes. Only true as of that date."""
    money = q.club_money(connection, club_uid)
    if money is None:
        return None
    now = money["now"]

    def both(pounds: int | None) -> dict | None:
        return {"pounds": pounds, "euros": round(pounds * EUROS_PER_POUND)} if pounds is not None else None

    first = money["history"][0]
    return {
        "as_of": str(now["game_date"]),
        "use": "the club's money at that date only; tell it as where the club stands now, never as a past season's",
        "bank_balance": both(now["bank"]), "transfer_budget": both(now["transfer_budget"]), "wage_budget": both(now["wage_budget"]),
        "income_a_year": both(money["yearly_income"]) if money["incomes"] else None,
        "biggest_income_streams": [{"type": i["kind_name"], "a_year": both(i["yearly"]), "until": str(i["ends"]) if i["ends"] else None}
                                   for i in money["incomes"][:3]] or None,
        "owed": both(money["debt_total"]) if money["debts"] else None,
        "debts": [{"from": d["source_name"] or "unnamed", "borrowed": both(d["amount"]), "until": str(d["ends"]) if d["ends"] else None}
                  for d in money["debts"][:4]] or None,
        "bank_balance_earlier": {"game_date": str(first["game_date"]), **both(first["bank"])}
        if first is not now and first["bank"] is not None else None,
    }


def _runs(seasons: list[str]) -> dict:
    """A trophy's longest run of wins in consecutive seasons and the run up to its latest win, counted here:
    writers miscount ("2022/23" and "2023/24" follow each other, as do "2061" and "2062")."""
    years = sorted({int(label[:4]) for label in seasons if label[:4].isdigit()})
    if not years:
        return {}
    runs, start = [], years[0]
    for previous, year in zip(years, years[1:]):
        if year != previous + 1:
            runs.append((start, previous))
            start = year
    runs.append((start, years[-1]))
    label = {int(x[:4]): x for x in seasons if x[:4].isdigit()}
    first, last = max(runs, key=lambda run: (run[1] - run[0], run[1]))
    found = {"longest_run": {"wins_in_a_row": last - first + 1, "from": label[first], "to": label[last]}}
    if runs[-1] != (first, last) and runs[-1][1] > runs[-1][0]:
        found["latest_run"] = {"wins_in_a_row": runs[-1][1] - runs[-1][0] + 1, "from": label[runs[-1][0]], "to": label[runs[-1][1]]}
    return found


def club_season_notebook(connection: Connection, club_uid: int, season: int) -> dict | None:
    team = q.teams(connection, [club_uid]).get(club_uid)
    if team is None:
        return None
    today = q.game_date(connection)
    names = _Lookup(connection)
    league = q._one(connection, "select league_uid, count(*) n from player_seasons where team_uid = :t and season_start = :s and kind = 1 "
                                "and league_uid is not null group by 1 order by n desc limit 1", t=club_uid, s=season)
    league_uid = league["league_uid"] if league else None
    label, start, end = _season_label(connection, league_uid, season)
    data = q.club_season(connection, club_uid, season)
    fixtures = data["fixtures"]
    goals = _goals(connection, [f["id"] for f in fixtures])
    results = [_result_from(f, club_uid, names, goals[f["id"]]) for f in fixtures]
    unplayed = q._one(connection, "select count(*) n from fixtures where (home_uid = :t or away_uid = :t) and date between :s and :e "
                                  "and home_goals is null", t=club_uid, s=start, e=end)["n"]
    in_progress = bool(unplayed) or (today is not None and str(today) <= end)

    table_part = None
    if league_uid:
        table = q.league_table(connection, league_uid, start, end)
        row = next((r for r in table if r["team_uid"] == club_uid), None)
        if row:
            around = [r for r in table if abs(r["position"] - row["position"]) <= 2 or r["position"] <= 3]
            table_part = {"league": names.competition(league_uid), "teams": len(table), "position": row["position"],
                          "points": row["points"], "played": row["played"], "won": row["won"], "drawn": row["drawn"], "lost": row["lost"],
                          "goals_for": row["scored"], "goals_against": row["conceded"],
                          "top_and_around": [{"position": r["position"], "team": names.team(r["team_uid"]), "points": r["points"],
                                              "played": r["played"]} for r in around]}
    previous = next((s for s in q.club_overview(connection, club_uid)["seasons"] if s["season"] == season - 1), None)

    margins = sorted((r for r in results), key=lambda r: int(r["score"].split("-")[0]) - int(r["score"].split("-")[1]))
    squad = []
    nations = {p["fm_uid"]: p for p in q._rows(connection,
        "select p.fm_uid, p.birth_date, n.adjective from people p left join nations n on n.fm_uid = p.nation_uid where p.fm_uid in "
        f"({','.join(str(int(p['uid'])) for p in data['players']) or '0'})")}
    caps = {r["person_uid"]: r for r in q._rows(connection,
        "select person_uid, sum(starts + substitute_appearances) games, sum(goals) goals from player_seasons where team_uid = :t "
        "and season_start = :s and kind = 4 group by 1", t=club_uid, s=season)}
    positions = q.main_positions(connection, [p["uid"] for p in data["players"]])
    season_start = date.fromisoformat(start)
    for p in data["players"][:SQUAD_SIZE]:
        info = nations.get(p["uid"], {})
        squad.append({"name": p["name"], "age": q.age_on(info.get("birth_date"), season_start), "nationality": info.get("adjective"),
                      "position": POSITION_WORDS.get(positions.get(p["uid"]), positions.get(p["uid"])),
                      "games": p["apps"], "goals": p["goals"], "assists": p["assists"], "league_games": p["league_apps"],
                      "league_goals": p["league_goals"], "player_of_the_match": p["pom"], "average_rating": p["rating"],
                      "awards": p["awards"], "national_team_games": (caps.get(p["uid"]) or {}).get("games")})

    def move(t: dict, side: str) -> dict:
        return {"date": str(t["date"]), "player": t["name"], "kind": q.TRANSFER_KINDS.get(t["kind"]), side: names.team(t[f"{side}_uid"]),
                "fee_pounds": t["fee"] or None, "fee_euros": round(t["fee"] * EUROS_PER_POUND) if t["fee"] else None}

    reports = [m for m in db.session.query(MatchText).filter(MatchText.kind == REPORT, MatchText.date >= date.fromisoformat(start),
                                                             MatchText.date <= date.fromisoformat(end))
               .filter((MatchText.home_uid == club_uid) | (MatchText.away_uid == club_uid)).order_by(MatchText.date).limit(6)]
    old = db.session.get(ClubSeasonStory, (club_uid, season))
    return {
        "game_date": str(today) if today else None,
        "club": team["name"], "season": label,
        "status": ("in progress: write from the present moment; do not state a final position, trophy or legacy"
                   if in_progress else "finished"),
        "league": table_part,
        "previous_season": {"season": q.season_label(previous["season"]), "league": names.competition(previous["league_uid"]),
                            "position": previous["position"], "points": previous["points"]} if previous else None,
        "record_all_competitions": dict(data["record"]),
        "won": [{"competition": names.competition(x["competition_uid"]), "decided_on": str(x["decided_on"])} for x in data["won"]],
        "runner_up": [{"competition": names.competition(x["competition_uid"]), "decided_on": str(x["decided_on"])} for x in data["runner_up"]],
        "finals": [_result_from(f, club_uid, names, goals.get(f["id"]) or _goals(connection, [f["id"]])[f["id"]]) for f in data["finals"]],
        "biggest_wins": [r for r in reversed(margins) if r["result"] == "won"][:3],
        "heaviest_defeats": [r for r in margins if r["result"] == "lost"][:3],
        "results": results,
        "squad": squad,
        "best_eleven": [{"position": POSITION_WORDS.get(b["position"], b["position"]), "player": b["name"], "games": b["appearances"],
                         "goals": b["goals"]} for b in data["eleven"] if b["slot"] < 11],
        "signed": [move(t, "from") for t in data["signed"]],
        "left": [move(t, "to") for t in data["left"]],
        "match_reports": [{"date": str(m.date), "text": _colour(m.text)} for m in reports] or None,
        # an older story only when the curator wrote or edited it: an AI story given back is copied, not improved
        "old_story": ({"text": _colour(old.text), "use": "colour only; every fact above wins where the two disagree"}
                      if old and old.text and old.source != STORY_GENERATED else None),
        "club_background": club_background(club_uid),
        "club_relationships": club_relationships(connection, team.get("club_uid") or club_uid),
        # the money is the newest save's: it belongs only in the season going on or the one just ended
        "finances": club_finances(connection, team.get("club_uid") or club_uid)
        if today and season >= (today.year if today.month >= 7 else today.year - 1) - 1 else None,
    }


# -- an edition ---------------------------------------------------------------------------------------
def edition_notebook(connection: Connection, competition_uid: int, year: int) -> dict | None:
    comp = q.competition(connection, competition_uid)
    data = q.edition(connection, competition_uid, year) if comp else None
    if data is None:
        return None
    today = q.game_date(connection)
    names = _Lookup(connection)
    e = data["edition"]
    goals = _goals(connection, [f["id"] for f in data["played"]])

    def line(f: dict) -> dict:
        row = {"date": str(f["date"]), "home": names.team(f["home_uid"]), "away": names.team(f["away_uid"])}
        if f["home_goals"] is not None:
            full = f["home_goals_after_extra_time"] is not None
            row["score"] = (f"{f['home_goals_after_extra_time']}-{f['away_goals_after_extra_time']}" if full
                            else f"{f['home_goals']}-{f['away_goals']}")
            if full:
                row["after_extra_time"] = True
            if f["home_penalties"] is not None:
                row["penalties"] = f"{f['home_penalties']}-{f['away_penalties']}"
        return row

    def table(rows: list[dict]) -> list[dict]:
        return [{"position": r["position"], "team": names.team(r["team_uid"]), "played": r["played"], "won": r["won"], "drawn": r["drawn"],
                 "lost": r["lost"], "goals_for": r["scored"], "goals_against": r["conceded"], "points": r["points"]} for r in rows]

    rounds = []
    for r in data["rounds"]:
        item = {"round": r["name"], "first_date": str(r["games"][0]["date"])}
        if r.get("legs"):
            item["with"] = r["legs"]
        if r.get("table"):
            item["table"] = table(r["table"])
        elif len(r["games"]) <= 32:
            item["games"] = [line(f) for f in r["games"]]
        else:
            item["games_count"] = len(r["games"])
        rounds.append(item)
    played = data["played"]
    margins = sorted(played, key=lambda f: -abs((f["home_goals"] or 0) - (f["away_goals"] or 0)))[:5]
    final = None
    if data["final"]:
        f = data["final"]
        final = line(f) | {"scorers": [f"{_scorer(g)} ({names.team(f['home_uid'] if g['side'] == 0 else f['away_uid'])})"
                                       for g in (goals.get(f["id"]) or _goals(connection, [f["id"]])[f["id"]])]}
    winner = data["places"].get(1)
    titles = None
    if winner:
        titles = q._one(connection, "select count(*) n from edition_places where competition_uid = :c and team_uid = :t and place = 1 "
                                    "and year <= :y", c=data["parts"][0]["competition_uid"] if data["parts"] else competition_uid,
                        t=winner, y=year)["n"]
    before = q._one(connection, "select p.team_uid, e.season_label from competition_editions e join edition_places p using (competition_uid, year) "
                                "where e.competition_uid = :c and e.year < :y and p.place = 1 order by e.year desc limit 1",
                    c=competition_uid, y=year)
    old = db.session.get(EditionStory, (competition_uid, year))
    chapters = db.session.query(EditionChapter).filter_by(competition_uid=competition_uid, year=year).order_by(EditionChapter.sequence).all()
    return {
        "game_date": str(today) if today else None,
        "competition": comp["name"], "edition": e["season_label"],
        "status": "finished" if e.get("decided_on") else "in progress: write from the present moment; do not crown a winner",
        "decided_on": str(e["decided_on"]) if e.get("decided_on") else None,
        "winner": names.team(winner), "runner_up": names.team(data["places"].get(2)), "third": names.team(data["places"].get(3)),
        "winner_titles_so_far": titles,
        "champion_decided_in": names.competition(data["parts"][0]["competition_uid"]) if data["parts"] else None,
        "other_titles_this_season": {names.competition(p["competition_uid"]): names.team(p["winners"].get(year)) for p in data["parts"][1:]} or None,
        "previous_winner": {"team": names.team(before["team_uid"]), "edition": before["season_label"]} if before else None,
        "games_played": len(played), "goals": data["goals"],
        "final": final,
        "table": table(data["table"]) if data["is_league"] else None,
        "rounds": rounds if not data["is_league"] or len(rounds) > 1 else None,
        "biggest_results": [line(f) | {"competition_round": next((r["name"] for r in data["rounds"] if f in r["games"]), None)} for f in margins],
        "top_scorers": [{"player": s["name"], "team": names.team(s["team_uid"]), "goals": s["goals"], "penalties": s["penalties"]}
                        for s in data["scorers"][:10]],
        "older_texts": ([_colour(old.text)] if old and old.text and old.source != STORY_GENERATED else [])
                       + [_colour(c.body) for c in chapters] or None,
    }


# -- a match ------------------------------------------------------------------------------------------
STANDOUTS = 3  # players of each side named in a match report's facts
BIG_CHANCE_XG = 0.5  # a player whose chances add up to this and who did not score "missed chances"


def _on_pitch(player: dict) -> str:
    """ "came on 71'", "off 69'", "sent off 45+1'"... nothing for ninety (or 120) minutes."""
    def at(minute: int, added: int = 0) -> str:
        return f"{minute}+{added}'" if added else f"{minute}'"
    if player["red_cards"]:
        return f"sent off {at(player['sent_off_minute'], player['added_time_off'])}"
    parts = []
    if player["minute_on"]:
        parts.append(f"came on {at(player['minute_on'])}")
    if player["injury_minute"]:
        parts.append(f"went off injured {at(player['injury_minute'], player['added_time_off'])}")
    elif player["minute_off"]:
        parts.append(f"replaced {at(player['minute_off'], player['added_time_off'])}")
    return ", ".join(parts)


def _player_line(player: dict) -> str:
    """What a player did, in words: "1 goal, 5 key passes, 13.4 km"."""
    def count(n: int, one: str, many: str) -> str | None:
        return f"{n} {one if n == 1 else many}" if n else None
    bits = [count(player["goals"], "goal", "goals"), count(player["assists"], "assist", "assists"),
            count(player["key_passes"], "key pass", "key passes"),
            f"{player['shots']} shots, {player['shots_on_target']} on target" if player["shots"] >= 3 else None,
            f"won {player['tackles_won']} tackles" if player["tackles_won"] >= 4 else None,
            f"{player['interceptions']} interceptions" if player["interceptions"] >= 4 else None,
            f"{player['clearances']} clearances" if player["clearances"] >= 6 else None,
            f"{player['dribbles']} dribbles" if player["dribbles"] >= 4 else None,
            f"{player['passes_completed']} of {player['passes']} passes" if player["passes"] >= 60 else None,
            f"{player['distance'] / 1000:.1f} km" if (player["distance"] or 0) >= 12_500 else None,
            count((player.get("saves_held") or 0) + (player.get("saves_parried") or 0) + (player.get("saves_tipped") or 0), "save", "saves")
            if (player.get("saves_held") or 0) + (player.get("saves_parried") or 0) + (player.get("saves_tipped") or 0) >= 3 else None]
    return ", ".join(bit for bit in bits if bit)


def known_players_facts(connection: Connection, fixture_id: int) -> dict | None:
    """What the archive knows of a match whose full report is gone: the rows the game kept for the players it follows
    (player_games), each in words, with the minutes and the rating. None when there are no such rows."""
    sides = q.fixture_player_games(connection, fixture_id)
    if not any(sides):
        return None
    names = _Lookup(connection)
    f = q._one(connection, "select home_uid, away_uid from fixtures where id = :f", f=fixture_id)
    out = []
    for team_uid, rows in zip((f["home_uid"], f["away_uid"]), sides):
        for r in rows:
            line = {"name": r["name"], "team": names.team(team_uid)}
            if r["status"] != "played":
                line["note"] = r["status_word"]
            else:
                keeper = r["position"] == "GK"
                bits = [f"{r['goals']} goal{'s' if r['goals'] > 1 else ''}" if r["goals"] else None,
                        f"{r['dribbles']} saves" if keeper and (r["dribbles"] or 0) >= 3 else None,
                        (f"{r['conceded']} conceded" if r["conceded"] else "a clean sheet") if keeper else None,
                        f"xG against {r['xg']:.2f}" if keeper and (r["xg"] or 0) >= 1 else None,
                        f"{r['assists']} assist{'s' if r['assists'] > 1 else ''}" if r["assists"] else None,
                        f"{r['key_passes']} key passes" if (r["key_passes"] or 0) >= 2 else None,
                        f"{r['shots']} shots, {r['shots_on_target']} on target" if (r["shots"] or 0) >= 3 else None,
                        f"won {r['tackles_won']} tackles" if (r["tackles_won"] or 0) >= 4 else None,
                        f"{r['interceptions']} interceptions" if (r["interceptions"] or 0) >= 4 else None,
                        f"{r['passes_completed']} of {r['passes']} passes" if (r["passes"] or 0) >= 60 else None,
                        f"xG {r['xg']:.2f}" if not keeper and (r["xg"] or 0) >= 0.5 else None,
                        "a yellow card" if r["yellow_cards"] else None]
                line.update(position=r["position"], rating=r["rating"], minutes=r["minutes"], replaced_at=r["minute_off"] or None,
                            came_on=(r["minutes"] and not r["minute_off"] and r["minutes"] < 90) or None, did=", ".join(b for b in bits if b) or None)
            out.append(line)
    return {"note": "the game's full report of this match is gone; these are the rows it kept for the players it follows, nobody else",
            "players": out}


def match_report_facts(connection: Connection, fixture_id: int) -> dict | None:
    """The game's own report of a match, for a writer: who had the ball and the chances, the
    Player of the Match, the stand-out players, red cards, injuries and chances missed."""
    report = q.match_report(connection, fixture_id)
    if report is None or None in report["sides"]:
        return None
    names = _Lookup(connection)
    teams = [names.team(side["team_uid"]) for side in report["sides"]]
    players = [dict(player, team=teams[player["side"]]) for side in report["sides"] for player in side["players"]
               if player["played"] and player["name"]]  # a writer cannot use a player without a name

    def side_line(side: dict) -> dict:
        row = {"team": names.team(side["team_uid"]), "possession": f"{side['possession']}%" if side.get("possession") is not None else None,
               "shots": side["shots"], "shots_on_target": side["shots_on_target"], "expected_goals": side["xg"],
               "clear_cut_chances": side["clear_cut_chances"], "half_chances": side.get("half_chances") or None, "long_shots": side.get("long_shots") or None,
               "corners": side["corners"], "hit_the_woodwork": side["woodwork"] or None,
               "keeper_saves": (f"{side['saves']}" + (f", {side['saves_tipped']} tipped over or round the post" if side["saves_tipped"] else "")
                                + (f", {side['saves_parried']} parried" if side["saves_parried"] else "")) if side.get("saves_known") else None,
               "passes_completed": f"{side['passes_completed']} of {side['passes']}", "fouls": side["fouls"],
               "yellow_cards": side["yellow_cards"], "red_cards": side["red_cards"] or None}
        return {key: value for key, value in row.items() if value is not None}

    def person(player: dict, rated: bool = True) -> dict:
        row = {"name": player["name"], "team": player["team"]}
        if rated and player["rating_shown"]:
            row["rating"] = round(player["rating_shown"], 1)
        line, pitch = _player_line(player), _on_pitch(player)
        if line:
            row["did"] = line
        if pitch:
            row["minutes"] = pitch
        return row

    pom = next((player for player in players if player["person_uid"] and player["person_uid"] == report["player_of_the_match_uid"]), None)
    standouts = []
    for side in (0, 1):
        rated = sorted((player for player in players if player["side"] == side and player["rating_shown"] and player is not pom),
                       key=lambda player: -player["rating_shown"])
        standouts += [person(player) for player in rated[:STANDOUTS]]
    facts = {
        "stadium": report["stadium"], "referee": report["referee_name"], "minutes_played": report["minutes"],
        "teams": [side_line(side) for side in report["sides"]],
        "player_of_the_match": person(pom) if pom else None,
        "stand_out_players": standouts,
        "sent_off": [person(player, rated=False) for player in players if player["red_cards"]] or None,
        "injured": [person(player, rated=False) for player in players if player["injury_minute"]] or None,
        "booked": [f"{player['name']} ({player['team']}, {player['booked_minute']}')" for player in players if player["yellow_cards"]] or None,
        "substitutes_who_made_a_difference": [person(player) for player in players if player["minute_on"]
                                              and (player["goals"] or player["assists"])] or None,
        "chances_missed": [{"name": player["name"], "team": player["team"], "shots": player["shots"], "expected_goals": round(player["xg"], 2)}
                           for player in players if player["xg"] >= BIG_CHANCE_XG and not player["goals"]] or None,
        "note": "Ratings are the match ratings out of 10; expected goals measure the quality of the chances.",
    }
    selection = tactics.selection_notes(connection, fixture_id)
    if selection:
        facts["line_ups"] = {names.team(team): side["line_up"] for team, side in selection.items()}
        facts["team_selection"] = {names.team(team): side["notes"] for team, side in selection.items() if side["notes"]} or None
        facts["substitutions"] = {names.team(team): side["substitutions"] for team, side in selection.items() if side["substitutions"]} or None
        facts["note"] += (" line_ups are the team sheets: each player's slot (GK, DC centre-back, DR/DL full-backs, WBR/WBL "
                          "wing-backs, DM, MC, MR/ML, AMR/AML wingers, AMC, ST) and shirt number, the shape back to front. "
                          "team_selection says what was unusual about each side's selection, against its recent matches: tell "
                          "the story in it where it matters (a regular dropped or injured and who took his place, a new "
                          "signing or a youngster starting, a change of system, a partnership). Only the slots are known: "
                          "never invent roles, instructions or tactics beyond them.")
    return {key: value for key, value in facts.items() if value is not None}


def shot_words(across: float, height: float) -> str:
    """Where a goal crossed the line, in words: the posts at +-3.66 m, the bar at 2.44 (the side is not told: the pitch's
    sides, not the shooter's)."""
    corner = abs(across) >= 2.2
    if height >= 1.7:
        return "into the top corner" if corner else "high into the net"
    if height <= 0.9:
        return "into the bottom corner" if corner else "low into the net"
    return "inside the post" if corner else "into the middle of the goal"


CHANCES_TOLD = 10  # the chances that were not goals a match notebook lists, the nearest misses first
POST, BAR = 3.66, 2.44


def chance_words(outcome: str, across: float, height: float) -> str:
    """A chance that was not a goal, in words: where it went and what became of it (31 Oct 2026: the save gives every shot's
    crossing point and second, and the writers invented the rest)."""
    if outcome == "woodwork":
        return "hit the bar" if height >= BAR - 0.4 else "hit the post"
    if outcome == "off target":
        wide, over = abs(across) > POST, height > BAR
        return "wide and over" if wide and over else "over the bar" if over else "wide" if wide else "off target"
    if outcome == "blocked":
        return "blocked"
    if outcome == "saved":
        if abs(across) > POST or height > BAR:
            return "saved, going wide"
        return "saved " + ("low at the post" if abs(across) >= 2.2 and height <= 0.9 else "high at the post" if abs(across) >= 2.2 and height >= 1.7
                           else "at the post" if abs(across) >= 2.2 else "low in the middle" if height <= 0.9 else "high in the middle" if height >= 1.7
                           else "in the middle")
    return outcome


def _chance_rank(shot: dict) -> tuple:
    """The nearest misses first: the woodwork, then saves in the frame, then blocks, then the rest; the earliest first."""
    order = {"woodwork": 0, "saved": 1 if abs(shot["across"]) <= POST and shot["height"] <= BAR else 3, "blocked": 2}
    return (order.get(shot["outcome"], 4), shot["minute"], shot["second"])


def match_notebook(connection: Connection, fixture_id: int) -> dict | None:
    data = q.match(connection, fixture_id)
    if data is None:
        return None
    f = data["fixture"]
    names = _Lookup(connection)
    day = date.fromisoformat(str(f["date"]))
    scorers = {g["scorer_uid"] for g in data["goals"] if g["scorer_uid"]}
    people = {p["fm_uid"]: p for p in q._rows(connection,
        "select p.fm_uid, p.birth_date, n.adjective from people p left join nations n on n.fm_uid = p.nation_uid "
        f"where p.fm_uid in ({','.join(str(int(u)) for u in scorers) or '0'})")}
    positions = q.main_positions(connection, scorers)
    club_standing = _standing_here(connection, scorers, (f["home_uid"], f["away_uid"]))

    every_shot = q._rows(connection, "select s.side, s.person_uid, s.minute, s.second, s.outcome, s.across, s.height, s.outside_box, p.name "
                                     "from match_shots s left join people p on p.fm_uid = s.person_uid where s.fixture_id = :f "
                                     "order by s.minute, s.second", f=fixture_id)
    shots = [sh for sh in every_shot if sh["outcome"] == "goal"]
    culprits = q._rows(connection, "select m.side, m.mistakes_leading_to_goal mistakes, p.name from match_players m join people p on p.fm_uid = m.person_uid "
                                   "where m.fixture_id = :f and coalesce(m.mistakes_leading_to_goal, 0) > 0", f=fixture_id)
    conceded = {0: sum(1 for g in data["goals"] if g["side"] == 1 and g["kind"] in (0, 1, 2)),
                1: sum(1 for g in data["goals"] if g["side"] == 0 and g["kind"] in (0, 1, 2))}

    def goal(g: dict) -> dict:
        person = people.get(g["scorer_uid"], {})
        found = {"minute": _minute(g), "team": names.team(f["home_uid"] if g["side"] == 0 else f["away_uid"]), "scorer": g["name"],
                 "kind": {0: "goal", 1: "own goal", 2: "penalty"}[g["kind"]], "age": q.age_on(person.get("birth_date"), day),
                 "nationality": person.get("adjective"), "position": POSITION_WORDS.get(positions.get(g["scorer_uid"]))}
        shot = next((sh for sh in shots if sh["side"] == g["side"] and sh["person_uid"] == g["scorer_uid"] and abs(sh["minute"] - g["minute"]) <= 1), None)
        if shot:
            found["where_it_went"] = shot_words(shot["across"], shot["height"])
            found["at"] = f"{shot['minute']}:{shot['second']:02d}"  # the clock to the second, from the shot
            if shot["outside_box"] == 1:
                found["from_outside_the_box"] = True
        blamed = [c for c in culprits if c["side"] != g["side"]]  # the conceding side's mistakes: tied to the goal only when it is the only one
        if len(blamed) == 1 and blamed[0]["mistakes"] == 1 and conceded[blamed[0]["side"]] == 1:
            found["after_a_mistake_by"] = blamed[0]["name"]
        if g["scorer_uid"] in club_standing:
            found["standing_with_these_clubs"] = club_standing[g["scorer_uid"]]
        return found

    def form(team_uid: int) -> list[dict]:
        before = q._rows(connection, "select * from fixtures where (home_uid = :t or away_uid = :t) and date < :d and home_goals is not null "
                                     "order by date desc limit 5", t=team_uid, d=str(day))
        return [_result_from(x, team_uid, names) for x in before]

    def standing(team_uid: int) -> dict | None:
        league = q._one(connection, "select league_uid from player_seasons where team_uid = :t and kind = 1 and league_uid is not null "
                                    "and season_start <= :y order by season_start desc limit 1", t=team_uid, y=day.year)
        if not league:
            return None
        season = day.year if day.month >= 7 else day.year - 1
        start, end = q.league_window(connection, league["league_uid"], season)
        if start.endswith("-01-01"):
            start = f"{day.year}-01-01"
        table = q.league_table(connection, league["league_uid"], start, str(day - timedelta(days=1)))
        row = next((r for r in table if r["team_uid"] == team_uid), None)
        return {"league": names.competition(league["league_uid"]), "position": row["position"], "points": row["points"],
                "played": row["played"], "teams": len(table)} if row and row["played"] else None

    round_name, after, edition = None, None, None
    if data["edition"]:
        edition = q.edition(connection, f["competition_uid"], data["edition"]["year"])
        round_name = next((r["name"] for r in edition["rounds"] if any(x["id"] == f["id"] for x in r["games"])), None) if edition else None
        scope = q._one(connection, "select scope from competitions where fm_uid = :c", c=f["competition_uid"])
        after = q.next_round(edition["stages"], f, drawn=bool(scope and scope["scope"] == "cup")) if edition else None
    full = f["home_goals_after_extra_time"] is not None
    preview = db.session.query(MatchText).filter_by(date=day, home_uid=f["home_uid"], away_uid=f["away_uid"], kind=PREVIEW).first()
    meetings = data["meetings"][:5]
    return {
        "date": str(day), "weekday": day.strftime("%A"), "competition": names.competition(f["competition_uid"]),
        "edition": data["edition"]["season_label"] if data["edition"] else None, "round": round_name, "is_final": data["is_final"],
        "home": names.team(f["home_uid"]), "away": names.team(f["away_uid"]),
        "score": (f"{f['home_goals_after_extra_time']}-{f['away_goals_after_extra_time']}" if full else f"{f['home_goals']}-{f['away_goals']}")
        if f["home_goals"] is not None else None,
        "score_after_90_minutes": f"{f['home_goals']}-{f['away_goals']}" if full else None,
        "penalties": f"{f['home_penalties']}-{f['away_penalties']}" if f["home_penalties"] is not None else None,
        "attendance": f["attendance"] or None, "away_fans": f["away_fans"] or None,
        "goals": [goal(g) for g in data["goals"] if g["kind"] in (0, 1, 2)],
        "chances_that_were_not_goals": [
            {"at": f"{sh['minute']}:{sh['second']:02d}", "team": names.team(f["home_uid"] if sh["side"] == 0 else f["away_uid"]),
             "player": sh["name"], "what": chance_words(sh["outcome"], sh["across"], sh["height"]),
             **({"from_outside_the_box": True} if sh["outside_box"] == 1 else {})}
            for sh in sorted((sh for sh in every_shot if sh["outcome"] != "goal" and sh["name"]), key=_chance_rank)[:CHANCES_TOLD]] or None,
        "mistakes_leading_to_goals": [f"{c['name']} ({names.team(f['home_uid'] if c['side'] == 0 else f['away_uid'])})"
                                      + (f", {c['mistakes']} goals" if c["mistakes"] > 1 else "") for c in culprits] or None,
        "shoot_out": [{"team": names.team(f["home_uid"] if g["side"] == 0 else f["away_uid"]), "taker": g["name"], "scored": g["kind"] == 4}
                      for g in data["goals"] if g["kind"] in (4, 5)] or None,
        "home_form_before": form(f["home_uid"]), "away_form_before": form(f["away_uid"]),
        "home_league_before": standing(f["home_uid"]), "away_league_before": standing(f["away_uid"]),
        "head_to_head_before": {"home_wins": data["record"]["home"], "draws": data["record"]["drawn"], "away_wins": data["record"]["away"]},
        "last_meetings": [_result_from(m, f["home_uid"], names) | {"from_side_of": names.team(f["home_uid"])} for m in meetings],
        "preview": {"text": _colour(preview.text), "use": "colour only"} if preview else None,
        "match_report": (report_facts := match_report_facts(connection, fixture_id)),
        "players_we_know_of": known_players_facts(connection, fixture_id) if report_facts is None and f["home_goals"] is not None else None,
        "next_round": _next_round(after, names),
        "road_so_far": _road_so_far(edition, f, day, names) if edition and not edition["is_league"] else None,
        "expectation": _expectation(connection, f, day, names),
        "rivalry": _rivalry(connection, f),
    }


def _standing_here(connection: Connection, people: set[int], team_uids: tuple[int, int]) -> dict[int, list[str]]:
    """The people who are a Legend, an Icon or disliked at either club of a match (the game's lists)."""
    clubs = {row["club_uid"] for row in q.teams(connection, team_uids).values() if row["club_uid"]}
    found: dict[int, list[str]] = {}
    for uid in people:
        for r in q.person_club_relations(connection, uid):
            if r["club_uid"] in clubs and r["group"] in ("Legend", "Icon", "Disliked"):
                found.setdefault(uid, []).append(f"{'disliked' if r['group'] == 'Disliked' else r['group'].lower()} at {r['club_name']}")
    return found


def _rivalry(connection: Connection, fixture: dict) -> dict | None:
    """How each club ranks the other as a rival (the game's lists), when either does."""
    clubs = {t: row["club_uid"] for t, row in q.teams(connection, [fixture["home_uid"], fixture["away_uid"]]).items() if row["club_uid"]}
    home, away = clubs.get(fixture["home_uid"]), clubs.get(fixture["away_uid"])
    if not (home and away):
        return None
    found = {}
    for club, other, side in ((home, away, "home"), (away, home, "away")):
        rival = q.rivalry(connection, club, other)
        if rival:
            found[f"{side}_sees_the_other_as"] = {"rival_level_of_100": rival["level"], "rivalry": rival["reason"] or None}
    return found or None


def _road_so_far(edition: dict, fixture: dict, day: date, names: _Lookup) -> dict | None:
    """Each side's way through this edition of the competition before the day: its group or league
    phase as a record and a place, and every other game (qualifying and knockout rounds) one by one."""
    road = {}
    for side in (fixture["home_uid"], fixture["away_uid"]):
        entry: dict = {}
        games = []
        for r in edition["rounds"]:
            mine = [g for g in r["games"] if side in (g["home_uid"], g["away_uid"])]
            before = [g for g in mine if str(g["date"]) < str(day) and g["home_goals"] is not None]
            if "table" not in r:
                games += [{"round": r["name"]} | {k: v for k, v in _result_from(g, side, names).items() if k != "competition"} for g in before]
                continue
            if not mine:
                continue
            group = {t for g in mine for t in (g["home_uid"], g["away_uid"])}  # a group: the side and the teams it meets
            pool = r["games"] if r["name"] == "League phase" else [g for g in r["games"] if {g["home_uid"], g["away_uid"]} <= group]
            table = q.table_from([g for g in pool if str(g["date"]) < str(day)])
            row = next((row for row in table if row["team_uid"] == side), None)
            if row:
                finished = all(str(g["date"]) < str(day) for g in mine)
                entry["league_phase" if r["name"] == "League phase" else "group"] = {
                    "record": f"won {row['won']}, drawn {row['drawn']}, lost {row['lost']}, goals {row['scored']}-{row['conceded']}",
                    "points": row["points"], "place": f"{row['position']} of {len({t for g in pool for t in (g['home_uid'], g['away_uid'])})}",
                    "status": "finished" if finished else f"before this game, {row['played']} of {len(mine)} played"}
        entry["games"] = games or "none before this match"
        if len(games) > 1:  # counted here: writers miscount
            count = Counter(game["result"] for game in games)
            goals = [tuple(map(int, game["score"].split("-"))) for game in games]
            entry["record_before_this_match"] = (f"won {count['won']}, drawn {count['drawn']}, lost {count['lost']}, "
                                     f"goals {sum(a for a, _ in goals)}-{sum(b for _, b in goals)}")
        road[names.team(side)] = entry
    return road


FAVOURITE_GAP = (500, 1500, 3000)  # reputation gaps (0-10 000): slight, clear and overwhelming favourites


def _expectation(connection: Connection, fixture: dict, day: date, names: _Lookup) -> dict | None:
    """Who was expected to win, from the game's reputations (as of the latest save) and the sides'
    league levels, and how the result measured up to that: as expected, an upset, a giant-killing."""
    sides = {}
    for side in (fixture["home_uid"], fixture["away_uid"]):
        team = q._one(connection, "select reputation from teams where fm_uid = :t", t=side)
        league = q._one(connection, "select c.level, c.scope from player_seasons s join competitions c on c.fm_uid = s.league_uid "
                                    "where s.team_uid = :t and s.kind = 1 and s.season_start <= :y order by s.season_start desc limit 1",
                        t=side, y=day.year)
        sides[side] = {"reputation": team and team["reputation"], "level": league and league["level"]}
    home, away = (sides[fixture["home_uid"]], sides[fixture["away_uid"]])
    if home["reputation"] is None or away["reputation"] is None:
        return None
    gap = home["reputation"] - away["reputation"]
    favourite, underdog = ((fixture["home_uid"], fixture["away_uid"]) if gap > 0 else (fixture["away_uid"], fixture["home_uid"]))
    strength = next((word for word, limit in zip(("overwhelming", "clear", "slight"), reversed(FAVOURITE_GAP)) if abs(gap) >= limit), None)
    facts: dict = {"favourites": names.team(favourite) if strength else "neither: evenly matched"}
    if strength:
        facts["how_clearly"] = strength
    levels = (home["level"], away["level"])
    if all(levels) and levels[0] != levels[1]:
        higher = fixture["home_uid"] if levels[0] < levels[1] else fixture["away_uid"]
        facts["divisions"] = f"{names.team(higher)} play {abs(levels[0] - levels[1])} division(s) higher"
    scored, conceded, result = q.result_for(fixture, favourite)
    level_gap = (sides[underdog]["level"] or 0) - (sides[favourite]["level"] or 0)
    if not strength:
        facts["result"] = "an even contest"
    elif result == "W":
        facts["result"] = "as expected"
    elif result == "D":
        facts["result"] = "a good draw for the underdogs" + (", though penalties decided it" if fixture["home_penalties"] is not None else "")
    elif strength != "slight" or level_gap >= 2:
        facts["result"] = "a giant-killing: the underdogs won"
    else:
        facts["result"] = "an upset: the underdogs won"
    if abs(scored - conceded) >= 4:
        facts["margin"] = "a rout"
    facts["note"] = "Say it in words; never give reputation numbers."
    return facts


def _next_round(after: dict | None, names: "_Lookup") -> dict | None:
    """The tie's winner and what it meets next, in the words the writer needs."""
    if after is None:
        return None
    facts = {"winner": names.team(after["winner"])}
    if "aggregate" in after:
        (first, second), (a, b), penalties = after["aggregate"].values()
        facts["aggregate"] = f"{names.team(first)} {a}-{b} {names.team(second)}" + (f", {penalties[0]}-{penalties[1]} on penalties" if penalties else "")
    if not after["known"]:
        return facts | {"opponent": "not known yet: name no possible opponent"}
    facts["round"] = after["round"]
    if after.get("draw_to_come"):
        return facts | {"opponent": "the draw is still to come: name no possible opponent"}
    if "opponent" in after:
        game = after["next_game"]
        return facts | {"opponent": names.team(after["opponent"]),
                        "next_game": f"{names.team(game['home'])} v {names.team(game['away'])} on {game['date']}"}
    first, second = (names.team(team) for team in after["winner_of"])
    return facts | {"opponent": f"the winner of {first} v {second}, still to be decided; it can only be one of these two"}
