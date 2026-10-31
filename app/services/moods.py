"""Morale, happiness, concerns and the dressing room, as the newest save has them (fm_save.Concern, Feeling,
read_social_groups; the import keeps them in person_moods, person_concerns, person_feelings and social_groups).

Only what the save shows is told: the game's words for morale and happiness where its screens have shown them (a number
otherwise), the concerns it names, and a concern's stage only when it is sure: "told his team-mates" (the game's
complaining stage) and team-mates taking his side (their own "treated unfairly" concerns about him). The game's
"(In public domain)" label has no proven byte yet.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.services import fm_save

RECENT_DAYS = 365  # an ended concern still worth telling
RECENT_STAGE_DAYS = 14  # a concern's stage (the newest save's) is told for a day this close to the save's date
NOT_FIT = 8500  # condition below this (85%) is told in a preview
NOT_SHARP = 7500  # match sharpness below this (75%)
TIRED = 200  # fatigue from this on (-500 fresh .. +500 jaded)
JADED = 400
FAVOURED = fm_save.FAVOURED_LEVEL


# Words for the morale levels whose game words are not known yet: a story says "his morale is poor", never "seven out of
# twenty" (31 Oct 2026: three printed stories gave the number, which breaks the world).
_MORALE_FILL = ((15, "Very Good"), (9, "Fair"), (6, "Poor"), (3, "Very Poor"), (0, "Abysmal"))


def morale_word(value: int | None) -> str | None:
    if value is None:
        return None
    return fm_save.MORALE_WORDS.get(value) or next(word for floor, word in _MORALE_FILL if value >= floor)


def happiness_word(value: int | None) -> str | None:
    if value is None:
        return None
    return next((word for limit, word in fm_save.HAPPINESS_WORDS if value >= limit), "Not happy")


def morale_tone(value: int | None) -> str:
    """A colour for the morale: good (15+), fair (9-14), low."""
    if value is None:
        return ""
    return "good" if value >= 15 else "fair" if value >= 9 else "low"


def _rows(connection: Connection, sql: str, **params) -> list[dict]:
    return [dict(row) for row in connection.execute(text(sql), params).mappings()]


def _marks(uids) -> str:
    return ",".join(str(int(u)) for u in uids) or "0"


def _names(connection: Connection, table: str, uids) -> dict[int, str]:
    return {r["fm_uid"]: r["name"] for r in _rows(connection, f"select fm_uid, name from {table} where fm_uid in ({_marks(set(uids) - {None})})")}


def _game_date(connection: Connection) -> date | None:
    value = connection.execute(text("select max(as_of) from person_moods")).scalar()
    return date.fromisoformat(value) if isinstance(value, str) else value


def describe(rows: list[dict], connection: Connection) -> list[dict]:
    """The named concerns among ``rows`` (person_concerns rows) with their words; the others are left out."""
    named = [r for r in rows if (r["kind"], r["code"]) in fm_save.CONCERNS]
    people = _names(connection, "people", (r["about_person_uid"] for r in named))
    competitions = _names(connection, "competitions", (r["competition_uid"] for r in named))
    nations = _names(connection, "nations", (r["nation_uid"] for r in named))
    found = []
    for r in named:
        words = fm_save.CONCERNS[(r["kind"], r["code"])].format(
            person=people.get(r["about_person_uid"], "a team-mate"), competition=competitions.get(r["competition_uid"], "the"),
            nation=nations.get(r["nation_uid"], "his home country"))
        found.append({**r, "text": words, "about_name": people.get(r["about_person_uid"]),
                      "told_team_mates": r["stage"] == fm_save.COMPLAINING, "code_key": (r["kind"], r["code"])})
    return found


def backers(connection: Connection, uids) -> dict[int, list[dict]]:
    """The people who now think each of ``uids`` has been treated unfairly (open concerns about him)."""
    kind, code = fm_save.TREATED_UNFAIRLY
    found: dict[int, list[dict]] = defaultdict(list)
    for r in _rows(connection, "select c.about_person_uid about, c.person_uid uid, p.name, c.started from person_concerns c "
                               f"join people p on p.fm_uid = c.person_uid where c.about_person_uid in ({_marks(uids)}) "
                               "and c.kind = :kind and c.code = :code and c.ended is null order by c.started, p.name", kind=kind, code=code):
        found[r["about"]].append(r)
    return found


DISLIKE_LEVEL = 10  # a dislike this strong is worth a word (the game lists likes from FAVOURED on)
PEOPLE_EACH = 6
# the reasons worth a line first (Parkins' six at full strength were four backroom staff, and the coach who brought him through fell out)
REASON_WEIGHT = {2: 0, 9: 1, 10: 2, 11: 3, 6: 4, 7: 5, 5: 6, 8: 7}


def people_in_his_life(connection: Connection, uid: int) -> dict[str, list[str]] | None:
    """For a writer: whom a person likes and dislikes, and who likes and dislikes him, each with the reason the game keeps
    (a team-mate, his manager, a friend, an idol, family, a coach who brought him through), the strongest first. Works for
    the retired too, who have feelings but no morale."""
    def words(rows: list[dict]) -> list[str]:
        rows = sorted(rows, key=lambda r: (-r["level"], REASON_WEIGHT.get(r["reason"], 9), r["name"]))
        return [f"{r['name']}" + (f" ({fm_save.RELATION_REASONS[r['reason']]})" if r["reason"] in fm_save.RELATION_REASONS else "")
                for r in rows[:PEOPLE_EACH]]

    his = _rows(connection, "select p.name, f.likes, f.reason, f.level from person_feelings f join people p on p.fm_uid = f.other_uid "
                            "where f.person_uid = :u order by f.level desc, p.name", u=uid)
    theirs = _rows(connection, "select p.name, f.likes, f.reason, f.level from person_feelings f join people p on p.fm_uid = f.person_uid "
                               "where f.other_uid = :u order by f.level desc, p.name", u=uid)
    found = {"close_to": words([r for r in his if r["likes"] and r["level"] >= FAVOURED]),
             "dislikes": words([r for r in his if not r["likes"] and r["level"] >= DISLIKE_LEVEL]),
             "liked_by": words([r for r in theirs if r["likes"] and r["level"] >= FAVOURED]),
             "disliked_by": words([r for r in theirs if not r["likes"] and r["level"] >= DISLIKE_LEVEL])}
    found = {k: v for k, v in found.items() if v}
    return found or None


PRESS_DAYS = 60  # the press answers a person's background carries: this many game days back
PRESS_EACH = 4


def press_words(connection: Connection, uid: int, day: date | None = None) -> dict[str, list[str]] | None:
    """For a writer: what a manager said at press conferences lately and what others said about him, each as the game's
    headline for the answer with the date and the tone ("2063-12-19: Novaes Campos dismisses Carvalho's 'fluke result'
    (very negative)"). The spirit, not the words; what others said counts only where the headline names him."""
    today = day or _game_date(connection)
    since = str(today - timedelta(days=PRESS_DAYS)) if today else "0000"

    def line(r: dict) -> str:
        return f"{r['day']}: {r['headline']}" + (f" ({r['tone']})" if r["tone"] else "")

    said = _rows(connection, "select * from press_answers where speaker_uid = :u and subject_uid != :u and headline is not null "
                             "and day >= :s order by day desc", u=uid, s=since)
    about = _rows(connection, "select * from press_answers where subject_uid = :u and speaker_uid != :u and headline is not null "
                              "and names_subject = 1 and day >= :s order by day desc", u=uid, s=since)
    found = {"said_in_public": list(dict.fromkeys(line(r) for r in said))[:PRESS_EACH],
             "said_about_him": list(dict.fromkeys(line(r) for r in about))[:PRESS_EACH]}
    found = {k: v for k, v in found.items() if v}
    return found or None


def news_words(connection: Connection, uid: int, day: date | None = None) -> list[str] | None:
    """For a writer: the game's news about a person in the last PRESS_DAYS, as facts in words (never the game's text)."""
    today = day or _game_date(connection)
    since = str(today - timedelta(days=PRESS_DAYS)) if today else "0000"
    try:
        rows = _rows(connection, "select n.*, p.name person, o.name other_person, t.name team, ot.name other_team from news_items n "
                                 "left join people p on p.fm_uid = n.person_uid left join people o on o.fm_uid = n.other_person_uid "
                                 "left join teams t on t.fm_uid = n.team_uid left join teams ot on ot.fm_uid = n.other_team_uid "
                                 "where (n.person_uid = :u or n.other_person_uid = :u) and n.day >= :s order by n.day desc", u=uid, s=since)
    except Exception:  # noqa: BLE001 - no news items table yet
        return None
    lines = []
    for r in rows:
        mine = r["person_uid"] == uid
        if r["kind"] == "job_link" and mine:
            lines.append(f"{r['day']}: linked with the vacant job at {r['other_team']}" + (f" after {r['other_person']} left" if r["other_person"] else ""))
        elif r["kind"] == "board_pressure" and mine:
            lines.append(f"{r['day']}: the {r['team']} board said to be losing patience with him; the game against {r['other_team']} could decide his fate")
        elif r["kind"] == "danger_man":
            lines.append(f"{r['day']}: singled out {r['other_person']} ({r['team']}) as the man to watch before the match of {r['match_day']}" if mine
                         else f"{r['day']}: {r['person']} singled him out as the man to watch before the match of {r['match_day']}")
        elif r["kind"] == "national_scouting":
            lines.append(f"{r['day']}: went to watch {r['other_person']} for {r['team']}" if mine
                         else f"{r['day']}: watched by the {r['team']} manager, {r['person']}")
        elif r["kind"] == "transfer_rumour" and mine:
            lines.append(f"{r['day']}: {r['team']} reported to be interested, a deal of about £{(r['fee'] or 0) / 1e6:.0f}M")
    return list(dict.fromkeys(lines))[:PRESS_EACH + 2] or None


def frame_of_mind(connection: Connection, uid: int) -> dict | None:
    """For a writer: a player's morale and happiness in words, his open grievances and who backs him; None for the retired."""
    mood = person_mood(connection, uid)
    if not mood:
        return None
    found = {"morale": mood["morale_word"], "happiness": mood["happiness_word"],
             "open_grievances": [c["text"] + (" (he has told his team-mates)" if c["told_team_mates"] else "") for c in mood["open"]] or None,
             "backed_by": [b["name"] for b in mood["backers"]] or None}
    return {k: v for k, v in found.items() if v}


def person_mood(connection: Connection, uid: int) -> dict | None:
    """A player's morale and happiness, his open and recent concerns, who backs him, and the people he favours and
    dislikes; None when the newest save has no mood for him."""
    mood = next(iter(_rows(connection, "select morale, happiness, as_of from person_moods where person_uid = :u", u=uid)), None)
    if not mood or mood["morale"] is None:  # the game keeps a morale for players only
        return None
    today = _game_date(connection)
    since = today - timedelta(days=RECENT_DAYS) if today else date.min
    concerns = describe(_rows(connection, "select * from person_concerns where person_uid = :u order by started", u=uid), connection)
    feelings = _rows(connection, "select f.other_uid uid, p.name, f.likes, f.reason, f.level from person_feelings f "
                                 "join people p on p.fm_uid = f.other_uid where f.person_uid = :u and f.level >= :level "
                                 "order by f.level desc, p.name", u=uid, level=FAVOURED)
    for f in feelings:
        f["reason_name"] = fm_save.RELATION_REASONS.get(f["reason"])
    return {
        **mood,
        "morale_word": morale_word(mood["morale"]), "morale_tone": morale_tone(mood["morale"]),
        "happiness_word": happiness_word(mood["happiness"]),
        "open": [c for c in concerns if c["ended"] is None],
        "recent": [c for c in concerns if c["ended"] is not None and str(c["ended"]) >= str(since)][-4:],
        "backers": backers(connection, [uid]).get(uid, []),
        "favoured": [f for f in feelings if f["likes"]],
        "dislikes": [f for f in feelings if not f["likes"]],
    }


def squad_moods(connection: Connection, uids) -> dict[int, dict]:
    """Morale and the open named concerns of each of ``uids`` (for squad tables)."""
    found = {r["person_uid"]: {**r, "morale_word": morale_word(r["morale"]), "morale_tone": morale_tone(r["morale"]), "open": []}
             for r in _rows(connection, f"select person_uid, morale, happiness from person_moods where person_uid in ({_marks(uids)}) "
                                        "and morale is not null")}
    for c in describe(_rows(connection, f"select * from person_concerns where person_uid in ({_marks(found)}) and ended is null "
                                        "order by started"), connection):
        found[c["person_uid"]]["open"].append(c)
    return found


def dressing_room(connection: Connection, team_uid: int) -> dict | None:
    """The human club's social groups with each member's morale and concerns, and the players the others side with."""
    members = _rows(connection, "select g.person_uid uid, g.group_number, p.name from social_groups g join people p on p.fm_uid = g.person_uid "
                                "where g.team_uid = :t order by g.group_number, g.seq", t=team_uid)
    if not members:
        return None
    moods = squad_moods(connection, [m["uid"] for m in members])
    groups: dict[int, list[dict]] = defaultdict(list)
    for m in members:
        groups[m["group_number"]].append({**m, **moods.get(m["uid"], {"open": []})})
    group_of = {m["uid"]: m["group_number"] for m in members}
    sides = []
    for about, people in backers(connection, group_of).items():
        sides.append({"uid": about, "name": next((m["name"] for m in members if m["uid"] == about), ""),
                      "own": moods.get(about, {}).get("open", []),
                      "own_words": "; ".join(c["text"][:1].lower() + c["text"][1:] for c in moods.get(about, {}).get("open", [])),
                      "backers": [{**p, "same_group": group_of.get(p["uid"]) == group_of[about]} for p in people]})
    sides.sort(key=lambda s: -len(s["backers"]))
    return {"groups": [groups[n] for n in sorted(groups)], "sides": sides}


def public_talk(connection: Connection, uid: int, day: date) -> list[str]:
    """What is talked about a player on ``day`` beyond himself: the concerns he has told his team-mates (only near the
    newest save's date, as the save keeps only a concern's stage now), the team-mates he backs, and the team-mates who
    back him. A concern he keeps to himself is left out."""
    talk = []
    open_then = "c.started <= :d and (c.ended is null or c.ended > :d)"
    rows = describe(_rows(connection, f"select * from person_concerns c where c.person_uid = :u and {open_then} order by c.started",
                          u=uid, d=str(day)), connection)
    today = _game_date(connection)
    recent = today is not None and abs((today - day).days) <= RECENT_STAGE_DAYS
    for c in rows:
        if c["code_key"] == fm_save.TREATED_UNFAIRLY:
            talk.append(f"thinks {c['about_name'] or 'a team-mate'} has been treated unfairly, since {c['started']}")
        elif recent and c["told_team_mates"]:
            talk.append(f"has told his team-mates: {c['text'][:1].lower() + c['text'][1:]}, since {c['started']}")
    kind, code = fm_save.TREATED_UNFAIRLY
    backing = _rows(connection, f"select p.name from person_concerns c join people p on p.fm_uid = c.person_uid where c.about_person_uid = :u "
                                f"and c.kind = :k and c.code = :c and {open_then} order by c.started", u=uid, k=kind, c=code, d=str(day))
    if backing:
        talk.append("team-mates who think he has been treated unfairly: " + ", ".join(r["name"] for r in backing))
    return talk


def fatigue_word(value: int | None) -> str | None:
    """The archive's words for fatigue (-500 completely fresh .. +500 completely jaded; the game shows -151 as "Fresh")."""
    if value is None:
        return None
    return "fresh" if value < 0 else "some fatigue" if value < TIRED else "tired" if value < JADED else "jaded"


def fitness_notes(connection: Connection, names: dict[int, str], day: date) -> list[str]:
    """For the players ``names`` (uid -> name) expected in a match on ``day``: who is not fully fit, short of match
    sharpness or tired, from the newest save, when it is close enough to the day."""
    today = _game_date(connection)
    if today is None or abs((day - today).days) > RECENT_STAGE_DAYS:
        return []
    notes = []
    for r in _rows(connection, f"select person_uid, sharpness, fatigue, condition from person_moods where person_uid in ({_marks(names)})"):
        found = []
        if r["condition"] is not None and r["condition"] < NOT_FIT:
            found.append(f"condition {r['condition'] / 100:.0f}%")
        if r["sharpness"] is not None and r["sharpness"] < NOT_SHARP:
            found.append(f"short of match sharpness ({r['sharpness'] / 100:.0f}%)")
        if r["fatigue"] is not None and r["fatigue"] >= TIRED:
            found.append(fatigue_word(r["fatigue"]))
        if found:
            notes.append(f"{names[r['person_uid']]}: {', '.join(found)}")
    if notes:
        notes.append(f"(as of {today})")
    return notes
