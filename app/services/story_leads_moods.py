"""Stories from the dressing room (moods.py): unsettled players, team-mates taking sides, concerns that end.

For the players of the covered leagues' clubs (story_leads_now.current_leagues) and the followed clubs, from the dated
concerns the newest save keeps. Only what the save shows: the concern, when it began, whether he has told his team-mates
(the game's complaining stage) and which team-mates think he has been treated unfairly. Morale and happiness are the
newest save's, so they are told only for a window that ends near its date. story_leads.find_leads adds them.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from app.services import fm_save, moods
from app.services import story_leads_now as now
from app.services import world_queries as q

UNSETTLED = (fm_save.MOVE_ON, fm_save.NEW_DEAL, fm_save.RETURN_HOME, fm_save.MORE_GAMES, fm_save.LEFT_OUT)
UNSETTLED_NAME = 3000  # a player this well known (world reputation) with a concern is news; any at a followed club
SIDES = 2  # team-mates who think he has been treated unfairly before it is a story
SETTLED_DAYS = 21  # a concern that lasted this long, then ended, is a story
SETTLED_NAME = 5000
MOOD_DAYS = 14  # the newest save's morale belongs to a window that ends this close to its date
CONCERN_WEIGHT = {fm_save.MOVE_ON: 1.0, fm_save.NEW_DEAL: 0.8, fm_save.RETURN_HOME: 0.8, fm_save.LEFT_OUT: 0.7, fm_save.MORE_GAMES: 0.6}


def _teams(w) -> set[int]:
    if getattr(w, "_mood_teams", None) is None:
        teams = set(w.followed_teams)
        for league in now.current_leagues(w):
            start, end = now._window(w, league)
            teams |= {r["team_uid"] for r in q.league_table(w.c, league, start, end)}
        w._mood_teams = teams
    return w._mood_teams


def _day(value) -> date | None:
    return date.fromisoformat(str(value)[:10]) if value else None


def _concerns(w, where: str, **params) -> list[dict]:
    """Named concerns of the covered teams' players (``where`` on person_concerns c), with the player and his words."""
    marks = ",".join(str(int(t)) for t in _teams(w)) or "0"
    rows = w.rows(f"select c.*, p.name player, p.birth_date, p.team_uid, p.reputation_world from person_concerns c "
                  f"join people p on p.fm_uid = c.person_uid where p.team_uid in ({marks}) and ({where})", **params)
    return moods.describe(_first_team(w, [r for r in rows if (r["kind"], r["code"]) in fm_save.CONCERNS]), w.c)


def _first_team(w, rows: list[dict], uid: str = "person_uid", team: str = "team_uid") -> list[dict]:
    """The rows of players who are not youth-team players: one on a club's books who played this season only for its U21s
    or U18s is not a dressing-room story, nor a team-mate taking sides (31 Oct 2026)."""
    by_team: dict[int, set[int]] = defaultdict(set)
    for r in rows:
        by_team[r[team]].add(r[uid])
    youth: set[int] = set()
    for t, uids in by_team.items():
        youth |= q.youth_only(w.c, t, w.season, uids)
    return [r for r in rows if r[uid] not in youth]


def _mood_facts(w, uid: int) -> dict:
    row = next(iter(w.rows("select morale, happiness, as_of from person_moods where person_uid = :u", u=uid)), None)
    if not row or not row["as_of"] or abs((_day(row["as_of"]) - w.until).days) > MOOD_DAYS:
        return {}
    return {"morale": moods.morale_word(row["morale"]), "happiness": moods.happiness_word(row["happiness"])}


def _player_facts(w, c: dict) -> tuple[dict, int | None]:
    team = c["team_uid"]
    league, _ = w.level(team, w.season)
    games = next(iter(w.rows("select sum(starts) starts, sum(substitute_appearances) subs from player_seasons where person_uid = :u "
                             "and team_uid = :t and season_start = :s and kind = 1", u=c["person_uid"], t=team, s=w.season)), {})
    return {"player": c["player"], "club": w.name(team), "age": q.age_on(c["birth_date"], w.until),
            "league_games_this_season": f"{games.get('starts') or 0} starts, {games.get('subs') or 0} as a substitute" if games else None,
            "manager": now._manager(w, team), **_mood_facts(w, c["person_uid"])}, league


def unsettled(w) -> list:
    """A player whose concern began in the cycle and is still open at its end: he wants to move on, a new deal, to go home,
    more games, or was left out of a squad. Told when he is well known or at a followed club."""
    from app.services.story_leads import Lead

    leads = []
    for c in _concerns(w, "c.started > :s and c.started <= :u and (c.ended is null or c.ended > :u)", s=str(w.since), u=str(w.until)):
        if c["code_key"] not in UNSETTLED:
            continue
        star = c["reputation_world"] or 0
        if star < UNSETTLED_NAME and not w.followed(c["team_uid"]):
            continue
        facts, league = _player_facts(w, c)
        facts.update({"concern": c["text"], "since": str(c["started"]),
                      "told_his_team_mates": "yes" if c["told_team_mates"] else "not yet, as far as is known"})
        backers = moods.backers(w.c, [c["person_uid"]]).get(c["person_uid"], [])
        backers = _first_team(w, [{**b, "team_uid": c["team_uid"]} for b in backers], uid="uid")
        if backers:
            facts["team_mates_who_think_he_is_treated_unfairly"] = ", ".join(b["name"] for b in backers)
        size = CONCERN_WEIGHT.get(c["code_key"], 0.6) * (0.6 + star / 15000 + (0.2 if c["told_team_mates"] else 0))
        leads.append(Lead("unsettled", f"{c['player']}: {c['text'][:1].lower() + c['text'][1:]}", w.until, w.weight(league) * 10 * size,
                          {k: v for k, v in facts.items() if v is not None}, [f"person:{c['person_uid']}", f"club:{c['team_uid']}"],
                          w.beats_for(c["team_uid"], competition_uid=league), slug=f"{c['person_uid']}:{c['kind']}-{c['code']}:{c['started']}"))
    return sorted(leads, key=lambda lead: -lead.score)[:6]


