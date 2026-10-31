"""How teams line up, from the match reports' team sheets: the slot each player filled (fm_save: start and end slot).

Without AI: a side's formation at kick-off and at the end, a team's profile before a match (its usual shape, who usually
fills each slot, its regulars, its partnerships and how the team does with and without them), and the talking points of
one match's selection: a change from the usual shape or during the game, regulars missing and why (injured, on the
bench, not in the squad, sold), who took their slot, new signings and youngsters starting, stars on the bench, players
out of their usual or natural slot. The writers and the fans get them with the match report (story_notebooks,
world_queries.match_summary). Only the slots are known, not the roles, duties or instructions.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy.engine import Connection

from app.services import world_queries as q

# The lines of a team sheet, back to front; a formation counts the players in each line that has any ("4-2-3-1").
LINES = (("SW", "D (R)", "D (L)", "D (C)"), ("WB (R)", "WB (L)", "DM"), ("M (R)", "M (L)", "M (C)"), ("AM (R)", "AM (L)", "AM (C)"),
         ("ST (C)",))
# The game's names of its formations (the tactics screen, the Data Hub), by the outfield slots of a team sheet. 4-3-3 DM
# Wide, 4-2-3-1 DM AM Wide, 4-4-2, 5-2-3 DM Wide and 5-2-1-2 DM AM are checked on its screens; the rest follow its pattern: the lines back to front, then DM or AM
# when those lines are used, WB with wing-backs, Wide or Narrow by whether it has wide players.
FM_NAMES = {
    ("AM (L)", "AM (R)", "D (C)", "D (C)", "D (L)", "D (R)", "DM", "M (C)", "M (C)", "ST (C)"): "4-3-3 DM Wide",
    ("AM (C)", "AM (L)", "AM (R)", "D (C)", "D (C)", "D (L)", "D (R)", "DM", "DM", "ST (C)"): "4-2-3-1 DM AM Wide",
    ("D (C)", "D (C)", "D (L)", "D (R)", "M (C)", "M (C)", "M (L)", "M (R)", "ST (C)", "ST (C)"): "4-4-2",
    ("AM (C)", "D (C)", "D (C)", "D (C)", "DM", "DM", "ST (C)", "ST (C)", "WB (L)", "WB (R)"): "5-2-1-2 DM AM",
    ("D (C)", "D (C)", "D (C)", "DM", "M (C)", "M (C)", "ST (C)", "ST (C)", "WB (L)", "WB (R)"): "5-3-2 DM WB",
    ("AM (C)", "D (C)", "D (C)", "D (L)", "D (R)", "DM", "M (C)", "M (C)", "ST (C)", "ST (C)"): "4-1-2-1-2 DM AM Narrow",
    ("AM (L)", "AM (R)", "D (C)", "D (C)", "D (C)", "DM", "DM", "ST (C)", "WB (L)", "WB (R)"): "5-2-3 DM Wide",
    ("AM (C)", "AM (C)", "D (C)", "D (C)", "D (C)", "DM", "DM", "ST (C)", "WB (L)", "WB (R)"): "5-2-2-1 DM AM",
    ("AM (L)", "AM (R)", "D (C)", "D (C)", "D (L)", "D (R)", "DM", "DM", "ST (C)", "ST (C)"): "4-2-4 DM Wide",
}
WIDE = {"M (R)", "M (L)", "AM (R)", "AM (L)"}
SLOT_WORDS = {"GK": "goalkeeper", "SW": "sweeper", "D (C)": "centre-back", "D (R)": "right-back", "D (L)": "left-back",
              "WB (R)": "right wing-back", "WB (L)": "left wing-back", "DM": "defensive midfielder", "M (C)": "central midfielder",
              "M (R)": "right midfielder", "M (L)": "left midfielder", "AM (C)": "attacking midfielder", "AM (R)": "right winger",
              "AM (L)": "left winger", "ST (C)": "striker"}
SHORT = {"GK": "GK", "SW": "SW", "D (C)": "DC", "D (R)": "DR", "D (L)": "DL", "WB (R)": "WBR", "WB (L)": "WBL", "DM": "DM",
         "M (C)": "MC", "M (R)": "MR", "M (L)": "ML", "AM (C)": "AMC", "AM (R)": "AMR", "AM (L)": "AML", "ST (C)": "ST"}
# Pairs that play together, by the slots they fill.
PAIRS = (("centre-backs", {"D (C)", "SW"}, {"D (C)", "SW"}), ("central midfield", {"M (C)", "DM"}, {"M (C)", "DM"}),
         ("strike partners", {"ST (C)"}, {"ST (C)"}), ("right flank", {"D (R)", "WB (R)"}, {"M (R)", "AM (R)"}),
         ("left flank", {"D (L)", "WB (L)"}, {"M (L)", "AM (L)"}))
PROFILE_GAMES = 12  # the team's matches with a team sheet before this one, at most
PROFILE_DAYS = 240  # ... within this many days
MIN_GAMES = 3  # fewer, and a team has no usual shape yet
REGULAR = 0.6  # a share of the starts that makes a regular
PARTNERS_MIN = 4  # games together before a partnership is a fact
APART_MIN = 3  # ... and games without one of them to compare with
PARTNERSHIP_NOTES = 2  # a side's partnerships told, the clearest first
PARTNERSHIP_GAP = 0.5  # goals a game (conceded by centre-backs, scored by strikers), with against without, that make it good or bad
POINTS_GAP = 0.6  # points a game, with against without, for the other pairs
NEW_DAYS = 120  # a signing this recent is a newcomer
YOUNG = 20  # a starter this age or younger with few starts is a youngster getting his chance
STARS = 3  # the best-known players of a squad
UNNATURAL = 12  # a slot he rates below this (1-20) is not his position
COMPETITIVE = ("league", "cup", "continental")


def formation(slots) -> str | None:
    """The game's name of the formation of a team sheet's outfield slots ("4-3-3 DM Wide"); None without nine outfield
    slots (a red card leaves nine; ten men keep the numbers of their lines, as the game shows them)."""
    slots = sorted(slot for slot in slots if slot and slot != "GK")
    if len(slots) < 9:
        return None
    if tuple(slots) in FM_NAMES:
        return FM_NAMES[tuple(slots)]
    backs = sum(slot in ("SW", "D (R)", "D (L)", "D (C)", "WB (R)", "WB (L)") for slot in slots)
    counts = [backs, *(sum(slot in line for slot in slots) for line in (("DM",), LINES[2], LINES[3], LINES[4]))]
    name = "-".join(str(n) for n in counts if n)
    tags = [tag for tag, used in (("DM", "DM" in slots), ("AM", "AM (C)" in slots), ("WB", any(s.startswith("WB") for s in slots))) if used]
    if "WB" not in tags:
        tags.append("Wide" if WIDE & set(slots) else "Narrow")
    return f"{name} {' '.join(tags)}"


def shape_words(shape: str | None) -> str | None:
    """A formation as the papers name it: the game's own name."""
    return shape


def _players(connection: Connection, fixture_ids: list[int]) -> list[dict]:
    marks = ",".join(str(int(f)) for f in fixture_ids) or "0"
    return q._rows(connection, "select m.fixture_id, m.side, m.seq, m.person_uid, m.played, m.rating, m.goals, m.assists, m.minute_on, "
                               "m.minute_off, coalesce(m.red_cards, 0) red_cards, m.injury_minute, m.shirt_number, m.start_position, "
                               "m.end_position, p.name, p.birth_date, p.reputation_world from match_players m left join people p "
                               f"on p.fm_uid = m.person_uid where m.fixture_id in ({marks}) order by m.fixture_id, m.side, m.seq")


