"""The coverage of a major international tournament in play: the World Cup, the European Championship, the Copa América
and the other continental finals. While one is on (from a week before its first game to three days after its last), every
game of the cycle gets a report lead and every game of the next two weeks a preview, the cycle gets a round-up, the
followed clubs' players there get their own story, and the final brings the player of the tournament. The match
notebooks carry the game's full report while the save keeps it and the followed clubs' players' own rows after.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from app.services import world_queries as q

MAJOR_TOURNAMENTS = ("World Cup", "European Football Championship", "Copa América", "Africa Cup of Nations", "Asian Cup", "Gold Cup",
                     "Confederations Cup", "Nations League Finals")
LEAD_DAYS, TAIL_DAYS = 14, 3  # in play from two weeks before the first game (18 Nov 2026: "like in real life") to three days after the last
PREVIEW_DAYS = 14
REPORT_SIZE, PREVIEW_SIZE = 1.0, 0.9
OURS_BONUS, KNOCKOUT_BONUS, FINAL_BONUS = 0.5, 0.5, 1.0
ROUND_UP_SIZE, OURS_SIZE, BEST_SIZE = 2.0, 1.6, 2.5
BEST_GAMES = 3  # games played before a player is in the running for player of the tournament
TOP_PLAYERS = 8
# The build-up (18 Nov 2026: "coverage of the groups, groups of death, people missing out on squads"): before the first game,
# a preview of each group and one of the whole field. The group of death is the group whose third-best side is the strongest,
# when that side is in the top half of the field.
GROUP_SIZE, DEATH_BONUS, FIELD_SIZE = 1.3, 0.6, 2.2
STARS = 10  # the best-known players named in the field's preview
BRACKET_EDITIONS, BRACKET_SEEN = 3, 2  # a first-knockout-round pairing the game used in this many of its last editions is "the bracket"
TOURNAMENT_BEAT = "tournament"  # the beat of the tournament's own paper (outlets.tournament_paper)


def major_tournament(w) -> dict | None:
    """The tournament in play at the cycle's end: its competition, its first and last game, its phase."""
    today = w.until
    since, until = str(today - timedelta(days=TAIL_DAYS + 60)), str(today + timedelta(days=LEAD_DAYS + 60))
    found = []
    for uid, comp in w.competitions.items():
        name = comp.get("name") or ""
        if comp.get("scope") != "national_teams" or "Qualif" in name or name.startswith(("Under", "U1", "U2")) or "Youth" in name:
            continue
        if not any(name == m or name.startswith(m) for m in MAJOR_TOURNAMENTS):
            continue
        span = w.rows("select min(date) first, max(date) last, sum(home_goals is not null) played, count(*) games from fixtures "
                      "where competition_uid = :c and date between :s and :e", c=uid, s=since, e=until)
        if not span or not span[0]["first"]:
            continue
        span = span[0]
        first, last = (date.fromisoformat(str(span[k])[:10]) for k in ("first", "last"))
        if not (first - timedelta(days=LEAD_DAYS) <= today <= last + timedelta(days=TAIL_DAYS)):
            continue
        tier = 0 if name == "World Cup" else 1 if name == "European Football Championship" else 2
        found.append({"uid": uid, "name": name, "year": first.year, "first": first, "last": last, "played": span["played"], "games": span["games"],
                      "phase": "about to start" if today < first else "over" if today > last else "in play", "tier": (tier, -(comp.get("reputation") or 0))})
    return min(found, key=lambda t: t["tier"]) if found else None


def _rounds(w, t: dict) -> dict[int, str]:
    """Each fixture's round name ("Group A", "Quarter-final", "Final") from the tournament's stages."""
    rows = w.rows("select * from fixtures where competition_uid = :c and date between :s and :e order by date, id",
                  c=t["uid"], s=str(t["first"]), e=str(t["last"]))
    stages: dict[int, list[dict]] = defaultdict(list)
    for f in rows:
        stages[f["stage"]].append(f)
    names = {}
    for r in q.round_names(sorted(stages.values(), key=lambda games: str(games[0]["date"]))):
        for f in r["games"]:
            names[f["id"]] = r.get("name") or "Round"
    return names


