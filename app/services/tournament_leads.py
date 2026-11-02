"""The coverage of a major international tournament in play: the World Cup, the European Championship, the Copa América
and the other continental finals. While one is on (from a week before its first game to three days after its last), every
game of the cycle gets a report lead and every game of the next two weeks a preview, the cycle gets a round-up, the
followed clubs' players there get their own story, and the final brings the player of the tournament. The match
notebooks carry the game's full report while the save keeps it and the followed clubs' players' own rows after.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from app.services import world_queries as q

MAJOR_TOURNAMENTS = ("World Cup", "European Football Championship", "Copa América", "Africa Cup of Nations", "Asian Cup", "Gold Cup",
                     "Confederations Cup", "Nations League Finals")
LEAD_DAYS, TAIL_DAYS = 7, 3  # in play from a week before the first game to three days after the last
PREVIEW_DAYS = 14
REPORT_SIZE, PREVIEW_SIZE = 1.0, 0.9
OURS_BONUS, KNOCKOUT_BONUS, FINAL_BONUS = 0.5, 0.5, 1.0
ROUND_UP_SIZE, OURS_SIZE, BEST_SIZE = 2.0, 1.6, 2.5
BEST_GAMES = 3  # games played before a player is in the running for player of the tournament
TOP_PLAYERS = 8


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
    if played or coming:  # the cycle's round-up
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
    return leads