def match_sides(connection: Connection, fixture_id: int) -> list[dict] | None:
    """Both sides of a match with a team sheet: the team, the starters by slot, the substitutes, the shape at kick-off and
    at the end. None when the report has no slots (one imported before they were read)."""
    fixture = q._one(connection, "select * from fixtures where id = :f", f=fixture_id)
    rows = _players(connection, [fixture_id]) if fixture else []
    if not any(r["start_position"] for r in rows):
        return None
    sides = []
    for number, team in ((0, fixture["home_uid"]), (1, fixture["away_uid"])):
        squad = [r for r in rows if r["side"] == number]
        starters = [r for r in squad if r["start_position"]]
        at_end = [r for r in squad if r["end_position"] and not r["minute_off"] and not r["red_cards"]]
        sides.append({"team_uid": team, "squad": squad, "starters": starters,
                      "used": [r for r in squad if r["minute_on"] and r["played"]],
                      "unused": [r for r in squad if not r["played"]],
                      "shape": formation(r["start_position"] for r in starters),
                      "shape_at_end": formation(r["end_position"] for r in at_end)})
    return sides


def team_profile(connection: Connection, team_uid: int, before: date, games: int = PROFILE_GAMES, since: date | None = None) -> dict:
    """The team's line-ups in its last competitive matches with a team sheet before a day (from ``since``, else within
    PROFILE_DAYS): the shapes, who fills each slot, each player's starts by slot and ratings, the regulars, and its
    partnerships with the team's record with and without."""
    fixtures = q._rows(connection, "select f.* from fixtures f join competitions c on c.fm_uid = f.competition_uid "
                                   "where (f.home_uid = :t or f.away_uid = :t) and f.date < :d and f.date >= :s and f.home_goals is not null "
                                   "and c.scope in ('league', 'cup', 'continental') and exists (select 1 from match_players m "
                                   "where m.fixture_id = f.id and m.start_position is not null) order by f.date desc limit :n",
                       t=team_uid, d=str(before), s=str(since or before - timedelta(days=PROFILE_DAYS)), n=games)
    rows = _players(connection, [f["id"] for f in fixtures])
    by_fixture: dict[int, list] = defaultdict(list)
    side_of = {f["id"]: 0 if f["home_uid"] == team_uid else 1 for f in fixtures}
    for r in rows:
        if r["side"] == side_of[r["fixture_id"]]:
            by_fixture[r["fixture_id"]].append(r)
    shapes, slot_starts = Counter(), defaultdict(Counter)
    players: dict[int, dict] = {}
    pair_games: dict[tuple, list] = defaultdict(list)
    results = []
    for f in fixtures:
        starters = [r for r in by_fixture[f["id"]] if r["start_position"] and r["person_uid"]]
        scored, conceded, result = q.result_for(f, team_uid)
        results.append((f["id"], scored, conceded, result))
        shapes[formation(r["start_position"] for r in starters)] += 1
        for r in starters:
            slot_starts[r["start_position"]][r["person_uid"]] += 1
            p = players.setdefault(r["person_uid"], {"name": r["name"], "starts": 0, "slots": Counter(), "ratings": defaultdict(list)})
            p["starts"] += 1
            p["slots"][r["start_position"]] += 1
            if r["rating"]:
                p["ratings"][r["start_position"]].append(r["rating"])
        for label, one, other in PAIRS:
            for a in starters:
                for b in starters:
                    if a["person_uid"] < b["person_uid"] if one == other else True:
                        if a["start_position"] in one and b["start_position"] in other and a is not b:
                            pair_games[(label, a["person_uid"], b["person_uid"])].append(f["id"])
    shapes.pop(None, None)
    n = len(fixtures)
    usual = shapes.most_common(1)[0] if shapes else (None, 0)
    record = {fid: (scored, conceded, result) for fid, scored, conceded, result in results}
    partnerships = []
    for (label, a, b), together in pair_games.items():
        if len(together) < PARTNERS_MIN:
            continue
        apart = [fid for fid in record if fid not in together]
        partnerships.append({"label": label, "players": (a, b), "names": (players[a]["name"], players[b]["name"]),
                             "together": _record(record, together), "apart": _record(record, apart) if apart else None})
    return {"team_uid": team_uid, "games": n, "shapes": shapes, "usual_shape": usual[0], "usual_share": usual[1],
            "slot_starts": slot_starts, "players": players,
            "regulars": {uid for uid, p in players.items() if n >= MIN_GAMES and p["starts"] >= REGULAR * n},
            "partnerships": partnerships}


def _record(record: dict, fixtures: list[int]) -> dict:
    games = [record[f] for f in fixtures]
    won, drawn = sum(g[2] == "W" for g in games), sum(g[2] == "D" for g in games)
    return {"games": len(games), "won": won, "drawn": drawn, "lost": len(games) - won - drawn,
            "scored": round(sum(g[0] for g in games) / len(games), 1), "conceded": round(sum(g[1] for g in games) / len(games), 1),
            "points": round((3 * won + drawn) / len(games), 1)}


def _gap(pair: dict) -> float:
    """How much better the team does with the pair than without one of them, in goals (see partnership_verdict)."""
    together, apart = pair["together"], pair["apart"]
    if not apart:
        return 0.0
    if pair["label"] == "centre-backs":
        return apart["conceded"] - together["conceded"]
    if pair["label"] == "strike partners":
        return together["scored"] - apart["scored"]
    return (together["points"] - apart["points"]) * PARTNERSHIP_GAP / POINTS_GAP


def partnership_verdict(pair: dict) -> str | None:
    """A strong or a struggling partnership, or neither: centre-backs by the goals conceded with them and without one of
    them, strike partners by the goals scored, the others by the points a game."""
    apart = pair["apart"]
    if not apart or apart["games"] < APART_MIN:
        return None
    gap = _gap(pair)
    return "a strong partnership" if gap >= PARTNERSHIP_GAP else "a struggling partnership" if gap <= -PARTNERSHIP_GAP else None


def _record_words(r: dict) -> str:
    return (f"{r['games']} game{'s' if r['games'] != 1 else ''} ({r['won']}-{r['drawn']}-{r['lost']}), {r['scored']} scored and "
            f"{r['conceded']} conceded a game")


def _ratings(connection: Connection, uids) -> dict[int, dict]:
    """Each player's position ratings (1-20) from his newest snapshot."""
    marks = ",".join(str(int(u)) for u in uids if u) or "0"
    found = {}
    for row in q._rows(connection, "select person_uid, positions from (select ps.person_uid, ps.positions, row_number() over "
                                   "(partition by ps.person_uid order by s.game_date desc) n from player_snapshots ps join saves s "
                                   f"on s.id = ps.save_id where ps.person_uid in ({marks}) and ps.positions is not null) where n = 1"):
        found[row["person_uid"]] = json.loads(row["positions"]) if isinstance(row["positions"], str) else row["positions"]
    return found


def _starts(n: int) -> str:
    return f"{n} start{'s' if n != 1 else ''}"


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def line_up(side: dict) -> str:
    """'4-2-3-1: GK #1 Jimenez; DR #48 Pico, ...' in the lines of the team sheet."""
    starters = sorted(side["starters"], key=lambda r: _order(r["start_position"]))
    names = "; ".join(f"{SHORT.get(r['start_position'], r['start_position'])} {('#' + str(r['shirt_number']) + ' ') if r['shirt_number'] else ''}"
                      f"{r['name'] or 'unknown'}" for r in starters)
    return f"{side['shape'] or 'unknown shape'}: {names}"