def _group_lines(w, t: dict, teams: set[int]) -> list[str] | None:
    """The groups these teams are in, as they stand: "Group A: Spain 6, Italy 4, Albania 1, Croatia 0"."""
    rows = w.rows("select * from fixtures where competition_uid = :c and date between :s and :e order by date, id",
                  c=t["uid"], s=str(t["first"]), e=str(t["last"]))
    stages: dict[int, list[dict]] = defaultdict(list)
    for f in rows:
        stages[f["stage"]].append(f)
    lines = []
    for r in q.round_names(sorted(stages.values(), key=lambda games: str(games[0]["date"]))):
        name = r.get("name") or ""
        if not name.startswith("Group") or not ({x for f in r["games"] for x in (f["home_uid"], f["away_uid"])} & teams):
            continue
        table = q.table_from(r["games"])
        lines.append(f"{name}: " + ", ".join(f"{w.name(row['team_uid'])} {row['points']}" for row in table))
    return lines or None


def _our_players(w, t: dict, fixture_ids: list[int] | None = None, teams: set[int] | None = None) -> list[dict]:
    """The followed clubs' players at the tournament, with their games (in the given fixtures, or all): their nation,
    games, minutes, goals, assists and average rating."""
    clubs = ",".join(str(u) for u in w.followed_clubs) or "0"
    people = {r["fm_uid"]: r for r in w.rows(f"select p.fm_uid, p.name, p.team_uid club_uid from people p join teams c on c.fm_uid = p.team_uid "
                                             f"where c.club_uid in ({clubs})")}
    if not people:
        return []
    ids = ",".join(str(u) for u in people)
    where = f" and f.id in ({','.join(str(i) for i in fixture_ids)})" if fixture_ids else ""
    rows = w.rows(f"select g.person_uid, g.team_uid, g.fixture_id, g.status, g.minutes, g.rating, g.goals, g.assists from player_games g "
                  f"join fixtures f on f.id = g.fixture_id where f.competition_uid = :c and f.date between :s and :e and g.person_uid in ({ids}){where}",
                  c=t["uid"], s=str(t["first"]), e=str(t["last"]))
    seen = {(r["person_uid"], r["fixture_id"]) for r in rows}
    for r in w.rows(f"select m.person_uid, case m.side when 0 then f.home_uid else f.away_uid end team_uid, m.fixture_id, m.played, m.minute_on, "
                    f"m.minute_off, m.rating, m.goals, m.assists from match_players m join fixtures f on f.id = m.fixture_id "
                    f"where f.competition_uid = :c and f.date between :s and :e and m.person_uid in ({ids}){where}",
                    c=t["uid"], s=str(t["first"]), e=str(t["last"])):
        if (r["person_uid"], r["fixture_id"]) in seen:
            continue
        rows.append({"person_uid": r["person_uid"], "team_uid": r["team_uid"], "fixture_id": r["fixture_id"], "status": "played" if r["played"] else "unused",
                     "minutes": ((r["minute_off"] or 90) - (r["minute_on"] or 0)) if r["played"] else None, "rating": r["rating"], "goals": r["goals"],
                     "assists": r["assists"]})
    by: dict[int, dict] = {}
    for r in rows:
        if teams is not None and r["team_uid"] not in teams:
            continue
        p = by.setdefault(r["person_uid"], {"person_uid": r["person_uid"], "name": people[r["person_uid"]]["name"], "club": w.name(people[r["person_uid"]]["club_uid"]),
                                            "nation": w.name(r["team_uid"]), "team_uid": r["team_uid"], "games": 0, "squad": 0, "minutes": 0, "goals": 0,
                                            "assists": 0, "ratings": []})
        p["squad"] += 1
        if r["status"] == "played":
            p["games"] += 1
            p["minutes"] += r["minutes"] or 0
            p["goals"] += r["goals"] or 0
            p["assists"] += r["assists"] or 0
            if r["rating"]:
                p["ratings"].append(r["rating"])
    out = []
    for p in by.values():
        p["rating"] = round(sum(p["ratings"]) / len(p["ratings"]), 2) if p["ratings"] else None
        del p["ratings"]
        out.append(p)
    return sorted(out, key=lambda p: (-p["minutes"], -p["squad"], p["name"]))