def taking_sides(w) -> list:
    """Team-mates who think one player has been treated unfairly (their concerns about him, open at the cycle's end, at least
    one begun in it): who they are, whether they share his social group, and his own grievance."""
    from app.services.story_leads import Lead

    kind, code = fm_save.TREATED_UNFAIRLY
    marks = ",".join(str(int(t)) for t in _teams(w)) or "0"
    rows = w.rows(f"select c.about_person_uid about, c.person_uid uid, p.name, p.team_uid, c.started from person_concerns c "
                  f"join people p on p.fm_uid = c.person_uid where c.kind = :k and c.code = :c and p.team_uid in ({marks}) "
                  "and c.started <= :u and (c.ended is null or c.ended > :u)", k=kind, c=code, u=str(w.until))
    rows = _first_team(w, rows, uid="uid")
    sides: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        if r["about"]:
            sides[r["about"]].append(r)
    leads = []
    for about, backers in sides.items():
        if len(backers) < SIDES or not any(str(b["started"]) > str(w.since) for b in backers):
            continue
        person = next(iter(w.rows("select fm_uid, name, birth_date, team_uid, reputation_world from people where fm_uid = :u", u=about)), None)
        if not person:
            continue
        team = person["team_uid"]
        league, _ = w.level(team, w.season)
        groups = {r["person_uid"]: r["group_number"] for r in w.rows("select person_uid, group_number from social_groups where team_uid = :t", t=team)}
        own = _concerns(w, "c.person_uid = :u and c.started <= :until and (c.ended is null or c.ended > :until)", u=about, until=str(w.until))
        facts = {"player": person["name"], "club": w.name(team), "age": q.age_on(person["birth_date"], w.until),
                 "team_mates_who_think_he_has_been_treated_unfairly": ", ".join(b["name"] for b in backers),
                 "since": str(min(b["started"] for b in backers)), "manager": now._manager(w, team), **_mood_facts(w, about)}
        if own:
            facts["his_own_grievance"] = "; ".join(c["text"] + (" (he has told his team-mates)" if c["told_team_mates"] else "") for c in own)
        if groups.get(about):
            same = [b["name"] for b in backers if groups.get(b["uid"]) == groups[about]]
            others = [b["name"] for b in backers if b["uid"] in groups and groups.get(b["uid"]) != groups[about]]
            if same:
                facts["from_his_own_circle_in_the_dressing_room"] = ", ".join(same)
            if others:
                facts["from_other_circles"] = ", ".join(others)
        size = 0.8 + len(backers) / 10 + (person["reputation_world"] or 0) / 15000
        leads.append(Lead("dressing_room", f"{len(backers)} {w.name(team)} players take {person['name']}'s side", w.until,
                          w.weight(league) * 10 * size, {k: v for k, v in facts.items() if v is not None},
                          [f"person:{about}", f"club:{team}"] + [f"person:{b['uid']}" for b in backers],
                          w.beats_for(team, competition_uid=league), slug=f"{about}:{min(b['started'] for b in backers)}"))
    return sorted(leads, key=lambda lead: -lead.score)[:3]


def settled(w) -> list:
    """A concern that lasted weeks and ended in the cycle, for a well-known player or one at a followed club: what it was,
    how long, and his games since it began."""
    from app.services.story_leads import Lead

    leads = []
    for c in _concerns(w, "c.ended > :s and c.ended <= :u", s=str(w.since), u=str(w.until)):
        if c["code_key"] not in UNSETTLED or not c["started"]:
            continue
        lasted = (_day(c["ended"]) - _day(c["started"])).days
        star = c["reputation_world"] or 0
        if lasted < SETTLED_DAYS or (star < SETTLED_NAME and not w.followed(c["team_uid"])):
            continue
        facts, league = _player_facts(w, c)
        facts.update({"concern": c["text"], "began": str(c["started"]), "ended": str(c["ended"]), "lasted_days": lasted,
                      "what_ended_it": "not known from the save"})
        size = 0.4 + star / 20000
        leads.append(Lead("settled", f"{c['player']} settles", w.until, w.weight(league) * 10 * size,
                          {k: v for k, v in facts.items() if v is not None}, [f"person:{c['person_uid']}", f"club:{c['team_uid']}"],
                          w.beats_for(c["team_uid"], competition_uid=league), slug=f"{c['person_uid']}:{c['kind']}-{c['code']}:{c['started']}:end"))
    return sorted(leads, key=lambda lead: -lead.score)[:3]


FINDERS = (unsettled, taking_sides, settled)