def selection_notes(connection: Connection, fixture_id: int) -> dict[int, dict] | None:
    """The talking points of each side's selection in one match, against the team's profile before it (see the module
    notes): {team uid: {"line_up", "shape", "usual_shape", "notes", "substitutions"}}. None without a team sheet."""
    sides = match_sides(connection, fixture_id)
    if sides is None:
        return None
    fixture = q._one(connection, "select * from fixtures where id = :f", f=fixture_id)
    day = date.fromisoformat(str(fixture["date"])[:10])
    found = {}
    for side in sides:
        team = side["team_uid"]
        profile = team_profile(connection, team, day)
        squad = {r["person_uid"]: r for r in side["squad"] if r["person_uid"]}
        starting = {r["person_uid"] for r in side["starters"] if r["person_uid"]}
        ratings = _ratings(connection, list(squad) + list(profile["regulars"]))
        notes: list[str] = []
        n = profile["games"]
        # the shape
        if n >= MIN_GAMES and profile["usual_shape"] and side["shape"] and side["shape"] != profile["usual_shape"]:
            notes.append(f"Changed shape: {shape_words(side['shape'])}, not their usual {shape_words(profile['usual_shape'])} "
                         f"({profile['usual_share']} of their last {n} games)")
        if side["shape_at_end"] and side["shape"] and side["shape_at_end"] != side["shape"]:
            notes.append(f"Finished the game in {shape_words(side['shape_at_end'])}, after starting in {shape_words(side['shape'])}")
        # substitutions, with the slot each one took (the player going off in the same minute, in the same slot first)
        gone, substitutions = [], []
        for r in sorted(side["used"], key=lambda r: r["minute_on"]):
            leaving = [x for x in side["squad"] if x["minute_off"] == r["minute_on"] and x is not r and not any(x is g for g in gone)]
            off = next((x for x in leaving if x["end_position"] == r["end_position"]), None) or (leaving[0] if leaving else None)
            if off:
                gone.append(off)
            words = f"{r['minute_on']}': {r['name']} on as {SLOT_WORDS.get(r['end_position'], r['end_position'])}"
            if off:
                words += f" for {off['name']} ({SLOT_WORDS.get(off['end_position'] or off['start_position'], 'unknown slot')}"
                words += ", injured)" if off["injury_minute"] else ")"
            substitutions.append(words)
        # a starter who moved during the game
        for r in side["starters"]:
            if r["end_position"] and r["end_position"] != r["start_position"]:
                notes.append(f"{r['name']} moved from {SLOT_WORDS.get(r['start_position'])} to {SLOT_WORDS.get(r['end_position'])} during the game")
        # regulars missing, and who took their slot
        missing = sorted(profile["regulars"] - starting, key=lambda uid: -profile["players"][uid]["starts"])
        absent_reasons = _absences(connection, team, day, [uid for uid in missing if uid not in squad])
        taken: set = set()
        for uid in missing:
            p = profile["players"][uid]
            slot = p["slots"].most_common(1)[0][0]
            r = squad.get(uid)
            if r is None:
                why = absent_reasons.get(uid, "not in the squad")
            elif r["played"]:
                why = f"on the bench, came on in the {_ordinal(r['minute_on'])} minute"
            else:
                why = "on the bench and not used"
            taker = next((s for s in side["starters"] if s["start_position"] == slot and s["person_uid"] not in profile["regulars"]
                          and s["person_uid"] not in taken), None)
            words = f"{p['name']}, a regular ({p['starts']} of the last {n} starts, usually {SLOT_WORDS.get(slot, slot)}), {why}"
            if taker:
                taken.add(taker["person_uid"])
                words += f"; {taker['name']} took his slot"
            notes.append(words)
        # newcomers, youngsters and players in a new or unnatural slot
        newcomers = _newcomers(connection, team, day, starting)
        for r in side["starters"]:
            uid, slot = r["person_uid"], r["start_position"]
            if not uid:
                continue
            before = profile["players"].get(uid)
            age = q.age_on(r["birth_date"], day)
            if uid in newcomers:
                notes.append(f"{r['name']}, a new signing ({newcomers[uid]}), started as {SLOT_WORDS.get(slot)}"
                             + ("; his first start in the reports" if not before else f"; {_starts(before['starts'])} before this"))
            elif age is not None and age <= YOUNG and (not before or before["starts"] <= 2):
                notes.append(f"{r['name']}, aged {age}, started as {SLOT_WORDS.get(slot)}"
                             + (" for the first time in the reports" if not before else f" ({_starts(before['starts'])} before this)"))
            if before and before["starts"] >= MIN_GAMES and before["slots"][slot] == 0:
                usual = before["slots"].most_common(1)[0][0]
                notes.append(f"{r['name']} started as {SLOT_WORDS.get(slot)}, not his usual {SLOT_WORDS.get(usual)}")
            level = ratings.get(uid, {}).get(slot)
            if level is not None and level < UNNATURAL:
                notes.append(f"{r['name']} played out of position as {SLOT_WORDS.get(slot)} (the game rates him "
                             f"{q.position_level(level)} there)")
        # stars on the bench
        best = sorted((r for r in side["squad"] if r["person_uid"] and r["reputation_world"]), key=lambda r: -r["reputation_world"])[:STARS]
        for r in best:
            if r["person_uid"] not in starting:
                notes.append(f"{r['name']}, one of the squad's best-known players, " +
                             (f"started on the bench and came on in the {_ordinal(r['minute_on'])} minute" if r["played"] else "was an unused substitute"))
        # partnerships on the pitch: the clearest few, one per record (a trio shares one)
        told, records = [], set()
        for pair in sorted(profile["partnerships"], key=lambda pair: -abs(_gap(pair))):
            a, b = pair["players"]
            verdict = partnership_verdict(pair) if a in starting and b in starting else None
            record = (tuple(pair["together"].values()), tuple(pair["apart"].values()) if pair["apart"] else None)
            if verdict and record not in records and len(told) < PARTNERSHIP_NOTES:
                records.add(record)
                told.append(f"{pair['names'][0]} and {pair['names'][1]} ({pair['label']}), {verdict}: together "
                            f"{_record_words(pair['together'])}; without one of them {_record_words(pair['apart'])}")
        notes += told
        for pair in game_partnerships(connection, team):  # the human club: the game's own record of the pairs on the pitch
            a, b = pair["players"]
            if a in starting and b in starting and pair["games"] >= PARTNERS_MIN and len(told) < PARTNERSHIP_NOTES + 1:
                told.append(pair["words"])
                notes.append(f"{pair['names'][0]} and {pair['names'][1]}: {pair['words']}")
        found[team] = {"line_up": line_up(side), "shape": shape_words(side["shape"]),
                       "usual_shape": shape_words(profile["usual_shape"]) if n >= MIN_GAMES else None,
                       "games_with_a_team_sheet_before": n, "notes": notes, "substitutions": substitutions}
    return found