def _player_words(p: dict) -> str:
    if not p["games"]:
        return f"{p['name']} ({p['club']}, {p['nation']}): in the squad for {p['squad']} games, not used"
    bits = [f"{p['games']} games" if p["games"] != 1 else "1 game", f"{p['minutes']} minutes"]
    if p["goals"]:
        bits.append(f"{p['goals']} goal{'s' if p['goals'] > 1 else ''}")
    if p["assists"]:
        bits.append(f"{p['assists']} assist{'s' if p['assists'] > 1 else ''}")
    if p["rating"]:
        bits.append(f"rated {p['rating']:.2f}")
    return f"{p['name']} ({p['club']}, {p['nation']}): " + ", ".join(bits)


def _score(f: dict) -> str:
    full = f["home_goals_after_extra_time"] is not None
    score = f"{f['home_goals_after_extra_time']}-{f['away_goals_after_extra_time']} after extra time" if full else f"{f['home_goals']}-{f['away_goals']}"
    if f["home_penalties"] is not None:
        score += f", {f['home_penalties']}-{f['away_penalties']} on penalties"
    return score


def _results(w, t: dict, games: list[dict], rounds: dict[int, str]) -> list[str]:
    return [f"{rounds.get(f['id'], '')}: {w.name(f['home_uid'])} {_score(f)} {w.name(f['away_uid'])}".strip(": ") for f in games]


def _scorers(w, t: dict, limit: int = 8) -> list[str]:
    rows = w.rows("select p.name, count(*) goals, max(case g.side when 0 then f.home_uid else f.away_uid end) team from goals g "
                  "join fixtures f on f.id = g.fixture_id join people p on p.fm_uid = g.scorer_uid where f.competition_uid = :c "
                  "and f.date between :s and :e and g.kind in (0, 2) group by g.scorer_uid order by goals desc limit :n",
                  c=t["uid"], s=str(t["first"]), e=str(t["last"]), n=limit)
    return [f"{r['name']} ({w.name(r['team'])}) {r['goals']}" for r in rows]


