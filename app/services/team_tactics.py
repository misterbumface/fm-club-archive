"""A club's tactics from the world database (team_tactics, team_tactic_slots): the system it plays now, as the human
manager's first tactic slot has it, and the systems its match files show it used over the years. For the public club
page and the previews. Read only; the importer writes the tables (save_import._write_tactics)."""

from __future__ import annotations

from datetime import date

from sqlalchemy.engine import Connection

from app.services import world_queries as q
from app.services.fm_tactics import POSITIONS, ROLES

LINES = (("GK",), ("SW", "D (R)", "D (C)", "D (L)", "WB (R)", "WB (L)"), ("DM",), ("M (R)", "M (C)", "M (L)"), ("AM (R)", "AM (C)", "AM (L)"),
         ("ST (C)",))
SHORT = {"GK": "GK", "SW": "SW", "D (R)": "DR", "D (L)": "DL", "D (C)": "DC", "WB (R)": "WBR", "WB (L)": "WBL", "DM": "DM", "M (R)": "MR",
         "M (L)": "ML", "M (C)": "MC", "AM (R)": "AMR", "AM (L)": "AML", "AM (C)": "AMC", "ST (C)": "ST"}
HISTORY_GAMES = 2  # a system seen in fewer dated matches is noted, not a spell


def _slots(c: Connection, tactic_id: int) -> list[dict]:
    return q._rows(c, "select s.*, p.name from team_tactic_slots s left join people p on p.fm_uid = s.person_uid "
                      "where s.tactic_id = :t order by s.seq", t=tactic_id)


def _order(slot: dict) -> tuple:
    """Slots in the game's order: goalkeeper first, then the lines, right to left within a line."""
    side = {"right": 0, None: 1, "left": 2}[slot["side"]]
    return POSITIONS.index(slot["position"]) if slot["position"] in POSITIONS else 99, side


def current(c: Connection, team_uid: int) -> dict | None:
    """The system the club plays now: the human manager's first slot, with the players' personalised roles applied to the
    slot each has one for. None for a club without one (every club but the human's)."""
    row = q._one(c, "select * from team_tactics where team_uid = :t and slot = 1", t=team_uid)
    if row is None:
        return None
    rows = _slots(c, row["id"])
    slots = sorted([s for s in rows if s["person_uid"] is None], key=_order)
    personal = [s for s in rows if s["person_uid"] is not None]
    for slot in slots:
        slot["code"], slot["short"] = _code(slot), _short(slot)
        slot["players"] = [{"person_uid": p["person_uid"], "name": p["name"], "role": p["role"], "role_key": p["role_key"], "duty": p["duty"],
                            "short": _short(p)}
                           for p in personal if (p["position"], p["side"]) == (slot["position"], slot["side"])]
    _place(slots)
    return {**row, "slots": slots, "lines": _lines(slots)}


def _place(slots: list[dict]) -> None:
    """An (x, y) per slot on a pitch attacking upwards (the public site's pitch): slots of one position side by side,
    the left one on the left."""
    by_spot: dict[str, list[dict]] = {}
    for s in slots:
        by_spot.setdefault(s["position"] if s["position"] in q.PITCH_SPOTS else "M (C)", []).append(s)
    for spot, group in by_spot.items():
        x, y = q.PITCH_SPOTS[spot]
        group.sort(key=lambda s: {"left": 0, None: 1, "right": 2}[s["side"]])
        for index, s in enumerate(group):
            s["x"], s["y"] = max(9, min(91, x + (index - (len(group) - 1) / 2) * q.PITCH_SPREAD)), y


def _code(slot: dict) -> str:
    """The game's slot code: "DCR", "AML", "ST"."""
    side = {"right": "R", "left": "L", None: ""}[slot["side"]]
    return SHORT.get(slot["position"], slot["position"]) + side


def _short(slot: dict) -> str:
    role = ROLES.get(slot["role_key"], (None, None))[1] or "?"
    return f"{role}-{(slot['duty'] or '?')[:2]}"


def _lines(slots: list[dict]) -> list[list[dict]]:
    """The slots by line, the strikers first (a pitch attacking upwards)."""
    lines = [[s for s in slots if s["position"] in line] for line in LINES]
    return [line for line in reversed(lines) if line]


def words(system: dict) -> str:
    """The system in words for the writers: "4-3-3 DM Wide, Tiki-Taka, Positive, Flexible: GK Sweeper Keeper (Support),
    DR Wing Back (Support), ..."."""
    head = ", ".join(x for x in (system.get("formation"), system.get("style"), system.get("mentality"), system.get("fluidity")) if x)
    roles = "; ".join(f"{s['code']} {s['role'] or 'role ' + s['role_key']} ({s['duty']})" for s in system["slots"])
    personal = "; ".join(f"{p['name']} plays as {p['role'] or 'role ' + p['role_key']} ({p['duty']}) at {s['code']}"
                         for s in system["slots"] for p in s["players"] if p["name"] and (p["role"], p["duty"]) != (s["role"], s["duty"]))
    return f"{head}: {roles}" + (f". Personalised: {personal}" if personal else "")


def history(c: Connection, team_uid: int) -> list[dict]:
    """The systems the club used in its dated matches, as spells: the same name and style in matches in a row, with the
    first and last date and the number of those matches (only the matches whose files the save still keeps)."""
    rows = q._rows(c, "select t.*, f.home_uid, f.away_uid, f.home_goals, f.away_goals from team_tactics t join fixtures f on f.id = t.fixture_id "
                      "where t.team_uid = :t and t.fixture_id is not null order by t.played_on", t=team_uid)
    spells: list[dict] = []
    for r in rows:
        key = (r["name"], r["style"])
        if spells and (spells[-1]["name"], spells[-1]["style"]) == key:
            spells[-1]["last"], spells[-1]["games"] = r["played_on"], spells[-1]["games"] + 1
        else:
            spells.append({"name": r["name"], "style": r["style"], "mentality": r["mentality"], "formation": r["formation"],
                           "first": r["played_on"], "last": r["played_on"], "games": 1})
    for s in spells:
        s["first_label"], s["last_label"] = q.season_label(_season(s["first"])), q.season_label(_season(s["last"]))
    return spells


def _season(day) -> int:
    when = day if isinstance(day, date) else date.fromisoformat(str(day)[:10])
    return when.year if when.month >= 7 else when.year - 1


def history_words(spells: list[dict]) -> str | None:
    """The spells in words for the writers, oldest first: "Gegenpress with GenGen 3 Attacking (4-3-3 DM Wide) in 2035/36;
    ..."."""
    if not spells:
        return None
    parts = []
    for s in spells:
        when = s["first_label"] if s["first_label"] == s["last_label"] else f"{s['first_label']} to {s['last_label']}"
        style = f"{s['style']} with " if s["style"] else ""
        parts.append(f"{style}{s['name']} ({s['formation'] or 'shape unknown'}) in {when}")
    return "; ".join(parts)