def _absences(connection: Connection, team: int, day: date, uids: list[int]) -> dict[int, str]:
    """Why regulars were not in the squad: injured that day, or gone from the club."""
    marks = ",".join(str(int(u)) for u in uids) or "0"
    found = {}
    for r in q._rows(connection, f"select person_uid, injury, injured_on, days from injuries where person_uid in ({marks})"):
        start = date.fromisoformat(str(r["injured_on"])[:10]) if r["injured_on"] else None
        if start and start <= day < start + timedelta(days=r["days"] + 1):
            found[r["person_uid"]] = f"injured ({r['injury']})"
    for r in q._rows(connection, f"select t.person_uid, t.kind, t.to_uid, n.name from transfers t left join teams n on n.fm_uid = t.to_uid "
                                 f"where t.person_uid in ({marks}) and t.from_uid = :t and t.date <= :d and t.date >= :s",
                     t=team, d=str(day), s=str(day - timedelta(days=PROFILE_DAYS))):
        found.setdefault(r["person_uid"], f"gone: {q.TRANSFER_KINDS.get(r['kind'], 'left').lower()} to {r['name'] or 'no club'}")
    return found


def _newcomers(connection: Connection, team: int, day: date, uids) -> dict[int, str]:
    """Starters who joined the team in the last NEW_DAYS: where from, and the fee."""
    marks = ",".join(str(int(u)) for u in uids) or "0"
    found = {}
    for r in q._rows(connection, f"select t.person_uid, t.kind, t.fee, t.date, n.name from transfers t left join teams n on n.fm_uid = t.from_uid "
                                 f"where t.person_uid in ({marks}) and t.to_uid = :t and t.kind in (1, 2, 3, 4, 7, 8) and t.date <= :d "
                                 f"and t.date >= :s order by t.date", t=team, d=str(day), s=str(day - timedelta(days=NEW_DAYS))):
        fee = f", £{r['fee'] / 1e6:.1f}m" if r["fee"] else ""
        found[r["person_uid"]] = f"{q.TRANSFER_KINDS.get(r['kind'], 'signed').lower()} from {r['name'] or 'no club'}, {r['date']}{fee}"
    return found


# -- before a match ---------------------------------------------------------------------------------------------------
# Which slot faces which: a side's right winger meets the other side's left-back.
DUELS = ((("AM (R)", "M (R)"), ("D (L)", "WB (L)")), (("AM (L)", "M (L)"), ("D (R)", "WB (R)")))


def _avg(values: list) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


ORDER = ["GK", *[slot for line in LINES for slot in line]]


def _order(slot: str) -> int:
    return ORDER.index(slot) if slot in ORDER else 99


def usual_slots(connection: Connection, team: int, before: date, shape: str | None) -> list[str] | None:
    """The slots of the team's newest team sheet in a shape before a day (two centre-backs, one striker...)."""
    if not shape:
        return None
    by_match: dict[int, list] = defaultdict(list)
    for r in q._rows(connection, "select f.id, m.start_position from fixtures f join match_players m on m.fixture_id = f.id "
                                 "where (f.home_uid = :t and m.side = 0 or f.away_uid = :t and m.side = 1) and f.date < :d "
                                 "and m.start_position is not null order by f.date desc limit 400", t=team, d=str(before)):
        by_match[r["id"]].append(r["start_position"])
    slots = next((s for s in by_match.values() if formation(s) == shape), None)
    return sorted(slots, key=_order) if slots else None


def pick_eleven(profile: dict, slots: list[str], out: set = frozenset()) -> dict[int, str]:
    """A player for each slot: first those who usually fill it, then for an empty slot one of the same line, then anyone
    left, the most used first; never one of ``out``. {person: slot}, in the order of the team sheet."""
    chosen: dict[int, str] = {}
    empty = []
    for slot in slots:
        pick = next((uid for uid, _ in profile["slot_starts"][slot].most_common() if uid not in chosen and uid not in out), None)
        if pick is None:
            empty.append(slot)
        else:
            chosen[pick] = slot
    line_of = {slot: number for number, line in enumerate((("GK",), *LINES)) for slot in line}
    for slot in empty:
        spare = sorted((uid for uid in profile["players"] if uid not in chosen and uid not in out),
                       key=lambda uid: (-sum(n for s, n in profile["players"][uid]["slots"].items() if line_of.get(s) == line_of.get(slot)),
                                        -profile["players"][uid]["starts"]))
        if spare:
            chosen[spare[0]] = slot
    return dict(sorted(chosen.items(), key=lambda kv: _order(kv[1])))


def expected_eleven(connection: Connection, team: int, day: date, shape: str | None = None) -> dict | None:
    """The team's likely eleven for a match on ``day``: its usual shape (or ``shape``, the one it plays against such an
    opponent) and, for each slot of it, the player who has filled it most, without the injured and those who have left
    (the next one takes the slot). With the regulars who are out and why, and each chosen player's average rating in his
    recent starts. None before MIN_GAMES matches with a team sheet."""
    profile = team_profile(connection, team, day)
    if profile["games"] < MIN_GAMES or not profile["usual_shape"]:
        return None
    shape = shape if shape and usual_slots(connection, team, day, shape) else profile["usual_shape"]
    slots = usual_slots(connection, team, day, shape)
    if not slots:
        return None
    candidates = set().union(*(set(c) for c in profile["slot_starts"].values()))
    here = {r["fm_uid"] for r in q._rows(connection, f"select fm_uid from people where team_uid = :t and fm_uid in "
                                                     f"({','.join(str(int(u)) for u in candidates) or '0'})", t=team)}
    injured = q.current_injuries(connection, candidates)  # out only when still injured on the match day
    out = {uid: f"injured: {i['injury']}, back about {i['back_on']:%-d %B}" for uid, i in injured.items()
           if uid in here and (i["back_on"] is None or i["back_on"] >= day)}
    out |= {uid: "has left the club" for uid in candidates if uid not in here}
    chosen = pick_eleven(profile, slots, set(out))
    players = profile["players"]
    line = "; ".join(f"{SHORT.get(slot, slot)} {players[uid]['name']}" for uid, slot in chosen.items())
    form = {uid: _avg([r for rs in players[uid]["ratings"].values() for r in rs]) for uid in chosen}
    missing = [f"{players[uid]['name']} ({SLOT_WORDS.get(players[uid]['slots'].most_common(1)[0][0])}, {why})"
               for uid, why in out.items() if uid in profile["regulars"]]
    return {"shape": shape, "shape_words": shape_words(shape), "share": profile["shapes"][shape],
            "games": profile["games"], "line_up": f"{shape}: {line}", "chosen": chosen, "form": form,
            "names": {uid: players[uid]["name"] for uid in chosen}, "regulars_out": missing, "out": out}


def key_duels(home: dict, away: dict) -> list[str]:
    """The flank duels of two expected elevens: a right winger against the left-back facing him, with each one's average
    rating in his recent starts."""
    found = []
    for first, second in ((home, away), (away, home)):
        for attack, defence in DUELS:
            a = next((uid for uid, slot in first["chosen"].items() if slot in attack), None)
            d = next((uid for uid, slot in second["chosen"].items() if slot in defence), None)
            if a and d:
                found.append(f"{first['names'][a]} ({SLOT_WORDS[first['chosen'][a]]}, average rating {first['form'][a]}) against "
                             f"{second['names'][d]} ({SLOT_WORDS[second['chosen'][d]]}, {second['form'][d]})")
    return found



# -- a season ---------------------------------------------------------------------------------------------------------
# A style figure, how it is counted for a side in a match, and the label it earns against the league average.
STYLE = (("possession", "Possession", "%"), ("passes", "Passes", ""), ("pass_share", "Passes completed", "%"),
         ("crosses", "Crosses", ""), ("dribbles", "Dribbles", ""), ("ball_won", "Tackles and interceptions", ""),
         ("headers", "Headers", ""), ("shots", "Shots", ""), ("xg", "Expected goals", ""), ("shots_against", "Shots against", ""),
         ("xg_against", "Expected goals against", ""))