def tournament_coverage(w) -> list:
    """Every lead of a major tournament in play this cycle."""
    from app.services.story_leads import Lead
    from app.services.story_leads_now import _how_goals_went_in

    t = major_tournament(w)
    if t is None:
        return []
    rounds = _rounds(w, t)
    played = w.rows("select * from fixtures where competition_uid = :c and home_goals is not null and date > :s and date <= :u order by date, id",
                    c=t["uid"], s=str(w.since), u=str(w.until))
    coming = w.rows("select * from fixtures where competition_uid = :c and home_goals is null and date > :u and date <= :e order by date, id",
                    c=t["uid"], u=str(w.until), e=str(w.until + timedelta(days=PREVIEW_DAYS)))
    ours_all = _our_players(w, t)
    ours_by_team: dict[int, list[dict]] = defaultdict(list)
    for p in ours_all:
        ours_by_team[p["team_uid"]].append(p)
    weight = w.weight(t["uid"]) if w.comp(t["uid"]).get("reputation") else 4.0
    label = f"{t['name']} {t['year']}"
    leads = []

    def size_of(f: dict, base: float) -> float:
        name = rounds.get(f["id"], "")
        size = base + (OURS_BONUS if ours_by_team.get(f["home_uid"]) or ours_by_team.get(f["away_uid"]) else 0)
        size += FINAL_BONUS if name == "Final" else KNOCKOUT_BONUS if name and not name.startswith("Group") else 0
        return size

    for f in played:  # a report of every game
        in_it = _our_players(w, t, [f["id"]])
        best = w.rows("select p.name from match_reports r join people p on p.fm_uid = r.player_of_the_match_uid where r.fixture_id = :f", f=f["id"])
        goals = w.rows("select g.minute, g.added_time, g.side, g.kind, p.name from goals g left join people p on p.fm_uid = g.scorer_uid "
                       "where g.fixture_id = :f and g.kind in (0, 1, 2) order by g.minute, g.seq", f=f["id"])
        facts = {"tournament": label, "round": rounds.get(f["id"]), "date": str(f["date"]), "home": w.name(f["home_uid"]), "away": w.name(f["away_uid"]),
                 "score": _score(f),
                 "goals": [f"{('own goal by ' if g['kind'] == 1 else '') + (g['name'] or 'unknown')} {g['minute']}{'+' + str(g['added_time']) if g['added_time'] else ''}'{' (pen)' if g['kind'] == 2 else ''} "
                           f"({w.name(f['home_uid'] if g['side'] == 0 else f['away_uid'])})" for g in goals] or None,
                 "how_the_goals_went_in": _how_goals_went_in(w, f),
                 "player_of_the_match": best[0]["name"] if best else None,
                 "our_players_in_it": [_player_words(p) for p in in_it] or None,
                 "groups_now": _group_lines(w, t, {f["home_uid"], f["away_uid"]}) if rounds.get(f["id"], "").startswith("Group") else None}
        leads.append(Lead("tournament_report", f"{label}, {rounds.get(f['id'], 'round')}: {w.name(f['home_uid'])} {_score(f)} {w.name(f['away_uid'])}",
                          date.fromisoformat(str(f["date"])[:10]), weight * 10 * size_of(f, REPORT_SIZE), {k: v for k, v in facts.items() if v is not None},
                          [f"match:{f['date']}:{f['home_uid']}:{f['away_uid']}", f"competition:{t['uid']}", f"club:{f['home_uid']}", f"club:{f['away_uid']}"],
                          w.beats_for(f["home_uid"], f["away_uid"], competition_uid=t["uid"]) | {"world"} | ({"home"} if in_it else set())))
    for f in coming:  # a preview of every game to come
        teams = {f["home_uid"], f["away_uid"]}
        so_far = w.rows("select * from fixtures where competition_uid = :c and home_goals is not null and date between :s and :u "
                        "and (home_uid in (:h, :a) or away_uid in (:h, :a)) order by date", c=t["uid"], s=str(t["first"]), u=str(w.until),
                        h=f["home_uid"], a=f["away_uid"])
        meetings = w.rows("select * from fixtures where ((home_uid = :h and away_uid = :a) or (home_uid = :a and away_uid = :h)) "
                          "and home_goals is not null and date < :d order by date desc limit 3", h=f["home_uid"], a=f["away_uid"], d=str(f["date"]))
        facts = {"tournament": label, "round": rounds.get(f["id"]), "date": str(f["date"]), "home": w.name(f["home_uid"]), "away": w.name(f["away_uid"]),
                 "so_far_in_the_tournament": _results(w, t, so_far, rounds) or None,
                 "groups_now": _group_lines(w, t, teams) if rounds.get(f["id"], "").startswith("Group") else None,
                 "our_players_in_the_squads": [_player_words(p) for p in ours_all if p["team_uid"] in teams] or None,
                 "last_meetings": [f"{m['date']}: {w.name(m['home_uid'])} {_score(m)} {w.name(m['away_uid'])}" for m in meetings] or None}
        leads.append(Lead("tournament_preview", f"{label}, {rounds.get(f['id'], 'round')} to come: {w.name(f['home_uid'])} v {w.name(f['away_uid'])}, {f['date']}",
                          w.until, weight * 10 * size_of(f, PREVIEW_SIZE), {k: v for k, v in facts.items() if v is not None},
                          [f"match:{f['date']}:{f['home_uid']}:{f['away_uid']}", f"competition:{t['uid']}", f"club:{f['home_uid']}", f"club:{f['away_uid']}"],
                          w.beats_for(f["home_uid"], f["away_uid"], competition_uid=t["uid"]) | {"world"}
                          | ({"home"} if facts.get("our_players_in_the_squads") else set())))
    if t["phase"] == "about to start":
        leads += build_up(w, t, weight, label)
    if played or (coming and t["phase"] != "about to start"):  # the cycle's round-up (before the start, the field's preview is it)
        teams = {x for f in played for x in (f["home_uid"], f["away_uid"])}
        facts = {"tournament": label, "where_it_stands": f"{t['played']} of {t['games']} games played, {t['phase']}",
                 "results_this_cycle": _results(w, t, played, rounds) or None, "groups_now": _group_lines(w, t, teams) if played else None,
                 "top_scorers": _scorers(w, t) or None,
                 "next_games": [f"{rounds.get(f['id'], '')}, {f['date']}: {w.name(f['home_uid'])} v {w.name(f['away_uid'])}" for f in coming[:12]] or None,
                 "our_players_so_far": [_player_words(p) for p in ours_all] or None}
        leads.append(Lead("tournament_round_up", f"{label}: {len(played)} games this cycle, {t['played']} of {t['games']} played", w.until,
                          weight * 10 * ROUND_UP_SIZE, {k: v for k, v in facts.items() if v is not None}, [f"competition:{t['uid']}"],
                          {"world"} | {f"nation:{n}" for n in {w.team(x).get("nation_uid") for x in teams} if n}, slug=f"{t['uid']}:{w.until}"))
    cycle_ours = _our_players(w, t, [f["id"] for f in played]) if played else []
    if cycle_ours:  # the followed clubs' players this cycle
        facts = {"tournament": label, "this_cycle": [_player_words(p) for p in cycle_ours], "so_far": [_player_words(p) for p in ours_all],
                 "next_games_of_their_nations": [f"{rounds.get(f['id'], '')}, {f['date']}: {w.name(f['home_uid'])} v {w.name(f['away_uid'])}" for f in coming
                                                 if ours_by_team.get(f["home_uid"]) or ours_by_team.get(f["away_uid"])][:8] or None}
        clubs = sorted({p["club"] for p in cycle_ours})
        leads.append(Lead("our_players_at_tournament", f"{', '.join(clubs)} at the {label}: {len(cycle_ours)} players this cycle", w.until,
                          weight * 10 * OURS_SIZE, {k: v for k, v in facts.items() if v is not None},
                          [f"competition:{t['uid']}"] + [f"person:{p['person_uid']}" for p in cycle_ours[:6]], {"world", "home"}, slug=f"{t['uid']}:{w.until}"))
    final = next((f for f in played if rounds.get(f["id"]) == "Final"), None)
    if final:  # the player of the tournament, once
        best = w.rows("select p.name, max(case m.side when 0 then f.home_uid else f.away_uid end) team, count(*) games, avg(m.rating) rating, "
                      "sum(m.goals) goals, sum(m.assists) assists from match_players m join fixtures f on f.id = m.fixture_id "
                      "join people p on p.fm_uid = m.person_uid where f.competition_uid = :c and f.date between :s and :e and m.played = 1 "
                      "and m.rating is not null group by m.person_uid having count(*) >= :g order by rating desc limit :n",
                      c=t["uid"], s=str(t["first"]), e=str(t["last"]), g=BEST_GAMES, n=TOP_PLAYERS)
        winner = final["home_uid"] if q.result_for(final, final["home_uid"])[2] == "W" else final["away_uid"]
        facts = {"tournament": label, "final": f"{w.name(final['home_uid'])} {_score(final)} {w.name(final['away_uid'])}", "winners": w.name(winner),
                 "best_rated": [f"{r['name']} ({w.name(r['team'])}): {r['games']} games, rated {r['rating']:.2f}, {r['goals']} goals, {r['assists']} assists" for r in best] or None,
                 "top_scorers": _scorers(w, t) or None, "our_players": [_player_words(p) for p in ours_all] or None}
        leads.append(Lead("player_of_the_tournament", f"The {label}: the players who made it", date.fromisoformat(str(final["date"])[:10]),
                          weight * 10 * BEST_SIZE, {k: v for k, v in facts.items() if v is not None}, [f"competition:{t['uid']}", f"club:{winner}"],
                          {"world"}, slug=f"{t['uid']}:{t['year']}"))
    for lead in leads:
        lead.beats.add(TOURNAMENT_BEAT)
    return leads


