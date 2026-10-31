"""Stories from the team sheets (tactics.py): who plays where, and what changed.

For the clubs of the covered leagues (story_leads_now.current_leagues) and the followed clubs, from their competitive
matches with a team sheet: a team leaving its usual system, a player moved to a new role and how he does there, a
regular dropped while fit (a star on the bench, a stalwart losing his place, often to a newcomer), a youngster breaking
through, a partnership that makes a team or costs it, and a league's tactical trends. story_leads.find_leads adds them.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from app.services import story_leads_now as now
from app.services import tactics
from app.services import world_queries as q

HISTORY_DAYS = 240  # the matches read before the cycle
BEFORE_GAMES = 5  # matches before the cycle that make a habit
USUAL_SHARE = 0.5  # of them in one shape: the usual system
NEW_SHAPE_GAMES = 2  # matches of the cycle in another shape
ROLE_STARTS_BEFORE = 5  # starts in one role before a move is news
ROLE_STARTS_NOW = 2  # ... and starts in the new role in the cycle
STALWART = 0.7  # share of the starts before the cycle that makes a stalwart
YOUNG = 20
BREAKTHROUGH_STARTS = 2  # starts in the cycle, after at most one before
PARTNERS_SEASON = 6  # games together this season before a partnership is a story
TREND_SIDES = 60  # team sheets in a league's season before its trends are news
TREND_SHAPE_SIDES = 10  # ... and of one shape before its points count
# The roles a move between is a story: a striker made a winger, a full-back made a centre-back, a midfielder moved
# out wide. A holding midfielder moved up one line when his team changes system is not one.
ROLES = {"GK": "goalkeeper", "D (C)": "centre-back", "SW": "centre-back", "D (R)": "full-back", "D (L)": "full-back",
         "WB (R)": "full-back", "WB (L)": "full-back", "DM": "central midfielder", "M (C)": "central midfielder",
         "AM (C)": "central midfielder", "M (R)": "wide player", "M (L)": "wide player", "AM (R)": "wide player",
         "AM (L)": "wide player", "ST (C)": "striker"}


def _sheets(w) -> dict[int, list[dict]]:
    """Each covered team's competitive matches with a team sheet, oldest first: date, shape, slots and ratings of the
    starters, the substitutes used and unused, the result."""
    if getattr(w, "_sheets", None) is not None:
        return w._sheets
    leagues = now.current_leagues(w)
    teams = set(w.followed_teams)
    for league in leagues:
        start, end = now._window(w, league)
        teams |= {r["team_uid"] for r in q.league_table(w.c, league, start, end)}
    marks = ",".join(str(int(t)) for t in teams) or "0"
    fixtures = w.rows(f"select f.* from fixtures f join competitions c on c.fm_uid = f.competition_uid where c.scope in "
                      f"('league', 'cup', 'continental') and f.home_goals is not null and f.date > :s and f.date <= :u "
                      f"and (f.home_uid in ({marks}) or f.away_uid in ({marks})) and exists (select 1 from match_players m "
                      f"where m.fixture_id = f.id and m.start_position is not null) order by f.date",
                      s=str(w.until - timedelta(days=HISTORY_DAYS)), u=str(w.until))
    rows = defaultdict(list)
    ids = ",".join(str(f["id"]) for f in fixtures) or "0"
    for r in w.rows(f"select fixture_id, side, person_uid, played, rating, start_position, end_position, minute_on, goals, assists "
                    f"from match_players where fixture_id in ({ids}) and person_uid is not null"):
        rows[(r["fixture_id"], r["side"])].append(r)
    found: dict[int, list[dict]] = defaultdict(list)
    for f in fixtures:
        for side, team in ((0, f["home_uid"]), (1, f["away_uid"])):
            if team not in teams:
                continue
            squad = rows[(f["id"], side)]
            starters = {r["person_uid"]: r["start_position"] for r in squad if r["start_position"]}
            scored, conceded, result = q.result_for(f, team)
            found[team].append({"id": f["id"], "date": date.fromisoformat(str(f["date"])[:10]), "competition": f["competition_uid"],
                                "shape": tactics.formation(starters.values()), "starters": starters,
                                "rating": {r["person_uid"]: r["rating"] for r in squad if r["rating"]},
                                "goals": {r["person_uid"]: r["goals"] for r in squad}, "assists": {r["person_uid"]: r["assists"] for r in squad},
                                "used": {r["person_uid"] for r in squad if r["minute_on"] and r["played"]},
                                "unused": {r["person_uid"] for r in squad if not r["played"]},
                                "scored": scored, "conceded": conceded, "result": result, "opponent": f["away_uid"] if side == 0 else f["home_uid"]})
    w._sheets = found
    return found


def _split(w, matches: list[dict]) -> tuple[list[dict], list[dict]]:
    return [m for m in matches if m["date"] <= w.since], [m for m in matches if m["date"] > w.since]


def _people(w, uids) -> dict[int, dict]:
    marks = ",".join(str(int(u)) for u in uids) or "0"
    return {r["fm_uid"]: r for r in w.rows(f"select fm_uid, name, birth_date, team_uid, reputation_world from people where fm_uid in ({marks})")}


def _results(matches: list[dict]) -> str:
    won = sum(m["result"] == "W" for m in matches)
    drawn = sum(m["result"] == "D" for m in matches)
    return f"{len(matches)} games: {won} won, {drawn} drawn, {len(matches) - won - drawn} lost, {sum(m['scored'] for m in matches)}-{sum(m['conceded'] for m in matches)}"


def _league(w, team: int) -> int | None:
    league, _ = w.level(team, w.season)
    return league


def _injured_during(w, uids, start: date, end: date) -> set[int]:
    marks = ",".join(str(int(u)) for u in uids) or "0"
    hurt = set()
    for r in w.rows(f"select person_uid, injured_on, days from injuries where person_uid in ({marks}) and injured_on is not null"):
        begun = date.fromisoformat(str(r["injured_on"])[:10])
        if begun <= end and begun + timedelta(days=r["days"]) >= start:
            hurt.add(r["person_uid"])
    return hurt


def system_changes(w) -> list:
    """A team that played its usual system in most matches before the cycle and another one in the cycle's matches,
    the newest included: the shapes, the results before and since, and a new manager if there is one."""
    from app.services.story_leads import Lead

    leads = []
    for team, matches in _sheets(w).items():
        before, cycle = _split(w, matches)
        before = before[-tactics.PROFILE_GAMES:]
        shapes = Counter(m["shape"] for m in before if m["shape"])
        if len(before) < BEFORE_GAMES or not shapes or len(cycle) < NEW_SHAPE_GAMES:
            continue
        usual, count = shapes.most_common(1)[0]
        if count < USUAL_SHARE * len(before):
            continue
        new = [m for m in cycle if m["shape"] and m["shape"] != usual]
        if len(new) < NEW_SHAPE_GAMES or cycle[-1]["shape"] == usual:
            continue
        shape = Counter(m["shape"] for m in new).most_common(1)[0][0]
        manager = w.rows("select name, staff_since from people where staff_job = 'Manager' and staff_team_uid = :t limit 1", t=team)
        new_manager = bool(manager and manager[0]["staff_since"] and str(manager[0]["staff_since"]) > str(w.until - timedelta(days=60)))
        league = _league(w, team)
        facts = {"club": w.name(team), "usual_system": f"{tactics.shape_words(usual)} ({count} of the {len(before)} games before)",
                 "new_system": tactics.shape_words(shape), "games_in_the_new_system": len(new), "results_before": _results(before[-6:]),
                 "results_in_the_new_system": _results(new), "manager": now._manager(w, team),
                 "new_manager": f"{manager[0]['name']} took over on {manager[0]['staff_since']}" if new_manager else None}
        size = 0.7 + (0.25 if new_manager else 0) + (w.rep(team) or 3000) / 40000
        leads.append(Lead("system_change", f"{w.name(team)} switch from {usual} to {shape}", w.until, w.weight(league) * 10 * size,
                          {k: v for k, v in facts.items() if v is not None}, [f"club:{team}"] + ([f"competition:{league}"] if league else []),
                          w.beats_for(team, competition_uid=league), slug=f"{team}:{shape}:{w.until:%Y-%m}"))
    return sorted(leads, key=lambda lead: -lead.score)[:6]


def role_changes(w) -> list:
    """A player who started mostly in one role before the cycle and in another in the cycle: how he does there (his
    ratings, goals and assists in each role)."""
    from app.services.story_leads import Lead

    leads = []
    for team, matches in _sheets(w).items():
        before, cycle = _split(w, matches)
        roles_before, roles_now = defaultdict(Counter), defaultdict(Counter)
        for m in before:
            for uid, slot in m["starters"].items():
                roles_before[uid][ROLES.get(slot)] += 1
        for m in cycle:
            for uid, slot in m["starters"].items():
                roles_now[uid][ROLES.get(slot)] += 1
        people = None
        for uid, counts in roles_now.items():
            if not roles_before.get(uid):
                continue
            old, old_starts = roles_before[uid].most_common(1)[0]
            new, new_starts = counts.most_common(1)[0]
            if old == new or "goalkeeper" in (old, new) or old_starts < ROLE_STARTS_BEFORE or new_starts < ROLE_STARTS_NOW:
                continue
            people = people or _people(w, roles_now)
            person = people.get(uid, {})

            def in_role(role, games):
                picked = [m for m in games if ROLES.get(m["starters"].get(uid)) == role]
                ratings = [m["rating"][uid] for m in picked if uid in m["rating"]]
                return {"starts": len(picked), "average_rating": tactics._avg(ratings),
                        "goals": sum(m["goals"].get(uid, 0) for m in picked), "assists": sum(m["assists"].get(uid, 0) for m in picked)}
            then, since = in_role(old, before), in_role(new, cycle)
            league = _league(w, team)
            better = (since["average_rating"] or 0) - (then["average_rating"] or 0)
            facts = {"player": person.get("name"), "club": w.name(team), "age": q.age_on(person.get("birth_date"), w.until),
                     "usual_role": old, "new_role": new, f"as_{old.replace(' ', '_')}": then, f"as_{new.replace(' ', '_')}": since,
                     "verdict": "thriving in the new role" if better >= 0.3 else "struggling in the new role" if better <= -0.3 else None}
            size = 0.6 + abs(better) / 2 + (person.get("reputation_world") or 0) / 30000
            leads.append(Lead("position_change", f"{person.get('name')} ({w.name(team)}): from {old} to {new}", w.until,
                              w.weight(league) * 10 * size, {k: v for k, v in facts.items() if v is not None},
                              [f"person:{uid}", f"club:{team}"], w.beats_for(team, competition_uid=league), slug=f"{uid}:{new}"))
    return sorted(leads, key=lambda lead: -lead.score)[:6]


def dropped(w) -> list:
    """A stalwart (most of the starts before the cycle) who started none of the cycle's matches, still at the club and
    on the bench at least once (so available: a player out of every squad may be injured, suspended or ill, which the
    save does not always say), and who took his slot (a newcomer, a youngster). The better known he is, the bigger."""
    from app.services.story_leads import Lead

    leads = []
    for team, matches in _sheets(w).items():
        before, cycle = _split(w, matches)
        before = before[-tactics.PROFILE_GAMES:]
        if len(before) < BEFORE_GAMES or len(cycle) < 2:
            continue
        starts = Counter(uid for m in before for uid in m["starters"])
        stalwarts = {uid for uid, n in starts.items() if n >= STALWART * len(before)}
        out = {uid for uid in stalwarts if not any(uid in m["starters"] for m in cycle)}
        if not out:
            continue
        people = _people(w, out | {uid for m in cycle for uid in m["starters"]})
        hurt = _injured_during(w, out, cycle[0]["date"] - timedelta(days=7), cycle[-1]["date"])
        signed = {r["person_uid"]: r for r in w.rows(
            "select t.person_uid, t.fee, t.date, n.name from_name from transfers t left join teams n on n.fm_uid = t.from_uid "
            "where t.to_uid = :t and t.date > :d and t.kind in (1, 2, 3, 4, 7, 8)", t=team, d=str(w.until - timedelta(days=tactics.NEW_DAYS)))}
        for uid in out - hurt:
            person = people.get(uid, {})
            if person.get("team_uid") != team:
                continue  # sold or loaned out: a transfer story
            slot = Counter(m["starters"][uid] for m in before if uid in m["starters"]).most_common(1)[0][0]
            takers = Counter(p for m in cycle for p, s in m["starters"].items() if s == slot and p not in stalwarts)
            taker = takers.most_common(1)[0][0] if takers else None
            benched = sum(uid in m["unused"] or uid in m["used"] for m in cycle)
            if not benched:
                continue
            league = _league(w, team)
            facts = {"player": person.get("name"), "club": w.name(team), "age": q.age_on(person.get("birth_date"), w.until),
                     "usual_slot": tactics.SLOT_WORDS.get(slot), "starts_before": f"{starts[uid]} of the {len(before)} games before",
                     "this_cycle": f"started none of {len(cycle)} games; on the bench in {benched}, out of the squad in {len(cycle) - benched}",
                     "injury": "none recorded", "results_this_cycle": _results(cycle), "manager": now._manager(w, team)}
            if taker:
                t = people.get(taker, {})
                facts["his_replacement"] = t.get("name")
                facts["replacement_age"] = q.age_on(t.get("birth_date"), w.until)
                if taker in signed:
                    s = signed[taker]
                    facts["replacement_is_a_new_signing"] = f"from {s['from_name']}, {s['date']}" + (f", £{s['fee'] / 1e6:.1f}m" if s["fee"] else "")
                ratings = [m["rating"][taker] for m in cycle if taker in m["rating"] and m["starters"].get(taker) == slot]
                facts["replacement_average_rating"] = tactics._avg(ratings)
            star = person.get("reputation_world") or 0
            size = 0.6 + star / 15000 + (0.15 if taker in signed else 0)
            leads.append(Lead("dropped", f"{person.get('name')} out of {w.name(team)}'s eleven" + (f", {facts.get('his_replacement')} in" if taker else ""),
                              w.until, w.weight(league) * 10 * size, {k: v for k, v in facts.items() if v is not None},
                              [f"person:{uid}", f"club:{team}"] + ([f"person:{taker}"] if taker else []),
                              w.beats_for(team, competition_uid=league), slug=f"{uid}:{w.until:%Y-%m}"))
    return sorted(leads, key=lambda lead: -lead.score)[:6]


def breakthroughs(w) -> list:
    """A player of twenty or younger who started BREAKTHROUGH_STARTS or more of the cycle's matches after at most one start
    before: his slot, his ratings, whose place he took."""
    from app.services.story_leads import Lead

    leads = []
    for team, matches in _sheets(w).items():
        before, cycle = _split(w, matches)
        starts_now = Counter(uid for m in cycle for uid in m["starters"])
        starts_before = Counter(uid for m in before for uid in m["starters"])
        hopefuls = [uid for uid, n in starts_now.items() if n >= BREAKTHROUGH_STARTS and starts_before[uid] <= 1]
        if not hopefuls:
            continue
        people = _people(w, hopefuls)
        for uid in hopefuls:
            person = people.get(uid, {})
            age = q.age_on(person.get("birth_date"), w.until)
            if age is None or age > YOUNG:
                continue
            slot = Counter(m["starters"][uid] for m in cycle if uid in m["starters"]).most_common(1)[0][0]
            ratings = [m["rating"][uid] for m in cycle if uid in m["rating"] and uid in m["starters"]]
            holder = Counter(p for m in before for p, s in m["starters"].items() if s == slot and p != uid).most_common(1)
            league = _league(w, team)
            facts = {"player": person.get("name"), "age": age, "club": w.name(team), "slot": tactics.SLOT_WORDS.get(slot),
                     "starts_this_cycle": starts_now[uid], "starts_before": starts_before[uid], "average_rating": tactics._avg(ratings),
                     "goals": sum(m["goals"].get(uid, 0) for m in cycle), "assists": sum(m["assists"].get(uid, 0) for m in cycle),
                     "slot_usually_held_by": _people(w, [holder[0][0]]).get(holder[0][0], {}).get("name") if holder else None}
            size = 0.6 + max((tactics._avg(ratings) or 6.5) - 6.5, 0) / 2 + (w.rep(team) or 3000) / 40000
            leads.append(Lead("breakthrough", f"{person.get('name')} ({age}) breaks into {w.name(team)}'s eleven", w.until,
                              w.weight(league) * 10 * size, {k: v for k, v in facts.items() if v is not None},
                              [f"person:{uid}", f"club:{team}"], w.beats_for(team, competition_uid=league) | {"young"}, slug=f"{uid}"))
    return sorted(leads, key=lambda lead: -lead.score)[:6]


def partnerships(w) -> list:
    """The clearest partnership of a team this season, with PARTNERS_SEASON games together and on the pitch in the cycle:
    the team's record with them and without one of them."""
    from app.services.story_leads import Lead

    leads = []
    for team, matches in _sheets(w).items():
        season = [m for m in matches if m["date"] >= w.until - timedelta(days=300)]
        cycle_ids = {m["id"] for m in season if m["date"] > w.since}
        if not cycle_ids:
            continue
        record = {m["id"]: (m["scored"], m["conceded"], m["result"]) for m in season}
        pairs: dict[tuple, list] = defaultdict(list)
        for m in season:
            for label, one, other in tactics.PAIRS:
                for a, sa in m["starters"].items():
                    for b, sb in m["starters"].items():
                        if a != b and sa in one and sb in other and (one != other or a < b):
                            pairs[(label, a, b)].append(m["id"])
        best = None
        for (label, a, b), games in pairs.items():
            if len(games) < PARTNERS_SEASON or not cycle_ids & set(games):
                continue
            apart = [fid for fid in record if fid not in games]
            pair = {"label": label, "players": (a, b), "together": tactics._record(record, games),
                    "apart": tactics._record(record, apart) if apart else None}
            verdict = tactics.partnership_verdict(pair)
            if verdict and (best is None or abs(tactics._gap(pair)) > abs(tactics._gap(best[0]))):
                best = (pair, verdict)
        if best is None:
            continue
        pair, verdict = best
        people = _people(w, pair["players"])
        names = [people.get(uid, {}).get("name") for uid in pair["players"]]
        league = _league(w, team)
        facts = {"club": w.name(team), "players": names, "role": pair["label"], "verdict": verdict,
                 "together": tactics._record_words(pair["together"]), "without_one_of_them": tactics._record_words(pair["apart"])}
        size = 0.55 + abs(tactics._gap(pair)) / 3 + (w.rep(team) or 3000) / 40000
        leads.append(Lead("partnership", f"{names[0]} and {names[1]}: {verdict} at {w.name(team)}", w.until, w.weight(league) * 10 * size,
                          facts, [f"club:{team}", *(f"person:{uid}" for uid in pair["players"])],
                          w.beats_for(team, competition_uid=league) | {"numbers"}, slug=f"{pair['players'][0]}:{pair['players'][1]}:{w.until:%Y-%m}"))
    return sorted(leads, key=lambda lead: -lead.score)[:4]