STYLE_DECIMALS = {"shots": 1, "shots_against": 1, "xg": 2, "xg_against": 2}  # the others are whole numbers
LABELS = ((lambda t, avg: t["possession"] >= 55, "Keeps the ball"), (lambda t, avg: t["possession"] <= 45, "Lets the opponent have the ball"),
          (lambda t, avg: t["pass_share"] >= avg["pass_share"] + 3, "Patient, accurate passing"),
          (lambda t, avg: t["crosses"] >= 1.3 * avg["crosses"], "Plays down the wings and crosses a lot"),
          (lambda t, avg: t["crosses"] <= 0.7 * avg["crosses"], "Rarely crosses"),
          (lambda t, avg: t["dribbles"] >= 1.3 * avg["dribbles"], "Dribbles a lot"),
          (lambda t, avg: t["ball_won"] >= 1.15 * avg["ball_won"], "Wins the ball back a lot"),
          (lambda t, avg: t["headers"] >= 1.25 * avg["headers"], "Direct, in the air a lot"),
          (lambda t, avg: t["xg"] >= 1.35 * avg["xg"], "Creates a lot"), (lambda t, avg: t["xg"] <= 0.7 * avg["xg"], "Creates little"),
          (lambda t, avg: t["xg_against"] <= 0.7 * avg["xg_against"], "Tight at the back"),
          (lambda t, avg: t["xg_against"] >= 1.35 * avg["xg_against"], "Open at the back"))


def _side_styles(connection: Connection, fixture_ids: list[int]) -> dict[tuple[int, int], dict]:
    """Each side's style figures in the given matches (from the players' sums)."""
    marks = ",".join(str(int(f)) for f in fixture_ids) or "0"
    sums = {(r["fixture_id"], r["side"]): r for r in q._rows(connection,
        "select fixture_id, side, sum(passes) passes, sum(passes_completed) done, sum(crosses) crosses, sum(dribbles) dribbles, "
        "sum(tackles) + sum(interceptions) ball_won, sum(headers) headers, sum(shots) shots, sum(xg) xg from match_players "
        f"where fixture_id in ({marks}) and played = 1 group by fixture_id, side")}
    found = {}
    for (fixture, side), r in sums.items():
        other = sums.get((fixture, 1 - side))
        if not other or not (r["done"] + other["done"]):
            continue
        found[(fixture, side)] = {"possession": 100 * r["done"] / (r["done"] + other["done"]), "passes": r["passes"],
                                  "pass_share": 100 * r["done"] / r["passes"] if r["passes"] else 0, "crosses": r["crosses"],
                                  "dribbles": r["dribbles"], "ball_won": r["ball_won"], "headers": r["headers"], "shots": r["shots"],
                                  "xg": r["xg"], "shots_against": other["shots"], "xg_against": other["xg"]}
    return found


def _mean(rows: list[dict]) -> dict:
    return {key: round(sum(r[key] for r in rows) / len(rows), 2) for key, _, _ in STYLE} if rows else {}


def how_they_play(connection: Connection, team: int, start: str, end: str, league: int | None) -> dict | None:
    """A team's season from its team sheets (start to end): the shapes it used, its usual eleven with each player's starts
    and average rating, its style against the league's average (with labels), its partnerships and how much it rotates.
    None before MIN_GAMES matches with a team sheet."""
    until = date.fromisoformat(str(end)[:10]) + timedelta(days=1)
    profile = team_profile(connection, team, until, games=80, since=date.fromisoformat(str(start)[:10]))
    if profile["games"] < MIN_GAMES:
        return None
    slots = usual_slots(connection, team, until, profile["usual_shape"]) or []
    eleven = pick_eleven(profile, slots)
    players = profile["players"]
    usual = [{"person_uid": uid, "name": players[uid]["name"], "position": slot, "slot": 0, "starts": players[uid]["starts"],
              "rating": _avg([r for rs in players[uid]["ratings"].values() for r in rs])} for uid, slot in eleven.items()]
    mine = q._rows(connection, "select f.id, case when f.home_uid = :t then 0 else 1 end side from fixtures f join competitions c "
                               "on c.fm_uid = f.competition_uid where (f.home_uid = :t or f.away_uid = :t) and f.date between :s and :e "
                               "and c.scope in ('league', 'cup', 'continental') and f.home_goals is not null", t=team, s=start, e=end)
    league_games = [r["id"] for r in q._rows(connection, "select id from fixtures where competition_uid = :l and date between :s and :e "
                                                         "and home_goals is not null", l=league, s=start, e=end)] if league else []
    styles = _side_styles(connection, [r["id"] for r in mine] + league_games)
    team_style = _mean([styles[(r["id"], r["side"])] for r in mine if (r["id"], r["side"]) in styles])
    league_style = _mean([v for (f, _), v in styles.items() if f in set(league_games)])
    labels = [label for test, label in LABELS if team_style and league_style and test(team_style, league_style)]
    sheets = q._rows(connection, "select f.id, f.date, m.person_uid from fixtures f join match_players m on m.fixture_id = f.id "
                                 "join competitions c on c.fm_uid = f.competition_uid where (f.home_uid = :t and m.side = 0 or "
                                 "f.away_uid = :t and m.side = 1) and m.start_position is not null and f.date between :s and :e "
                                 "and c.scope in ('league', 'cup', 'continental') order by f.date", t=team, s=start, e=end)
    elevens: dict[int, set] = defaultdict(set)
    for r in sheets:
        elevens[r["id"]].add(r["person_uid"])
    ordered = list(elevens.values())
    changes = [len(b - a) for a, b in zip(ordered, ordered[1:])]
    pairs = sorted((p for p in profile["partnerships"] if partnership_verdict(p)), key=lambda p: -abs(_gap(p)))
    return {"games": profile["games"], "shapes": [(shape_words(s), n) for s, n in profile["shapes"].most_common()],
            "usual_shape": shape_words(profile["usual_shape"]), "usual_eleven": usual,
            "style": [{"label": name, "team": team_style.get(key), "league": league_style.get(key), "unit": unit,
                       "format": f"%.{STYLE_DECIMALS.get(key, 0)}f"} for key, name, unit in STYLE]
            if team_style else [], "labels": labels,
            "partnerships": [{"names": p["names"], "label": p["label"], "verdict": partnership_verdict(p),
                              "together": _record_words(p["together"]), "apart": _record_words(p["apart"])} for p in pairs[:4]],
            "players_used": len(players), "changes_a_game": round(sum(changes) / len(changes), 1) if changes else None,
            "formations": formation_records(connection, team, start, end)}