def _groups(w, t: dict) -> list[tuple[str, list[dict]]]:
    """The tournament's groups, each with its games: [("Group A", games), ...]."""
    rows = w.rows("select * from fixtures where competition_uid = :c and date between :s and :e order by date, id",
                  c=t["uid"], s=str(t["first"]), e=str(t["last"]))
    stages: dict[int, list[dict]] = defaultdict(list)
    for f in rows:
        stages[f["stage"]].append(f)
    return [(r["name"], r["games"]) for r in q.round_names(sorted(stages.values(), key=lambda games: str(games[0]["date"])))
            if (r.get("name") or "").startswith("Group")]


def _slot(place: int, group: str) -> str:
    return {1: f"the winner of {group}", 2: f"the runner-up of {group}"}.get(place, "a third-placed side")


def past_bracket(w, t: dict, groups: list[tuple[str, list[dict]]], order=None) -> list[str] | None:
    """Who meets whom after the groups, read from the tournament's last editions (18 Nov 2026: "they can examine the bracket and
    they'll know who they could potentially face"): each pairing of the first knockout round, by group place, that the game used
    in at least BRACKET_SEEN of its last BRACKET_EDITIONS editions, with the sides that can fill each place now."""
    now = {name: sorted({x for f in games for x in (f["home_uid"], f["away_uid"])}, key=order or (lambda x: -(w.rep(x) or 0))) for name, games in groups}
    seen, rounds_named, editions = Counter(), Counter(), 0
    for e in w.rows("select year, decided_on from competition_editions where competition_uid = :c and year < :y and decided_on is not null "
                    "order by year desc limit :n", c=t["uid"], y=t["year"], n=BRACKET_EDITIONS):
        end = date.fromisoformat(str(e["decided_on"])[:10])
        rows = w.rows("select * from fixtures where competition_uid = :c and date between :s and :e and home_goals is not null order by date, id",
                      c=t["uid"], s=str(end - timedelta(days=60)), e=str(end))
        stages: dict[int, list[dict]] = defaultdict(list)
        for f in rows:
            stages[f["stage"]].append(f)
        rounds = q.round_names(sorted(stages.values(), key=lambda games: str(games[0]["date"])))
        past_groups = [r for r in rounds if (r.get("name") or "").startswith("Group")]
        knockout = next((r for r in rounds if r not in past_groups), None)
        if len(past_groups) != len(groups) or knockout is None:
            continue  # another format then
        by_stage = {games[0]["stage"]: name for name, games in groups}  # the game keeps a group's stage from edition to edition;
        names = [by_stage.get(r["games"][0]["stage"], name) for (name, _), r in zip(groups, past_groups)]  # two starting the same day swap
        place = {row["team_uid"]: (pos, name) for name, r in zip(names, past_groups) for pos, row in enumerate(q.table_from(r["games"]), 1)}
        editions += 1
        rounds_named[knockout.get("name") or "the first knockout round"] += 1
        for pair in {frozenset((f["home_uid"], f["away_uid"])) for f in knockout["games"]}:  # a second leg once
            a, b = sorted((place.get(x) for x in pair), key=lambda p: p or (9, ""))
            if a and b and a[0] <= 2:
                seen[(a, b if b[0] <= 2 else (3, ""))] += 1
    if editions < BRACKET_SEEN:
        return None
    named = rounds_named.most_common(1)[0][0]

    def who(slot) -> str:
        pos, group = slot
        return f"{_slot(pos, group)} ({' or '.join(w.name(x) for x in now[group])})" if group in now else _slot(pos, group)

    return [f"{named}: {who(a)} against {who(b)}, as in {n} of the last {editions} editions" for (a, b), n in sorted(seen.items()) if n >= BRACKET_SEEN] or None


