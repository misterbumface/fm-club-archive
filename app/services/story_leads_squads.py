"""National squads (8 Nov 2026: "players with a lot of caps who get dropped, who the manager brings in to replace them, the
new shape against the old, how they react").

Each import keeps every national team's squad (national_squads, one copy per save date). The squad at the end of the cycle
set against the one before it says who came in and who was left out. Around that list go the facts a reporter would have: a
player's caps, his starts in the last competitive internationals (the team sheets), an injury, his club form, and his
character. The why is told only when something shows it; a player with no injury and good form has no reason given.

Two stories: "national_squad" (a country's new squad) and "squad_dropped" (one well-known international left out).
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from app.services import firepower, tactics
from app.services import world_queries as q
from app.services.story_leads_now import _comp_name, _manager, _result_line

STANDING = 5500  # the nations well known enough for a story when none of the followed clubs' players is in it
SHEETS, SHEET_DAYS = 10, 730  # the last competitive internationals with a team sheet that count towards "a regular"
FORM_GAMES, FORM_DAYS = 6, 75  # a player's last club games
POOR_FORM, FORM_MINIMUM = 6.7, 3  # an average club rating below this, over at least so many rated games
KNOWN_CAPS, DROPPED_CAPS = 20, 30  # caps that make a left-out player worth naming; and worth a story of his own
FINALS_DAYS, FINALS_REPUTATION = 75, 120  # a squad this close to a tournament of this reputation is the finals squad
NEXT_DAYS = 120
FINALS, COMPETITIVE, OTHER = 1.6, 1.0, 0.7  # how much the squad matters: for a finals, for a qualifier, for a friendly or nothing
SQUAD_SIZE, DROPPED_SIZE = 0.7, 0.75
UNKNOWN_WEIGHT = 3.5  # a friendly has no reputation
SHOWN_IN = 8  # at most so many new names in a story


def _squad(w, team: int, day) -> dict[int, str | None]:
    return {r["person_uid"]: r["role"] for r in w.rows("select person_uid, role from national_squads where team_uid = :t and as_of = :d", t=team, d=str(day))}


def _sheets(w, team: int, day: str) -> list[dict[int, str]]:
    """The starters and their positions in the team's last competitive games with a team sheet, newest first."""
    since = str(date.fromisoformat(day[:10]) - timedelta(days=SHEET_DAYS))
    found = []
    for f in w.rows("select f.id, f.home_uid from fixtures f join competitions c on c.fm_uid = f.competition_uid where (f.home_uid = :t or f.away_uid = :t) "
                    "and f.home_goals is not null and f.date <= :d and f.date >= :s and c.scope = 'national_teams' and c.name not like '%Friendl%' "
                    f"and exists (select 1 from match_players m where m.fixture_id = f.id) order by f.date desc limit {SHEETS}", t=team, d=day, s=since):
        found.append({r["person_uid"]: r["start_position"] for r in w.rows(
            "select person_uid, start_position from match_players where fixture_id = :f and side = :s and start_position is not null",
            f=f["id"], s=0 if f["home_uid"] == team else 1)})
    return found


def _position(w, uid: int, latest: dict | None) -> str | None:
    """Where he starts most (his team sheets); for a player without any, the position he is best in."""
    row = w.rows("select start_position p, count(*) n from match_players where person_uid = :u and start_position is not null group by 1 order by n desc limit 1", u=uid)
    if row:
        return row[0]["p"]
    positions = (latest or {}).get("positions") or {}
    return max(positions, key=positions.get) if positions else None


def _line(position: str | None) -> str | None:
    return next((name for name, slots in firepower.LINES.items() if position in slots), None)


def _injury(w, uid: int, day: str) -> str | None:
    row = w.rows("select injury, injured_on, days from injuries where person_uid = :u and injured_on <= :d "
                 "and (current = 1 or date(injured_on, '+' || days || ' days') >= :d) order by injured_on desc limit 1", u=uid, d=day)
    return f"{row[0]['injury']} (since {str(row[0]['injured_on'])[:10]})" if row else None