def formation_records(connection: Connection, team: int, start: str, end: str) -> dict:
    """Like the game's Data Hub, for any team: in its competitive matches between two dates with both team sheets, its
    record by the formation it used and by the formation it faced: games, points a game, goals, expected goals and
    clear-cut chances for and against, and the minutes a chance. {"used": [...], "faced": [...]}, most used first."""
    fixtures = q._rows(connection, "select f.*, coalesce(r.minutes, 90) minutes from fixtures f join competitions c on c.fm_uid = f.competition_uid "
                                   "left join match_reports r on r.fixture_id = f.id where (f.home_uid = :t or f.away_uid = :t) "
                                   "and f.date between :s and :e and f.home_goals is not null and c.scope in ('league', 'cup', 'continental') "
                                   "and exists (select 1 from match_players m where m.fixture_id = f.id and m.start_position is not null)",
                       t=team, s=start, e=end)
    marks = ",".join(str(f["id"]) for f in fixtures) or "0"
    slots, figures = defaultdict(list), {}
    for r in q._rows(connection, f"select fixture_id, side, start_position from match_players where fixture_id in ({marks}) "
                                 "and start_position is not null"):
        slots[(r["fixture_id"], r["side"])].append(r["start_position"])
    for r in q._rows(connection, f"select fixture_id, side, sum(xg) xg, sum(clear_cut_chances) chances from match_players "
                                 f"where fixture_id in ({marks}) group by fixture_id, side"):
        figures[(r["fixture_id"], r["side"])] = r
    teams = {(r["fixture_id"], r["side"]): r["clear_cut_chances"] for r in q._rows(
        connection, f"select fixture_id, side, clear_cut_chances from match_teams where fixture_id in ({marks})")}
    used, faced = defaultdict(list), defaultdict(list)
    for f in fixtures:
        side = 0 if f["home_uid"] == team else 1
        mine, theirs = formation(slots[(f["id"], side)]), formation(slots[(f["id"], 1 - side)])
        if not mine or not theirs:
            continue
        scored, conceded, result = q.result_for(f, team)
        chances = [teams.get((f["id"], s)) if teams.get((f["id"], s)) is not None else (figures.get((f["id"], s)) or {}).get("chances") or 0
                   for s in (side, 1 - side)]
        game = {"result": result, "scored": scored, "conceded": conceded, "minutes": f["minutes"],
                "xg": (figures.get((f["id"], side)) or {}).get("xg") or 0, "xg_against": (figures.get((f["id"], 1 - side)) or {}).get("xg") or 0,
                "chances": chances[0], "chances_against": chances[1]}
        used[mine].append(game)
        faced[theirs].append(game)

    def rows(by_shape: dict) -> list[dict]:
        found = []
        for shape, games in sorted(by_shape.items(), key=lambda kv: -len(kv[1])):
            n, minutes = len(games), sum(g["minutes"] for g in games)
            won, drawn = sum(g["result"] == "W" for g in games), sum(g["result"] == "D" for g in games)
            chances, against = sum(g["chances"] for g in games), sum(g["chances_against"] for g in games)
            found.append({"shape": shape, "games": n, "won": won, "drawn": drawn, "lost": n - won - drawn,
                          "points": round((3 * won + drawn) / n, 2), "scored": sum(g["scored"] for g in games),
                          "conceded": sum(g["conceded"] for g in games), "xg": round(sum(g["xg"] for g in games) / n, 2),
                          "xg_against": round(sum(g["xg_against"] for g in games) / n, 2), "chances": chances, "chances_against": against,
                          "chance_every": round(minutes / chances) if chances else None,
                          "chance_against_every": round(minutes / against) if against else None})
        return found
    return {"used": rows(used), "faced": rows(faced), "games": sum(len(g) for g in used.values())}


def record_words(row: dict) -> str:
    """'4 games (3-1-0), 2.5 points a game, 9-2, a clear-cut chance every 45 minutes and one against every 180'."""
    words = (f"{row['games']} game{'s' if row['games'] != 1 else ''} ({row['won']}-{row['drawn']}-{row['lost']}), "
             f"{row['points']} points a game, goals {row['scored']}-{row['conceded']}")
    if row["chance_every"]:
        words += f", a clear-cut chance every {row['chance_every']} minutes"
    if row["chance_against_every"]:
        words += f" and one against every {row['chance_against_every']}"
    return words


# -- attribute duels ----------------------------------------------------------------------------------------------------
# Who meets whom in two expected elevens, and the game's attributes (1-20) that decide it.
FLANKS = ((("AM (R)", "M (R)"), ("D (L)", "WB (L)")), (("AM (L)", "M (L)"), ("D (R)", "WB (R)")))
CENTRE_BACKS = ("D (C)", "SW")
DUEL_EDGE = 3  # points of difference that make an edge
DUELS_SHOWN = 4


def _attributes(connection: Connection, uids) -> dict[int, dict]:
    """Each player's attributes and height from his newest snapshot."""
    marks = ",".join(str(int(u)) for u in uids if u) or "0"
    found = {}
    for row in q._rows(connection, "select person_uid, attributes, height_cm from (select ps.person_uid, ps.attributes, ps.height_cm, "
                                   "row_number() over (partition by ps.person_uid order by s.game_date desc) n from player_snapshots ps "
                                   f"join saves s on s.id = ps.save_id where ps.person_uid in ({marks})) where n = 1"):
        attributes = json.loads(row["attributes"]) if isinstance(row["attributes"], str) else row["attributes"]
        found[row["person_uid"]] = {**attributes, "height": row["height_cm"]}
    return found


def _mean_of(player: dict, names: tuple[str, ...]) -> float:
    return sum(player.get(name, 0) for name in names) / len(names)


def _figures(player: dict, names: tuple[str, ...]) -> str:
    return ", ".join(f"{name} {player[name]}" for name in names if name in player)


def attribute_duels(connection: Connection, home: dict, away: dict) -> list[str]:
    """The duels two expected elevens (expected_eleven) set up, with the attributes that decide them, where one side has a
    clear edge: a winger against the full-back facing him (speed, dribbling against tackling), a striker against the
    centre-backs in the air (heading, jumping, strength, height) and in behind (speed against speed and anticipation).
    The clearest first."""
    found: list[tuple[float, str]] = []
    players = _attributes(connection, list(home["chosen"]) + list(away["chosen"]))

    def pick(side: dict, slots) -> list[int]:
        return [uid for uid, slot in side["chosen"].items() if slot in slots and uid in players]

    for attack, defend in ((home, away), (away, home)):
        for wide, back in FLANKS:
            for a in pick(attack, wide):
                for d in pick(defend, back):
                    pa, pd = players[a], players[d]
                    speed = _mean_of(pa, ("Pace", "Acceleration")) - _mean_of(pd, ("Pace", "Acceleration"))
                    skill = pa.get("Dribbling", 0) - pd.get("Tackling", 0)
                    edge = max(speed, skill, key=abs)
                    if abs(edge) >= DUEL_EDGE:
                        who = attack["names"][a] if edge > 0 else defend["names"][d]
                        what = "pace" if edge == speed else ("dribbling against tackling" if edge > 0 else "tackling against dribbling")
                        found.append((abs(edge), f"{attack['names'][a]} ({SLOT_WORDS[attack['chosen'][a]]}: "
                                                 f"{_figures(pa, ('Pace', 'Acceleration', 'Dribbling'))}) against {defend['names'][d]} "
                                                 f"({SLOT_WORDS[defend['chosen'][d]]}: {_figures(pd, ('Pace', 'Acceleration', 'Tackling'))}); "
                                                 f"the edge in {what}: {who}"))
        backs = pick(defend, CENTRE_BACKS)
        for s in pick(attack, ("ST (C)",)):
            if not backs:
                continue
            ps = players[s]
            air = ("Heading", "Jumping Reach", "Strength")
            best = max(backs, key=lambda d: _mean_of(players[d], air))
            gap = _mean_of(ps, air) - _mean_of(players[best], air)
            if abs(gap) >= DUEL_EDGE:
                found.append((abs(gap), f"In the air: {attack['names'][s]} ({ps.get('height')} cm, {_figures(ps, air)}) against "
                                        f"{defend['names'][best]} ({players[best].get('height')} cm, {_figures(players[best], air)}); "
                                        f"the edge: {attack['names'][s] if gap > 0 else defend['names'][best]}"))
            quick = ("Pace", "Acceleration")
            slowest = min(backs, key=lambda d: _mean_of(players[d], quick))
            run = _mean_of(ps, (*quick, "Off The Ball")) - _mean_of(players[slowest], (*quick, "Anticipation"))
            if run >= DUEL_EDGE:
                found.append((run, f"In behind: {attack['names'][s]} ({_figures(ps, (*quick, 'Off The Ball'))}) against "
                                   f"{defend['names'][slowest]} ({_figures(players[slowest], (*quick, 'Anticipation'))})"))
    return [text for _, text in sorted(found, key=lambda item: -item[0])[:DUELS_SHOWN]]