SQUAD_CORE = 11  # a squad's strength: the mean current ability of its best eleven
SQUAD_CLASSES = ((180, "a monster of a squad, world class all through"), (168, "a very strong squad"), (156, "a strong squad"),
                 (145, "a decent squad"), (0, "a modest squad"))  # what a journalist would say of it, never the numbers (18 Nov 2026)


def squad_strength(squad: list[dict]) -> float | None:
    """The mean current ability of the squad's best SQUAD_CORE players; None when fewer of them have one."""
    abilities = sorted((p["current_ability"] for p in squad if p.get("current_ability")), reverse=True)[:SQUAD_CORE]
    return sum(abilities) / len(abilities) if len(abilities) >= SQUAD_CORE else None


def squad_class(strength: float | None) -> str | None:
    return next(words for floor, words in SQUAD_CLASSES if strength >= floor) if strength is not None else None


def _squad(w, team: int) -> list[dict]:
    """The team's latest squad: each player's name, club, world reputation and current ability."""
    return w.rows("select p.fm_uid, p.name, p.team_uid, p.reputation_world, p.current_ability from national_squads n join people p on p.fm_uid = n.person_uid "
                  "where n.team_uid = :t and n.as_of = (select max(as_of) from national_squads where team_uid = :t)", t=team)