def _club_form(w, uid: int, day: str) -> dict:
    """His last club games on a team sheet: how many, how many he started, his average rating."""
    since = str(date.fromisoformat(day[:10]) - timedelta(days=FORM_DAYS))
    games = w.rows("select m.played, m.rating, m.start_position from match_players m join fixtures f on f.id = m.fixture_id "
                   "join competitions c on c.fm_uid = f.competition_uid where m.person_uid = :u and f.date <= :d and f.date >= :s "
                   f"and c.scope != 'national_teams' order by f.date desc limit {FORM_GAMES}", u=uid, d=day, s=since)
    rated = [g["rating"] for g in games if g["played"] and g["rating"]]
    return {"games": len(games), "started": sum(g["start_position"] is not None for g in games), "rated": len(rated),
            "average": round(sum(rated) / len(rated), 2) if rated else None}


def _reason(injury: str | None, form: dict) -> dict:
    """What shows why he is out, if anything does."""
    if injury:
        return {"injured": injury}
    if form["rated"] >= FORM_MINIMUM and form["average"] < POOR_FORM:
        return {"poor_club_form": f"average rating {form['average']:.2f} in his last {form['rated']} club games"}
    if form["games"] >= FORM_MINIMUM + 1 and form["started"] * 3 <= form["games"]:
        return {"losing_his_place_at_his_club": f"started {form['started']} of his last {form['games']} club games"}
    return {"no_reason_known": True}


def _person(w, uid: int, team: int, day: str, sheets: list[dict[int, str]], manager: int | None) -> dict | None:
    row = w.rows("select name, birth_date, team_uid, reputation_world, current_ability from people where fm_uid = :u", u=uid)
    if not row or not row[0]["team_uid"]:  # gone from the game, or without a club: not a selection story
        return None
    p, latest = row[0], q.latest_attributes(w.c, uid)
    caps = w.rows("select coalesce(sum(starts + substitute_appearances), 0) games, coalesce(sum(goals), 0) goals from player_seasons "
                  "where person_uid = :u and kind = 4", u=uid)[0]  # the save counts a cap at the club he was at
    position = _position(w, uid, latest)
    found = {"uid": uid, "name": p["name"], "club": w.name(p["team_uid"]), "club_uid": p["team_uid"], "ability": p["current_ability"] or 0,
             "position": position, "line": _line(position), "caps": caps["games"], "goals": caps["goals"],
             "starts": sum(uid in sheet for sheet in sheets), "character": q.character_words((latest or {}).get("traits"))[:3], "manager": manager}
    if p["birth_date"]:
        found["age"] = int((date.fromisoformat(day[:10]) - date.fromisoformat(str(p["birth_date"])[:10])).days / 365.25)
    if manager:
        feeling = w.rows("select likes, level from person_feelings where person_uid = :u and other_uid = :m order by level desc limit 1", u=uid, m=manager)
        if feeling:
            found["feeling"] = "gets on with the manager" if feeling[0]["likes"] else "does not get on with the manager"
    return found


def _regular(player: dict, sheets: list) -> bool:
    return player["starts"] >= 2 and player["starts"] * 2 >= len(sheets)


def _known(player: dict, sheets: list) -> bool:
    return player["caps"] >= KNOWN_CAPS or _regular(player, sheets)


def _caps_words(player: dict) -> str:
    if not player["caps"]:
        return "uncapped"
    return f"{player['caps']} cap{'s' if player['caps'] != 1 else ''}, {player['goals']} international goal{'s' if player['goals'] != 1 else ''}"


def _words(player: dict, sheets: list) -> dict:
    """A player for a notebook."""
    words = {"name": player["name"], "age": player.get("age"), "club": player["club"], "position": player["position"],
             "caps": _caps_words(player),
             "character": ", ".join(player["character"]) or None, "with_the_manager": player.get("feeling")}
    if sheets and player["starts"]:
        words["starts_for_his_country"] = f"started {player['starts']} of the last {len(sheets)} competitive internationals"
    return {k: v for k, v in words.items() if v is not None}


def _left_out(w, player: dict, day: str, sheets: list) -> dict:
    injury, form = _injury(w, player["uid"], day), _club_form(w, player["uid"], day)
    return {**_words(player, sheets), **_reason(injury, form), "was_a_regular": _regular(player, sheets) or None}


def _by_line(w, people: list[dict]) -> Counter:
    return Counter(p["line"] for p in people if p and p["line"])


def _lines_words(counts: Counter) -> str:
    return ", ".join(f"{line} {counts.get(line, 0)}" for line in firepower.SHAPE)