# -- big games -----------------------------------------------------------------------------------------------------------
STRONGER = 500  # reputation points (0-10 000) that make an opponent stronger or weaker
BIG_GAME_DAYS = 365
BIG_GAME_MIN = 2  # matches against that kind of opponent
BIG_GAME_SHARE = 0.6  # ... in one shape that is not the usual one


def _opponent_kind(own: int | None, other: int | None) -> str | None:
    if own is None or other is None:
        return None
    return "stronger" if other >= own + STRONGER else "weaker" if other <= own - STRONGER else "similar"


def big_game_system(connection: Connection, team: int, opponent: int, day: date) -> dict | None:
    """The shape the team plays against opponents like this one (stronger, similar or weaker by the game's reputation), when
    it is not its usual one: a team that goes to a back five against the big clubs. Over its competitive matches of the last
    year with a team sheet, under its current manager. None when it plays its usual shape against them too, or has too few
    such matches."""
    reputation = {r["fm_uid"]: r["reputation"] for r in q._rows(connection, "select fm_uid, reputation from teams where fm_uid in (:t, :o)",
                                                                 t=team, o=opponent)}
    kind = _opponent_kind(reputation.get(team), reputation.get(opponent))
    if kind is None:
        return None
    # a habit is the manager's: only the matches since the current one took over
    manager = q._one(connection, "select staff_since from people where staff_job = 'Manager' and staff_team_uid = :t "
                                 "order by staff_since desc limit 1", t=team)
    since = day - timedelta(days=BIG_GAME_DAYS)
    if manager and manager["staff_since"] and str(manager["staff_since"]) > str(since):
        since = date.fromisoformat(str(manager["staff_since"])[:10])
    rows = q._rows(connection, "select f.id, case when f.home_uid = :t then 0 else 1 end side, case when f.home_uid = :t then f.away_uid "
                               "else f.home_uid end opponent, t.reputation from fixtures f join competitions c on c.fm_uid = f.competition_uid "
                               "left join teams t on t.fm_uid = case when f.home_uid = :t then f.away_uid else f.home_uid end "
                               "where (f.home_uid = :t or f.away_uid = :t) and f.date < :d and f.date >= :s and f.home_goals is not null "
                               "and c.scope in ('league', 'cup', 'continental')", t=team, d=str(day), s=str(since))
    slots = defaultdict(list)
    for r in q._rows(connection, f"select fixture_id, side, start_position from match_players where fixture_id in "
                                 f"({','.join(str(r['id']) for r in rows) or '0'}) and start_position is not null"):
        slots[(r["fixture_id"], r["side"])].append(r["start_position"])
    overall, against = Counter(), Counter()
    for r in rows:
        shape = formation(slots.get((r["id"], r["side"]), []))
        if not shape:
            continue
        overall[shape] += 1
        if _opponent_kind(reputation[team], r["reputation"]) == kind:
            against[shape] += 1
    if not overall or sum(against.values()) < BIG_GAME_MIN:
        return None
    usual, usual_count = overall.most_common(1)[0]
    shape, count = against.most_common(1)[0]
    if shape == usual or count < BIG_GAME_SHARE * sum(against.values()):
        return None
    return {"kind": kind, "shape": shape, "count": count, "games": sum(against.values()), "usual": usual, "usual_count": usual_count,
            "all_games": sum(overall.values()),
            "words": f"Against {kind} sides they play {shape} ({count} of {sum(against.values())} games); their usual is {usual} "
                     f"({usual_count} of {sum(overall.values())})"}


# -- danger men and form ---------------------------------------------------------------------------------------------------
FORM_GAMES = 5  # a team's last matches with a report
FORM_MIN = 3  # a player's appearances in them before his form counts
IN_FORM, OUT_OF_FORM = 7.3, 6.5  # average ratings
DANGER_MEN = 2


def danger_and_form(connection: Connection, team: int, day: date, out: set = frozenset()) -> dict:
    """A side's danger men and its players in and out of form, from its last FORM_GAMES competitive matches with a report
    before ``day``: goals, assists, expected goals, shots, chances made and key passes, and each match's rating, oldest
    first. Players still at the club and not in ``out`` (injured, gone)."""
    fixtures = q._rows(connection, "select f.id, case when f.home_uid = :t then 0 else 1 end side from fixtures f join competitions c "
                                   "on c.fm_uid = f.competition_uid where (f.home_uid = :t or f.away_uid = :t) and f.date < :d "
                                   "and f.home_goals is not null and c.scope in ('league', 'cup', 'continental') and exists "
                                   "(select 1 from match_players m where m.fixture_id = f.id) order by f.date desc limit :n",
                       t=team, d=str(day), n=FORM_GAMES)
    order = {f["id"]: i for i, f in enumerate(reversed(fixtures))}
    side_of = {f["id"]: f["side"] for f in fixtures}
    players: dict[int, dict] = {}
    for r in q._rows(connection, "select m.*, p.name, p.team_uid now_at from match_players m join people p on p.fm_uid = m.person_uid "
                                 f"where m.fixture_id in ({','.join(str(f['id']) for f in fixtures) or '0'}) and m.played = 1"):
        if r["side"] != side_of[r["fixture_id"]] or r["now_at"] != team or r["person_uid"] in out:
            continue
        p = players.setdefault(r["person_uid"], {"name": r["name"], "games": [], "goals": 0, "assists": 0, "xg": 0.0, "shots": 0,
                                                 "chances": 0, "key_passes": 0, "slots": Counter()})
        p["games"].append((order[r["fixture_id"]], r["rating"]))
        for name, field in (("goals", "goals"), ("assists", "assists"), ("xg", "xg"), ("shots", "shots"),
                            ("chances", "clear_cut_chances"), ("key_passes", "key_passes")):
            p[name] += r[field] or 0
        if r["start_position"] or r["end_position"]:
            p["slots"][r["start_position"] or r["end_position"]] += 1
    injured = q.current_injuries(connection, list(players))
    for uid in injured:
        players.pop(uid, None)

    def ratings(p: dict) -> list[float]:
        return [round(rating, 1) for _, rating in sorted(p["games"]) if rating]

    def who(p: dict) -> str:
        slot = p["slots"].most_common(1)[0][0] if p["slots"] else None
        return f"{p['name']} ({SLOT_WORDS[slot]})" if slot in SLOT_WORDS else p["name"]

    danger = sorted(players.values(), key=lambda p: -(3 * p["xg"] + p["goals"] + p["assists"] + 1.5 * p["chances"] + 0.5 * p["key_passes"]))
    danger_men = []
    for p in danger[:DANGER_MEN]:
        if not (p["goals"] or p["assists"] or p["xg"] >= 1):
            continue
        parts = [f"{p['goals']} goal{'s' if p['goals'] != 1 else ''}", f"{p['assists']} assist{'s' if p['assists'] != 1 else ''}",
                 f"{p['xg']:.1f} expected goals", f"{p['shots']} shots"]
        if p["chances"]:
            parts.append(f"{p['chances']} clear-cut chance{'s' if p['chances'] != 1 else ''} made")
        danger_men.append(f"{who(p)}: {', '.join(parts)} in his last {len(p['games'])} games; ratings {', '.join(map(str, ratings(p)))}")
    in_form, out_of_form = [], []
    for p in sorted(players.values(), key=lambda p: -(_avg(ratings(p)) or 0)):
        marks = ratings(p)
        if len(marks) < FORM_MIN:
            continue
        average = _avg(marks)
        line = f"{who(p)}: average {average} over his last {len(marks)} games ({', '.join(map(str, marks))})"
        if average >= IN_FORM:
            in_form.append(line)
        elif average <= OUT_OF_FORM:
            out_of_form.append(line)
    return {"danger_men": danger_men, "in_form": in_form[:3], "out_of_form": out_of_form[:2]}