def build_up(w, t: dict, weight: float, label: str) -> list:
    """Before the first game: a preview of each group (its sides by stature, the best-known players, our clubs' players in
    the squads, the games) and one of the whole field (the favourites, the holders, the stars)."""
    from app.services.story_leads import Lead

    groups = _groups(w, t)
    if not groups:
        return []
    teams = {x for _, games in groups for f in games for x in (f["home_uid"], f["away_uid"])}
    squads = {team: _squad(w, team) for team in teams}
    strength = {team: squad_strength(squads[team]) for team in teams}  # 18 Nov 2026: the squads called up, not the name, make the favourites
    by_name = {team: i + 1 for i, team in enumerate(sorted(teams, key=lambda x: -(w.rep(x) or 0)))}
    ranked = sorted(teams, key=lambda x: (strength[x] is None, -(strength[x] or 0), by_name[x]))
    rank = {team: i + 1 for i, team in enumerate(ranked)}
    followed = {r["fm_uid"] for r in w.rows(f"select fm_uid from teams where club_uid in ({','.join(str(u) for u in w.followed_clubs) or '0'})")}

    def side(team: int) -> str:
        stars = sorted((p for p in squads[team] if p["reputation_world"]), key=lambda p: -p["reputation_world"])[:3]
        words = squad_class(strength[team])
        return (f"{w.name(team)}: number {rank[team]} of {len(teams)} in the field on the squads called up"
                + (f" ({words})" if words else "") + f", number {by_name[team]} by reputation"
                + (f"; best known: {', '.join(p['name'] + ' (' + w.name(p['team_uid']) + ')' for p in stars)}" if stars else ""))

    def ours(members) -> list[str]:
        return [f"{p['name']} ({w.name(p['team_uid'])}, {w.name(team)})" for team in members for p in squads[team] if p["team_uid"] in followed]

    third = {name: sorted((rank[x] for x in {y for f in games for y in (f["home_uid"], f["away_uid"])}))[2:3] for name, games in groups}
    hardest = min((name for name in third if third[name]), key=lambda name: third[name][0], default=None)
    if hardest and third[hardest][0] > len(teams) / 2:
        hardest = None
    holders = w.rows("select p.team_uid from competition_editions e join edition_places p using (competition_uid, year) "
                     "where e.competition_uid = :c and p.place = 1 and e.year < :y order by e.year desc limit 1", c=t["uid"], y=t["year"])
    group_games = sum(len(games) for _, games in groups)
    bracket = past_bracket(w, t, groups, rank.get) if group_games >= t["games"] else None
    after = ("only the group games are set: who goes through is not known yet, and the knockout games have no dates; a possible "
             "opponent may be named only from the_likely_bracket, as a possibility") if group_games >= t["games"] else None  # 18 Nov 2026
    leads = []
    for name, games in groups:
        members = sorted({x for f in games for x in (f["home_uid"], f["away_uid"])}, key=lambda x: rank[x])
        death = name == hardest
        facts = {"tournament": label, "group": name, "first_game_of_the_tournament": str(t["first"]),
                 "the_sides_by_squad_strength": [side(x) for x in members],
                 "the_group_of_death": "the hardest group of the tournament: its third-best side is the strongest of any group's" if death else None,
                 "the_holders_are_in_it": w.name(holders[0]["team_uid"]) if holders and holders[0]["team_uid"] in members else None,
                 "our_clubs_players_in_the_squads": ours(members) or None,
                 "the_games": [f"{str(f['date'])[:10]}: {w.name(f['home_uid'])} v {w.name(f['away_uid'])}" for f in games],
                 "after_the_groups": after,
                 "the_likely_bracket": [line for line in bracket or [] if f"of {name} (" in line] or None}
        size = GROUP_SIZE + (DEATH_BONUS if death else 0) + (OURS_BONUS if facts["our_clubs_players_in_the_squads"] else 0)
        leads.append(Lead("tournament_group", f"{label}, {name}: {', '.join(w.name(x) for x in members)}" + (" (the group of death)" if death else ""),
                          w.until, weight * 10 * size, {k: v for k, v in facts.items() if v is not None},
                          [f"competition:{t['uid']}"] + [f"club:{x}" for x in members],
                          {"world"} | {f"nation:{n}" for n in (w.team(x).get("nation_uid") for x in members) if n}
                          | ({"home"} if facts["our_clubs_players_in_the_squads"] else set()), slug=f"{t['uid']}:{t['year']}:{name}"))
    stars = sorted((dict(p, nation=team) for team in teams for p in squads[team] if p["reputation_world"]), key=lambda p: -p["reputation_world"])[:STARS]
    facts = {"tournament": label, "first_game": str(t["first"]), "sides": len(teams),
             "the_favourites_by_squad_strength": [side(x) for x in ranked[:6]],
             "the_holders": w.name(holders[0]["team_uid"]) if holders else None,
             "the_groups": [f"{name}: {', '.join(w.name(x) for x in sorted({y for f in games for y in (f['home_uid'], f['away_uid'])}, key=lambda x: rank[x]))}"
                            for name, games in groups],
             "the_group_of_death": hardest,
             "the_best_known_players": [f"{p['name']} ({w.name(p['nation'])}, {w.name(p['team_uid'])})" for p in stars] or None,
             "our_clubs_players_in_the_squads": ours(ranked) or None, "after_the_groups": after,
             "the_likely_bracket": bracket}
    leads.append(Lead("tournament_field", f"{label}: the field, the favourites and the groups", w.until, weight * 10 * FIELD_SIZE,
                      {k: v for k, v in facts.items() if v is not None}, [f"competition:{t['uid']}"] + [f"club:{x}" for x in ranked[:4]],
                      {"world", "home"} if facts["our_clubs_players_in_the_squads"] else {"world"}, slug=f"{t['uid']}:{t['year']}"))
    return leads