def _shape(w, sheets: list, was: Counter, now: Counter, manager: int | None) -> dict:
    """The paper's material for a guess at a new plan: the shapes on the sheets, the squad's lines before and now, the manager's own."""
    shapes = [s for s in (tactics.formation(list(sheet.values())) for sheet in sheets) if s]
    facts = {"squad_by_line_now": _lines_words(now), "squad_by_line_before": _lines_words(was)}
    if shapes:
        usual, n = Counter(shapes).most_common(1)[0]
        facts["usual_shape"] = f"{usual} (in {n} of the last {len(shapes)} competitive games)"
        facts["last_game_shape"] = shapes[0]
    profile = q.manager_profile(w.c, manager) if manager else None
    if profile:
        facts["manager_prefers"] = profile.get("preferred_formation")
        facts["manager_style"] = profile.get("tactical_style")
    return {k: v for k, v in facts.items() if v}


def _what_for(w, team: int, before: str, now: str) -> tuple[dict, float, float]:
    """What the squad is for, how much it matters, and the competition's weight. A list that changed before games already played
    was named for those games, whatever comes next (Brazil's of March 2064 was not the Copa America squad, ten weeks early);
    only a list changed with no game since, a few weeks before a tournament, is the finals squad."""
    words: dict = {}
    coming = w.rows("select * from fixtures where (home_uid = :t or away_uid = :t) and home_goals is null and date > :u and date <= :e order by date limit 1",
                    t=team, u=str(w.until), e=str(w.until + timedelta(days=NEXT_DAYS)))
    if coming:
        words["next_game"] = f"{_comp_name(w, coming[0]['competition_uid'])}, {str(coming[0]['date'])[:10]}: {w.name(coming[0]['home_uid'])} v {w.name(coming[0]['away_uid'])}"
    played = w.rows("select * from fixtures where (home_uid = :t or away_uid = :t) and home_goals is not null and date > :b and date <= :n order by date desc limit 4",
                    t=team, b=before, n=now)
    if played:
        words["games_played_since_the_last_squad"] = [f"{_comp_name(w, f['competition_uid'])}, {_result_line(w, f)}" for f in played]
        competitive = [f for f in played if _is_competitive(w, f)]
        return words, COMPETITIVE if competitive else OTHER, max((w.weight(f["competition_uid"]) for f in competitive), default=UNKNOWN_WEIGHT)
    if not coming or not _is_competitive(w, coming[0]):
        return words, OTHER, UNKNOWN_WEIGHT
    comp, days = w.comp(coming[0]["competition_uid"]), (date.fromisoformat(str(coming[0]["date"])[:10]) - w.until).days
    name = comp.get("name") or ""
    if "Qualif" not in name and "Playoff" not in name and (comp.get("reputation") or 0) >= FINALS_REPUTATION and days <= FINALS_DAYS:
        return {**words, "the_finals_squad_for": name}, FINALS, w.weight(coming[0]["competition_uid"])
    return words, COMPETITIVE, w.weight(coming[0]["competition_uid"])


def _is_competitive(w, f: dict) -> bool:
    comp = w.comp(f["competition_uid"])
    return comp.get("scope") == "national_teams" and "Friendl" not in (comp.get("name") or "")


def _replacements(player: dict, incoming: list[dict], sheets: list) -> list[dict]:
    """The new names in his part of the team: his own position first, the strongest first."""
    same = [p for p in incoming if p["line"] and p["line"] == player["line"]]
    return [_words(p, sheets) for p in sorted(same, key=lambda p: (p["position"] != player["position"], -p["ability"]))[:3]]


def _a_team(w, team: int, now: str, before: str):
    """The two copies of the squad and what changed, or None for a nation the papers would not cover."""
    if team not in w.nations:  # a nation's senior team has the nation's own uid; the save has a few others under the same name
        return None
    new, old = _squad(w, team, now), _squad(w, team, before)
    came, went = set(new) - set(old), set(old) - set(new)
    captain_now = next((p for p, role in new.items() if role == "captain"), None)
    captain_was = next((p for p, role in old.items() if role == "captain"), None)
    if not came and not went and captain_now == captain_was:
        return None
    followed = {r["fm_uid"] for r in w.rows(f"select fm_uid from people where fm_uid in ({','.join(map(str, came | went)) or '0'}) "
                                            f"and team_uid in ({','.join(map(str, w.followed_teams)) or '0'})")}
    if (w.rep(team) or 0) < STANDING and not followed:
        return None
    return new, old, came, went, captain_now, captain_was, followed