def tactical_trends(w) -> list:
    """A covered league's systems this season: how often each shape is used and the points a game it wins, once a month."""
    from app.services.story_leads import Lead

    leads = []
    sheets = _sheets(w)
    for league in now.current_leagues(w):
        start, _ = now._window(w, league)
        sides = [m for matches in sheets.values() for m in matches if m["competition"] == league and str(m["date"]) >= start and m["shape"]]
        if len(sides) < TREND_SIDES:
            continue
        shapes = Counter(m["shape"] for m in sides)
        points = {shape: round(sum(3 if m["result"] == "W" else 1 if m["result"] == "D" else 0 for m in sides if m["shape"] == shape) / n, 2)
                  for shape, n in shapes.items() if n >= TREND_SHAPE_SIDES}
        teams_by_shape = defaultdict(set)
        for team, matches in sheets.items():
            usual = Counter(m["shape"] for m in matches if m["competition"] == league and str(m["date"]) >= start and m["shape"]).most_common(1)
            if usual:
                teams_by_shape[usual[0][0]].add(w.name(team))
        facts = {"competition": w.comp(league)["name"], "team_sheets_read": len(sides),
                 "systems": [f"{tactics.shape_words(s)}: {n} times ({round(100 * n / len(sides))}%)" + (f", {points[s]} points a game" if s in points else "")
                             for s, n in shapes.most_common(6)],
                 "clubs_by_usual_system": {tactics.shape_words(s): sorted(t)[:8] for s, t in teams_by_shape.items()}}
        best = max(points, key=points.get) if points else None
        common = shapes.most_common(1)[0][0]
        leads.append(Lead("tactical_trend", f"{w.comp(league)['name']}: the {common} " + (
                              "rules and wins the most points" if best == common else f"rules, the {best} wins the most points" if best else "rules"), w.until, w.weight(league) * 10 * 0.9, facts,
                          [f"competition:{league}"], w.beats_for(competition_uid=league) | {"numbers"}, slug=f"{league}:{w.until:%Y-%m}"))
    return sorted(leads, key=lambda lead: -lead.score)[:3]


FINDERS = (system_changes, role_changes, dropped, breakthroughs, partnerships, tactical_trends)