def player_form(connection: Connection, team: int, uids, since: str, games: int = FORM_GAMES) -> dict[int, dict]:
    """For a squad: each player's ratings in his last ``games`` matches with a report for the team (oldest first), and the
    slot he has started in most since ``since`` (the position he really plays, not his profile's)."""
    marks = ",".join(str(int(u)) for u in uids) or "0"
    found: dict[int, dict] = {}
    for r in q._rows(connection, "select m.person_uid, m.rating, m.start_position, f.date from match_players m join fixtures f on f.id = m.fixture_id "
                                 "join competitions c on c.fm_uid = f.competition_uid where m.person_uid in (" + marks + ") and m.played = 1 "
                                 "and ((f.home_uid = :t and m.side = 0) or (f.away_uid = :t and m.side = 1)) and c.scope in "
                                 "('league', 'cup', 'continental') and f.date >= :s order by f.date", t=team, s=since):
        p = found.setdefault(r["person_uid"], {"ratings": [], "slots": Counter()})
        if r["rating"]:
            p["ratings"].append(round(r["rating"], 1))
        if r["start_position"]:
            p["slots"][r["start_position"]] += 1
    for p in found.values():
        p["ratings"] = p["ratings"][-games:]
        p["slot"] = p["slots"].most_common(1)[0][0] if p["slots"] else None
    return found


# -- the last meeting ------------------------------------------------------------------------------------------------------
def last_meeting(connection: Connection, home: int, away: int, before: date, elevens: dict | None = None) -> dict | None:
    """The last match between two teams with team sheets before a day: the score, each side's formation and eleven, the
    goals and the player of the match, and (given the expected elevens, {team: expected_eleven}) who from that day's
    eleven is gone, injured or out of the expected eleven now, and who is new in it."""
    f = q._one(connection, "select f.*, c.name competition from fixtures f left join competitions c on c.fm_uid = f.competition_uid "
                           "where ((f.home_uid = :h and f.away_uid = :a) or (f.home_uid = :a and f.away_uid = :h)) and f.date < :d "
                           "and f.home_goals is not null and exists (select 1 from match_players m where m.fixture_id = f.id "
                           "and m.start_position is not null) order by f.date desc limit 1", h=home, a=away, d=str(before))
    if f is None:
        return None
    sides = match_sides(connection, f["id"])
    names = {r["fm_uid"]: r["name"] for r in q._rows(connection, "select fm_uid, name from teams where fm_uid in (:h, :a)", h=home, a=away)}
    goals = q._rows(connection, "select g.minute, g.added_time, g.side, g.kind, p.name from goals g left join people p on p.fm_uid = g.scorer_uid "
                                "where g.fixture_id = :f and g.kind in (0, 1, 2) order by g.minute, g.seq", f=f["id"])
    pom = q._one(connection, "select p.name from match_reports r join people p on p.fm_uid = r.player_of_the_match_uid where r.fixture_id = :f",
                 f=f["id"])
    score = (f"{f['home_goals_after_extra_time']}-{f['away_goals_after_extra_time']} after extra time"
             if f["home_goals_after_extra_time"] is not None else f"{f['home_goals']}-{f['away_goals']}")
    found = {"match": f"{f['date']}, {f['competition'] or 'a friendly'}: {names.get(f['home_uid'])} {score} {names.get(f['away_uid'])}",
             "goals": [f"{('own goal by ' if g['kind'] == 1 else '') + (g['name'] or 'unknown')} {g['minute']}"
                       f"{'+' + str(g['added_time']) if g['added_time'] else ''}'{' (pen)' if g['kind'] == 2 else ''} "
                       f"({names.get(f['home_uid'] if g['side'] == 0 else f['away_uid'])})" for g in goals] or None,
             "player_of_the_match": pom["name"] if pom else None, "sides": {}}
    for side in sides:
        team = side["team_uid"]
        then = {r["person_uid"]: r["name"] or "unknown" for r in side["starters"] if r["person_uid"]}  # a starter the people table lacks (1513, 21 Dec 2063)
        row = {"shape": side["shape"], "eleven": line_up(side)}
        eleven = (elevens or {}).get(team)
        if eleven:
            now = set(eleven["chosen"])
            gone = [f"{name} ({eleven['out'][uid]})" if uid in eleven["out"] else name for uid, name in then.items() if uid not in now]
            # those still at the club and fit are simply not in the likely eleven: say so
            gone = [g if "(" in g else f"{g} (not in the likely eleven)" for g in gone]
            new = [eleven["names"][uid] for uid in eleven["chosen"] if uid not in then]
            row["not_in_the_expected_eleven_now"] = gone or None
            row["new_in_the_expected_eleven"] = new or None
        found["sides"][names.get(team)] = {k: v for k, v in row.items() if v is not None}
    return {k: v for k, v in found.items() if v is not None}



# -- the game's own partnerships (the human club) -----------------------------------------------------------------------
def game_partnerships(connection: Connection, team: int) -> list[dict]:
    """The game's record of the team's pairs (only the human club has one), the most games together first: both names,
    games, average rating together, goals for and against, and whether one of them has left. A pair in several role
    combinations is added up."""
    pairs: dict[tuple, dict] = {}
    for r in q._rows(connection, "select g.*, a.name name_a, b.name name_b from partnerships g left join people a on a.fm_uid = g.person_a "
                                 "left join people b on b.fm_uid = g.person_b where g.team_uid = :t", t=team):
        key = tuple(sorted((r["person_a"], r["person_b"])))
        p = pairs.setdefault(key, {"players": key, "names": (r["name_a"], r["name_b"]), "games": 0, "rating_sum": 0, "goals_for": 0,
                                   "goals_against": 0, "departed": bool(r["departed"])})
        for name, field in (("games", "matches"), ("rating_sum", "rating_sum"), ("goals_for", "goals_for"), ("goals_against", "goals_against")):
            p[name] += r[field]
    for p in pairs.values():
        p["average_rating"] = round(p["rating_sum"] / p["games"] / 10, 2) if p["games"] else None
        p["words"] = (f"{p['games']} games together (the game's record), average rating {p['average_rating']}, "
                      f"goals {p['goals_for']}-{p['goals_against']}")
    return sorted(pairs.values(), key=lambda p: -p["games"])