def squads(w) -> list:
    from app.services.story_leads import Lead

    leads, dropped = [], []
    for r in w.rows("select team_uid, max(as_of) now, (select max(b.as_of) from national_squads b where b.team_uid = n.team_uid and b.as_of <= :s) before "
                    "from national_squads n where as_of > :s and as_of <= :u group by 1", s=str(w.since), u=str(w.until)):
        team, now, before = r["team_uid"], str(r["now"])[:10], r["before"] and str(r["before"])[:10]
        found = _a_team(w, team, now, before) if before else None  # the first copy of a squad is only the base
        if not found:
            continue
        new, old, came, went, captain_now, captain_was, followed = found
        sheets = _sheets(w, team, now)
        boss = w.rows("select fm_uid from people where staff_job = 'Manager' and staff_team_uid = :t limit 1", t=team)
        manager = boss[0]["fm_uid"] if boss else None
        people = {uid: _person(w, uid, team, now, sheets, manager) for uid in set(new) | set(old)}
        out = sorted((people[u] for u in went if people[u]), key=lambda p: (not _regular(p, sheets), -p["caps"], -p["ability"]))
        incoming = sorted((people[u] for u in came if people[u]), key=lambda p: -p["ability"])
        known = [p for p in out if _known(p, sheets)]
        captain_changed = captain_now != captain_was and captain_now and captain_was
        next_words, matters, weight = _what_for(w, team, before, now)
        why = {p["uid"]: _left_out(w, p, now, sheets) for p in known}
        if not known and not captain_changed and matters != FINALS and not followed:
            continue  # a change of the squad's last names before a friendly: not news
        name = w.name(team)
        fame = (w.rep(team) or 3000) / 8000
        new_manager = w.rows("select person_uid from manager_spells where team_uid = :t and ended > :b limit 1", t=team, b=before)  # the last man has gone
        shape = _shape(w, sheets, _by_line(w, [people[u] for u in old]), _by_line(w, [people[u] for u in new]), manager)
        facts = {"country": name, "manager": _manager(w, team), "a_new_manager_since_the_last_squad": True if new_manager else None, **next_words,
                 "left_out": [why[p["uid"]] for p in known] or None,
                 "others_no_longer_in_the_squad": [p["name"] for p in out if p not in known] or None,
                 "brought_in": [_words(p, sheets) for p in incoming[:SHOWN_IN]] or None,
                 "captain": people[captain_now]["name"] if captain_now and people.get(captain_now) else None,
                 "the_old_captain": people[captain_was]["name"] if captain_changed and people.get(captain_was) else None,
                 "the_shape": shape}
        refs = [f"club:{team}", f"nation:{w.team(team).get('nation_uid') or team}"]
        beats = w.beats_for(team) | ({"home"} if followed else set())
        boost = 1.3 if followed else 1.0
        score = weight * 10 * SQUAD_SIZE * fame * matters * boost * (1 + len(known) / 20)
        headline = f"{name}: the new squad, {len(came)} in and {len(went)} out" + (f", {known[0]['name']} left out" if known else "")
        leads.append(Lead("national_squad", headline, w.until, score, {k: v for k, v in facts.items() if v is not None},
                          refs + [f"person:{p['uid']}" for p in known[:4] + incoming[:2]], beats, slug=f"{team}:{now}"))
        star = next((p for p in known if p["caps"] >= DROPPED_CAPS and "injured" not in why[p["uid"]]), None)  # an injured man was not dropped
        if star and (matters != OTHER or _regular(star, sheets)):
            facts = {"country": name, "manager": _manager(w, team), **next_words, "left_out": why[star["uid"]],
                     "likely_replacements": _replacements(star, incoming, sheets) or None, "the_shape": shape}
            size = weight * 10 * DROPPED_SIZE * fame * matters * boost * (1 + min(star["caps"], 100) / 200)
            dropped.append(Lead("squad_dropped", f"{star['name']}, {star['caps']} caps, left out of the {name} squad", w.until, size,
                                {k: v for k, v in facts.items() if v is not None}, [f"person:{star['uid']}", *refs], beats, slug=f"{team}:{star['uid']}:{now}"))
    return sorted(leads, key=lambda lead: -lead.score)[:8] + sorted(dropped, key=lambda lead: -lead.score)[:4]
