"""More of a major tournament than its games (21 Nov 2026: "the groups are covered individually, the teams are covered with
short summaries, there are fan stories of fans following their team, i.e. the English journalist travelling with the
team, covering the drama"):

- each group after the cycle's games: the table, what is left, who is sure of the top two and who cannot reach it;
- the camp: a national paper's reporter with its own country's team (results, form, injuries, the next games);
- the fans on the road: the crowds at a side's games, its travelling support and the grounds, and the cycle's biggest crowds;
- before the start, a guide to each side (the squad, the stars, one to watch, the manager, the form, the record);
- once the knockout rounds are on, the road to the final: the ties played and to come, and each side's path.

The hard facts (results, tables, crowds, injuries) come from the save; the colour of a camp or a fans' piece (the mood,
the hotel, the songs, the trains) is the writer's own, and no number may be added to it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from itertools import product

from app.services import world_queries as q

GROUP_NOW_SIZE, GROUP_DONE_BONUS = 1.3, 0.4
CAMP_SIZE, FANS_SIZE, CROWDS_SIZE = 1.4, 1.0, 1.2
TOP = 2  # the places of a group the table speaks of: the top two go through in every format
MAX_LEFT = 8  # a group with more games left is not worked out (3**8 ways)
NEXT_GAMES = 2
CROWDS = 6  # the cycle's biggest crowds in the tournament-wide piece
GUIDE_SIZE, BRACKET_SIZE = 1.1, 2.0
GUIDE_STARS, FORM_GAMES, YOUNG = 5, 6, 23

THIRD_PLACE = ("Whether a third place is enough to go through (as one of the best third-placed sides) is not known here: say "
               "nothing about it.")
CAMP_NOTE = ("You travel with this squad for the paper: the camp, the training, the hotel, the mood, the bus to the ground, and "
             "what the players and the manager say to you, are your colour. A quote fits the man: his character, his form and "
             "what has happened to him here (a hat-trick, an injury, a dropped place). Every result, score, table, injury, name "
             "and number comes from the notebook alone.")
FANS_NOTE = ("A piece about the fans who follow this side: the songs, the flags, the trains and planes, the bars and the squares "
             "are your colour. The crowds, the travelling support and the grounds come from the notebook alone; add no number.")


TITLE_WORDS = {"World Cup": "the world champions", "European Football Championship": "the European champions",
               "Copa América": "the South American champions", "Africa Cup of Nations": "the African champions",
               "Asian Cup": "the Asian champions", "Gold Cup": "the Gold Cup holders", "Confederations Cup": "the Confederations Cup holders",
               "Nations League Finals": "the Nations League holders"}
CHAMPIONS_NOTE = ("A champion's result is part of the story: the world champions or the holders held, beaten or knocked out "
                  "is news, and the piece says who they are.")


def champions(w, t: dict) -> dict[int, str]:
    """The titles each national side holds as the tournament starts (21 Nov 2026: "Italy got knocked out of the Euro ...
    but no mention that they're THE WORLD CHAMPIONS"): the winner of the last decided edition of each major tournament,
    in words ("Italy: the world champions (World Cup 2062)"), and the holders of this tournament ("the holders")."""
    from app.services.tournament_leads import MAJOR_TOURNAMENTS

    cache = getattr(w, "_champions", None)
    if cache is None:
        cache = w._champions = {}
    if t["uid"] in cache:
        return cache[t["uid"]]
    held: dict[int, list[str]] = defaultdict(list)
    for uid, comp in w.competitions.items():
        name = comp.get("name") or ""
        if comp.get("scope") != "national_teams" or "Qualif" in name or name.startswith(("Under", "U1", "U2")) or "Youth" in name:
            continue
        base = next((m for m in MAJOR_TOURNAMENTS if name == m or name.startswith(m)), None)
        if base is None:
            continue
        last = w.rows("select e.year, p.team_uid from competition_editions e join edition_places p using (competition_uid, year) "
                      "where e.competition_uid = :c and p.place = 1 and e.decided_on < :d order by e.decided_on desc limit 1",
                      c=uid, d=str(t["first"]))
        if not last:
            continue
        words = "the holders" if uid == t["uid"] else TITLE_WORDS.get(base, f"the {name} holders")
        held[last[0]["team_uid"]].append(f"{words} ({name} {last[0]['year']})")
    cache[t["uid"]] = {team: f"{w.name(team)}: {' and '.join(titles)}" for team, titles in held.items()}
    return cache[t["uid"]]


def champions_in(w, t: dict, teams) -> list[str] | None:
    """The champions among these sides, for a story's facts; None when there is none."""
    held = champions(w, t)
    return [held[team] for team in sorted(set(teams), key=lambda x: -(w.rep(x) or 0)) if team in held] or None


def _groups(w, t: dict) -> list[tuple[str, list[dict]]]:
    from app.services.tournament_leads import _groups as groups

    return groups(w, t)


def _table_line(w, row: dict) -> str:
    return (f"{row['position']}. {w.name(row['team_uid'])}: played {row['played']}, won {row['won']}, drawn {row['drawn']}, "
            f"lost {row['lost']}, goals {row['scored']}-{row['conceded']}, {row['points']} points")


def group_places(games: list[dict]) -> dict[int, str]:
    """What each side of a group is sure of after the games played, on points alone (a tie may go either way): sure of the
    top TOP, unable to reach it, or still open. A finished group: each side's final place."""
    teams = sorted({x for f in games for x in (f["home_uid"], f["away_uid"])})
    table = {r["team_uid"]: r for r in q.table_from(games)}
    left = [f for f in games if f["home_goals"] is None]
    if not left:
        return {team: f"finished {_ordinal(table[team]['position'])}" for team in teams if team in table}
    if len(left) > MAX_LEFT:
        return {}
    now = {team: table.get(team, {}).get("points", 0) for team in teams}
    sure, out = set(teams), set(teams)
    for outcome in product((3, 1, 0), repeat=len(left)):
        points = dict(now)
        for f, home in zip(left, outcome):
            points[f["home_uid"]] += home
            points[f["away_uid"]] += {3: 0, 1: 1, 0: 3}[home]
        for team in teams:
            if sum(points[x] >= points[team] for x in teams if x != team) >= TOP:
                sure.discard(team)  # a way to be caught: not sure
            if sum(points[x] > points[team] for x in teams if x != team) < TOP:
                out.discard(team)  # a way to be in the top two (on goals or a tie at worst): not out
    return {team: "sure of a top-two place" if team in sure else "can no longer finish in the top two" if team in out
            else "a top-two place still open" for team in teams}


def _ordinal(n: int) -> str:
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth", 6: "sixth"}.get(n, f"{n}th")


def groups_now(w, t: dict, label: str, weight: float, Lead) -> list:
    """Each group with a game in the cycle: its table, the cycle's results, the games left and what each side is sure of."""
    leads = []
    for name, games in _groups(w, t):
        this_cycle = [f for f in games if f["home_goals"] is not None and w.since < date.fromisoformat(str(f["date"])[:10]) <= w.until]
        if not this_cycle:
            continue
        table = q.table_from(games)
        left = [f for f in games if f["home_goals"] is None]
        places = group_places(games)
        done = not left
        members = [r["team_uid"] for r in table] or sorted({x for f in games for x in (f["home_uid"], f["away_uid"])})
        grounds = _stadiums(w, [f["id"] for f in this_cycle if "id" in f])  # 21 Nov 2026: without them a writer put a game "in Sofia"
        facts = {"tournament": label, "group": name,
                 "the_table": [_table_line(w, r) for r in table],
                 "results_this_cycle": [_crowd(w, f, grounds.get(f.get("id"))) for f in this_cycle],
                 "games_left": [f"{str(f['date'])[:10]}: {w.name(f['home_uid'])} v {w.name(f['away_uid'])}" for f in left] or None,
                 "where_each_side_stands": [f"{w.name(team)}: {places[team]}" for team in members if team in places] or None,
                 "the_group_is_over": True if done else None, "about_third_place": THIRD_PLACE if not done else None}
        played = len(games) - len(left)
        leads.append(Lead("tournament_group_now", f"{label}, {name}" + (": the final table" if done else f" after {played} of {len(games)} games"),
                          w.until, weight * 10 * (GROUP_NOW_SIZE + (GROUP_DONE_BONUS if done else 0)),
                          {k: v for k, v in facts.items() if v is not None},
                          [f"competition:{t['uid']}"] + [f"club:{x}" for x in members],
                          {"world"} | {f"nation:{n}" for n in (w.team(x).get("nation_uid") for x in members) if n},
                          slug=f"{t['uid']}:{t['year']}:{name}:{w.until}"))
    return leads


def _sides(w, t: dict, coming: list[dict]) -> list[int]:
    """The sides with a game this cycle or in the next fortnight."""
    played = w.rows("select home_uid, away_uid from fixtures where competition_uid = :c and home_goals is not null and date > :s and date <= :u",
                    c=t["uid"], s=str(w.since), u=str(w.until))
    return sorted({x for f in [*played, *coming] for x in (f["home_uid"], f["away_uid"])})


def _their_games(w, t: dict, team: int) -> list[dict]:
    return w.rows("select * from fixtures where competition_uid = :c and date between :s and :e and (home_uid = :t or away_uid = :t) "
                  "order by date, id", c=t["uid"], s=str(t["first"]), e=str(t["last"]), t=team)


def camps(w, t: dict, label: str, weight: float, rounds: dict[int, str], coming: list[dict], Lead) -> list:
    """The reporter with each side (for its own country's paper): its games so far, its group, its best players at the
    tournament, its squad's injuries, its next games."""
    from app.services.story_leads_now import _manager
    from app.services.tournament_leads import _group_lines, _score, _squad

    leads = []
    for team in _sides(w, t, coming):
        nation = w.team(team).get("nation_uid")
        if not nation:
            continue
        games = _their_games(w, t, team)
        played = [f for f in games if f["home_goals"] is not None]
        next_games = [f for f in games if f["home_goals"] is None][:NEXT_GAMES]
        squad = {p["fm_uid"]: p for p in _squad(w, team)}
        injured = w.rows(f"select person_uid, injury, days from injuries where current = 1 and person_uid in ({','.join(str(u) for u in squad) or '0'})")
        best = w.rows("select p.fm_uid, p.name, count(*) games, sum(m.goals) goals, sum(m.assists) assists, avg(m.rating) rating from match_players m "
                      "join fixtures f on f.id = m.fixture_id join people p on p.fm_uid = m.person_uid where f.competition_uid = :c "
                      "and f.date between :s and :u and m.played = 1 and (case m.side when 0 then f.home_uid else f.away_uid end) = :t "
                      "and m.rating is not null group by m.person_uid order by avg(m.rating) desc limit 4",
                      c=t["uid"], s=str(t["first"]), u=str(w.until), t=team) if played else []
        facts = {"tournament": label, "side": w.name(team), "manager": _manager(w, team),
                 "their_games_so_far": [f"{rounds.get(f['id'], '')}, {str(f['date'])[:10]}: {w.name(f['home_uid'])} {_score(f)} {w.name(f['away_uid'])}".strip(", ")
                                        for f in played] or None,
                 "their_group_now": _group_lines(w, t, {team}),
                 "their_best_players_so_far": [f"{r['name']}: {r['games']} games, {r['goals'] or 0} goals, {r['assists'] or 0} assists, "
                                               f"rated {r['rating']:.2f}" for r in best] or None,
                 "injured_in_the_squad": [f"{squad[r['person_uid']]['name']}: {r['injury']}, about {r['days']} days" for r in injured] or None,
                 "their_next_games": [f"{rounds.get(f['id'], '')}, {str(f['date'])[:10]}: {w.name(f['home_uid'])} v {w.name(f['away_uid'])}".strip(", ")
                                      for f in next_games] or None,
                 "the_reporter": CAMP_NOTE}
        people = [f"person:{r['fm_uid']}" for r in best[:3]] + [f"person:{r['person_uid']}" for r in injured[:1]]  # their characters, for the quotes
        leads.append(Lead("tournament_camp", f"{label}: in the {w.name(team)} camp", w.until, weight * 10 * CAMP_SIZE,
                          {k: v for k, v in facts.items() if v is not None}, [f"club:{team}", f"competition:{t['uid']}", *people],
                          {f"nation:{nation}"}, slug=f"{t['uid']}:{t['year']}:{team}:{w.until}"))
    return leads


def _stadiums(w, fixture_ids: list[int]) -> dict[int, str]:
    if not fixture_ids:
        return {}
    return {r["fixture_id"]: r["stadium"] for r in w.rows(f"select fixture_id, stadium from match_reports where fixture_id in ({','.join(map(str, fixture_ids))}) "
                                                          "and stadium is not null")}


def _crowd(w, f: dict, ground: str | None, team: int | None = None) -> str:
    words = f"{str(f['date'])[:10]}: {w.name(f['home_uid'])} {f['home_goals']}-{f['away_goals']} {w.name(f['away_uid'])}"
    words += f" at {ground}" if ground else ""
    if f["attendance"]:
        words += f", a crowd of {f['attendance']:,}"
        if f["away_fans"] and (team is None or team == f["away_uid"]):
            words += f", {f['away_fans']:,} of them {w.name(f['away_uid'])} fans"
    return words


def fans(w, t: dict, label: str, weight: float, Lead) -> list:
    """The fans on the road with each side that played this cycle (for its own country's paper), and the cycle's crowds."""
    played = w.rows("select * from fixtures where competition_uid = :c and home_goals is not null and date > :s and date <= :u order by date, id",
                    c=t["uid"], s=str(w.since), u=str(w.until))
    if not played:
        return []
    grounds = _stadiums(w, [f["id"] for f in played])
    leads = []
    for team in sorted({x for f in played for x in (f["home_uid"], f["away_uid"])}):
        nation = w.team(team).get("nation_uid")
        theirs = [f for f in played if team in (f["home_uid"], f["away_uid"])]
        if not nation or not any(f["attendance"] for f in theirs):
            continue
        facts = {"tournament": label, "side": w.name(team), "their_games_this_cycle": [_crowd(w, f, grounds.get(f["id"]), team) for f in theirs],
                 "about_the_crowds": ("A side listed second played as the away side: its travelling support is counted. For the "
                                      "side listed first only the whole crowd is known."), "the_piece": FANS_NOTE}
        leads.append(Lead("tournament_fans", f"{label}: on the road with the {w.name(team)} fans", w.until, weight * 10 * FANS_SIZE, facts,
                          [f"club:{team}", f"competition:{t['uid']}"], {f"nation:{nation}"}, slug=f"{t['uid']}:{t['year']}:{team}:{w.until}"))
    biggest = sorted((f for f in played if f["attendance"]), key=lambda f: -f["attendance"])[:CROWDS]
    if biggest:
        travelling = max((f for f in played if f["away_fans"]), key=lambda f: f["away_fans"], default=None)
        facts = {"tournament": label, "the_biggest_crowds": [_crowd(w, f, grounds.get(f["id"])) for f in biggest],
                 "the_biggest_travelling_support": _crowd(w, travelling, grounds.get(travelling["id"])) if travelling else None,
                 "the_piece": FANS_NOTE.replace("this side", "the sides at the tournament")}
        leads.append(Lead("tournament_crowds", f"{label}: the crowds and the travelling fans", w.until, weight * 10 * CROWDS_SIZE,
                          {k: v for k, v in facts.items() if v is not None}, [f"competition:{t['uid']}"], {"world"},
                          slug=f"{t['uid']}:{t['year']}:{w.until}"))
    return leads


def _age(born, on: date) -> int | None:
    if not born:
        return None
    born = date.fromisoformat(str(born)[:10])
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def team_guides(w, t: dict, label: str, weight: float, Lead, groups: list[tuple[str, list[dict]]], squads: dict[int, list[dict]],
                standing, ours) -> list:
    """Before the first game, a guide to each side (21 Nov 2026: "a team guide before a tournament"): where its squad ranks,
    its best-known players and one young one to watch, the manager, its form coming in, its record at this tournament,
    its group and games, and the followed clubs' players in it. For its own country's paper and the tournament's."""
    from app.services.story_leads_now import _manager
    from app.services.tournament_leads import _score

    leads = []
    for name, games in groups:
        members = sorted({x for f in games for x in (f["home_uid"], f["away_uid"])})
        for team in members:
            squad = squads.get(team) or []
            if not squad:
                continue
            nation = w.team(team).get("nation_uid")
            stars = sorted((p for p in squad if p["reputation_world"]), key=lambda p: -p["reputation_world"])[:GUIDE_STARS]
            born = {r["fm_uid"]: r["birth_date"] for r in w.rows(
                f"select fm_uid, birth_date from people where fm_uid in ({','.join(str(p['fm_uid']) for p in squad)})")}
            star_ids = {p["fm_uid"] for p in stars}
            young = max((p for p in squad if p["fm_uid"] not in star_ids and p.get("current_ability")
                         and (_age(born.get(p["fm_uid"]), t["first"]) or 99) <= YOUNG), key=lambda p: p["current_ability"], default=None)
            form = w.rows("select * from fixtures where (home_uid = :t or away_uid = :t) and home_goals is not null and date < :d "
                          "order by date desc limit :n", t=team, d=str(t["first"]), n=FORM_GAMES)
            record = w.rows("select year, place from edition_places where competition_uid = :c and team_uid = :t and place <= 2 "
                            "and year < :y order by year", c=t["uid"], t=team, y=t["year"])
            titles = [r["year"] for r in record if r["place"] == 1]
            facts = {"tournament": label, "side": w.name(team), "first_game_of_the_tournament": str(t["first"]),
                     "where_they_stand": standing(team), "manager": _manager(w, team),
                     "best_known_players": [f"{p['name']} ({w.name(p['team_uid'])})" for p in stars] or None,
                     "one_to_watch": (f"{young['name']} ({w.name(young['team_uid'])}), {_age(born.get(young['fm_uid']), t['first'])}, "
                                      "one of the best young players in the squad") if young else None,
                     "form_coming_in": [f"{str(f['date'])[:10]}: {w.name(f['home_uid'])} {_score(f)} {w.name(f['away_uid'])}"
                                        f" ({w.comp(f['competition_uid']).get('name') or 'friendly'})" for f in form] or None,
                     "their_record_at_this_tournament": "; ".join(part for part in (
                         f"winners in {', '.join(map(str, titles))}" if titles else "",
                         f"runners-up in {', '.join(str(r['year']) for r in record if r['place'] == 2)}" if any(r["place"] == 2 for r in record) else "")
                         if part) or None,
                     "their_group": name, "their_group_games": [f"{str(f['date'])[:10]}: {w.name(f['home_uid'])} v {w.name(f['away_uid'])}"
                                                                for f in games if team in (f["home_uid"], f["away_uid"])],
                     "our_clubs_players_in_the_squad": ours([team]) or None}
            people = [f"person:{p['fm_uid']}" for p in stars[:3]] + ([f"person:{young['fm_uid']}"] if young else [])
            leads.append(Lead("tournament_team_guide", f"{label} team guide: {w.name(team)}", w.until, weight * 10 * GUIDE_SIZE,
                              {k: v for k, v in facts.items() if v is not None}, [f"club:{team}", f"competition:{t['uid']}", *people],
                              {f"nation:{nation}"} if nation else set(), slug=f"{t['uid']}:{t['year']}:{team}"))
    return leads


def _knockout(w, t: dict) -> list[dict]:
    """The tournament's knockout rounds as the competition page draws them: [{"name", "ties"}], each tie feeding the next."""
    rows = w.rows("select * from fixtures where competition_uid = :c and date between :s and :e order by date, id",
                  c=t["uid"], s=str(t["first"]), e=str(t["last"]))
    stages: dict[int, list[dict]] = defaultdict(list)
    for f in rows:
        stages[f["stage"]].append(f)
    rounds = q.round_names(sorted(stages.values(), key=lambda games: str(games[0]["date"])))
    for r in rounds:
        if r["name"].startswith("Group"):
            r["table"] = q.table_from(r["games"])
    return [column for column in q.edition_stages(rounds)["bracket"] if column["name"] != "Play-off round"]


def _tie_words(w, tie: dict) -> str:
    a, b = tie["teams"]
    if not tie["played"]:
        leg = tie["legs"][0]
        return f"{str(leg['date'])[:10]}: {w.name(leg['home_uid'])} v {w.name(leg['away_uid'])}"
    leg = tie["legs"][-1]
    words = f"{str(leg['date'])[:10]}: {w.name(leg['home_uid'])} {leg['home_goals']}-{leg['away_goals']} {w.name(leg['away_uid'])}"
    if tie["penalties"]:
        words += f", {w.name(tie['winner'])} through on penalties ({max(tie['penalties'])}-{min(tie['penalties'])})"
    elif tie["winner"]:
        words += f", {w.name(tie['winner'])} through"
    return words


def road_to_the_final(w, t: dict, label: str, weight: float, Lead) -> list:
    """Once the knockout rounds are on, the road to the final (21 Nov 2026: "the knockout and bracket stories"): each knockout
    round with its ties, played and to come; the sides still in it and the path each took. Who meets whom after the ties
    to come is not guessed: the game sets those fixtures later. Once per cycle while a knockout game was played this cycle or is to come."""
    columns = _knockout(w, t)
    ties = [tie for column in columns for tie in column["ties"] if tie]
    if not ties:
        return []
    window = (str(w.since), str(w.until + timedelta(days=14)))
    if not any(window[0] < str(leg["date"])[:10] <= window[1] for tie in ties for leg in tie["legs"]):
        return []
    out = {team for tie in ties if tie["winner"] for team in tie["teams"] if team != tie["winner"]}
    alive = sorted({team for tie in ties for team in tie["teams"]} - out, key=lambda x: -(w.rep(x) or 0))
    paths = {team: [f"{column['name']}: {_tie_words(w, tie)}" for column in columns for tie in column["ties"]
                     if tie and tie["played"] and team in tie["teams"]] for team in alive}
    facts = {"tournament": label,
             "the_knockout_rounds": [f"{column['name']}: " + "; ".join(_tie_words(w, tie) for tie in column["ties"] if tie)
                                     for column in columns if any(column["ties"])],
             "still_in_it": [w.name(x) for x in alive],
             "the_paths_so_far": [f"{w.name(team)}: " + " | ".join(path) for team, path in paths.items() if path] or None,
             "out_in_the_knockout_rounds": [w.name(x) for x in sorted(out, key=lambda x: -(w.rep(x) or 0))] or None}
    if columns[-1]["name"] != "Final":
        facts["about_the_draw"] = ("Who meets whom after the ties to come is not set yet: name a possible meeting only as a possibility "
                                   "the next ties will decide.")
    return [Lead("tournament_bracket", f"{label}: the road to the final", w.until, weight * 10 * BRACKET_SIZE,
                 {k: v for k, v in facts.items() if v is not None}, [f"competition:{t['uid']}"] + [f"club:{x}" for x in alive[:4]],
                 {"world"}, slug=f"{t['uid']}:{t['year']}:{w.until}")]
