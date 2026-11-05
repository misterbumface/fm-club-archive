"""The story finder: what happened in the world in one window, ranked as news (docs/media-plan.md).

Each finder reads the world database and returns leads. A lead has a kind, a factual headline,
the facts a writer needs, the world references it is about (``person:<uid>``, ``club:<uid>``,
``competition:<uid>``, ``edition:<uid>:<year>``, ``match:<date>:<home>:<away>``), the beats it
fits (``world``, ``nation:<uid>``, ``transfers``, ``young``, ``history``, ``numbers``) and a score.

The score is the weight of the competition (from the game's own reputation of it, 0-200, so a
World Cup final outweighs a regional cup and Europe's leagues outweigh the rest) times how rare
or surprising the event is: a first-ever title, a long wait ended, a winner far below the usual
stature, a giant-killing across levels or reputations, a record fee. Stories about the followed
clubs (the setting ``followed_clubs``: Chelsea and Boreham Wood unless changed) get the ``home``
beat; the outlets on that beat rank them higher (editions.plan_editions), the others do not.
"""

from __future__ import annotations

import json
import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.engine import Connection

from app.services import story_leads_now as now
from app.services import story_leads_crisis as crisis
from app.services import story_leads_moods as dressing
from app.services import story_leads_tactics as sheets
from app.services import world_queries as q

SENIOR = ("league", "cup", "continental", "national_teams")
RECORD_FEE = 1_000_000  # a league or club record below this (in pounds) is not news
ANNIVERSARIES = (10, 20, 25, 30, 40, 50)
MIN_PLAYER_AGE = 15  # a younger player has a wrong birth date
FOLLOWED_SETTING = "followed_clubs"  # a JSON list of club uids
DEFAULT_FOLLOWED: tuple[int, ...] = ()  # a new archive follows no club until the curator chooses (Settings)
FOLLOWED_BOOST = 1.5
YOUTH_NAMES = re.compile(r"\b(U ?1\d|U ?2[0-3]|Under ?\d+|Youth|Young|Reserves?|Academy|Juniors?)\b", re.IGNORECASE)
YOUTH_WEIGHT = 0.5  # youth competitions and awards are small news
UPSET_GAP = 1500  # a reputation gap (0-10 000) that makes a win a giant-killing
UPSET_LOSER = 5000  # ... when the loser's reputation is at least this


def followed_clubs() -> tuple[int, ...]:
    """The club uids whose stories are boosted (needs an app context; the default without one)."""
    try:
        from app.models import AppSetting

        setting = AppSetting.query.filter_by(key=FOLLOWED_SETTING).first()
    except Exception:  # noqa: BLE001 - no app context or no settings table: the default
        return DEFAULT_FOLLOWED
    if setting is None:
        return DEFAULT_FOLLOWED
    try:
        return tuple(int(uid) for uid in json.loads(setting.value))
    except (ValueError, TypeError):
        return DEFAULT_FOLLOWED


@dataclass
class Lead:
    kind: str
    headline: str
    date: date
    score: float
    facts: dict = field(default_factory=dict)
    refs: list[str] = field(default_factory=list)
    beats: set[str] = field(default_factory=set)
    slug: str | None = None  # a fixed key for a story that is told once, whatever the year (a legend's profile)
    outlet_id: int | None = None  # a story for one paper only (the report of a game that paper previewed)

    @property
    def key(self) -> str:
        """The same story found again (in a later window) has the same key."""
        if self.slug:
            return f"{self.kind}:{self.slug}"
        return f"{self.kind}:{self.refs[0] if self.refs else self.headline}:{self.date:%Y}"


class _World:
    """Facts the finders share, each read once."""

    def __init__(self, connection: Connection, since: date, until: date, followed: tuple[int, ...] = DEFAULT_FOLLOWED) -> None:
        self.c = connection
        self.since, self.until = since, until
        self.season = until.year if until.month >= 7 else until.year - 1
        self.competitions = {r["fm_uid"]: r for r in q._rows(connection, "select * from competitions where scope is not null")}
        self.nations = {r["fm_uid"]: r for r in q._rows(connection, "select * from nations")}
        self.reputation = {r["fm_uid"]: r["reputation"] for r in q._rows(connection, "select fm_uid, reputation from teams "
                                                                                     "where reputation is not null")}
        self.followed_clubs = set(followed)
        self.followed_teams = {r["fm_uid"] for r in q._rows(
            connection, f"select fm_uid from teams where club_uid in ({','.join(str(int(u)) for u in followed) or '0'})")}
        self._teams: dict[int, dict] = {}
        self._crowd: dict[int, float] | None = None
        self._levels: dict[int, dict[int, tuple[int, int | None]]] = {}

    def rows(self, sql: str, **params):
        return q._rows(self.c, sql, **params)

    def team(self, uid: int | None) -> dict:
        if uid and uid not in self._teams:
            self._teams.update(q.teams(self.c, [uid]))
            club = q._one(self.c, "select nation_uid, league_nation_uid from clubs where fm_uid = :u", u=uid)
            self._teams.setdefault(uid, {"name": None, "nation_uid": None, "club_uid": None})
            self._teams[uid] = {**self._teams[uid], "club_nation_uid": club and club["nation_uid"],
                                "league_nation_uid": club and club["league_nation_uid"]}
        return self._teams.get(uid, {}) if uid else {}

    def name(self, uid: int | None) -> str | None:
        return self.team(uid).get("name")

    def comp(self, uid: int | None) -> dict:
        return self.competitions.get(uid, {}) if uid else {}

    def weight(self, competition_uid: int | None) -> float:
        """2 to 5: from the game's reputation of the competition (World Cup and Champions League 5,
        English Premier Division 4.8, MLS 3.5), or the log of its average crowd when it has none.
        Youth competitions count half."""
        comp = self.comp(competition_uid)
        youth = comp.get("scope") == "youth" or bool(YOUTH_NAMES.search(comp.get("name") or ""))
        if comp.get("reputation") is not None:
            return (2 + 3 * comp["reputation"] / 200) * (YOUTH_WEIGHT if youth else 1)
        return self._crowd_weight(competition_uid) * (YOUTH_WEIGHT if youth else 1)

    def _crowd_weight(self, competition_uid: int | None) -> float:
        if self._crowd is None:
            self._crowd = {r["competition_uid"]: r["crowd"] for r in self.rows(
                "select competition_uid, avg(attendance) crowd from fixtures where attendance > 0 and competition_uid is not null "
                "and date >= :d group by 1", d=str(self.until - timedelta(days=5 * 365)))}
        crowd = self._crowd.get(competition_uid) or 500
        return math.log10(max(crowd, 100))

    def level(self, team_uid: int, season: int) -> tuple[int | None, int | None]:
        """A club's league and its level in one season."""
        if season not in self._levels:
            found: dict[int, Counter] = defaultdict(Counter)
            for r in self.rows("select team_uid, league_uid from player_seasons where season_start = :s and kind = 1 "
                               "and league_uid is not null", s=season):
                found[r["team_uid"]][r["league_uid"]] += 1
            self._levels[season] = {}
            for team, count in found.items():
                league = count.most_common(1)[0][0]
                self._levels[season][team] = (league, self.comp(league).get("level"))
        return self._levels[season].get(team_uid, (None, None))

    def rep(self, team_uid: int | None) -> int | None:
        """The game's reputation of a team (0-10 000; a club's first team, a nation's senior team)."""
        return self.reputation.get(team_uid) if team_uid else None

    def followed(self, *team_uids: int | None) -> bool:
        return any(uid in self.followed_teams for uid in team_uids if uid)

    def beats_for(self, *team_uids: int | None, competition_uid: int | None = None) -> set[str]:
        beats = {"world", "home"} if self.followed(*team_uids) else {"world"}
        nation = self.comp(competition_uid).get("nation_uid")
        if nation:
            beats.add(f"nation:{nation}")
        for uid in team_uids:
            t = self.team(uid)
            for key in ("league_nation_uid", "club_nation_uid", "nation_uid"):
                if t.get(key):
                    beats.add(f"nation:{t[key]}")
        return beats


def find_leads(connection: Connection, since: date, until: date, followed: tuple[int, ...] | None = None) -> list[Lead]:
    """Every lead in the window (since, until], best first."""
    world = _World(connection, since, until, followed_clubs() if followed is None else followed)
    leads: list[Lead] = []
    for finder in (_titles, _giant_killings, _big_wins, _promotions_and_relegations, _transfers, _spending, _rising_stars,
                   _scouting, _academy_scorers, _awards, _milestones, _club_scorers, _careers_ended, _legends, _legend_profiles,
                   _dynasties, _great_finals, _appointments, _where_are_they_now, _anniversaries, _performances, _sendings_off, _goal_awards,
                   _smash_and_grabs, *now.FINDERS, *sheets.FINDERS, *dressing.FINDERS, crisis.crises):
        leads.extend(finder(world))
    crisis.mark(world, leads)
    leads = fold_into_matchdays(leads)
    market_seasons(world, leads)
    leads.sort(key=lambda lead: -lead.score)
    unique: dict[str, Lead] = {}
    for lead in leads:
        unique.setdefault(lead.key, lead)
    return list(unique.values())


# -- economy (27 Oct 2026): one match-day piece instead of a dozen small ones; the market in its windows -------------------------
FOLDED_KINDS = ("performance", "sending_off", "smash_and_grab", "big_win")  # told inside the match-day article of their game


def fold_into_matchdays(leads: list[Lead]) -> list[Lead]:
    """A small story of a game that a match-day article covers becomes a line of that article (stories_of_the_day), not a piece."""
    rounds = {ref: lead for lead in leads if lead.kind == "league_round" for ref in lead.refs if ref.startswith("match:")}
    kept = []
    for lead in leads:
        home = next((rounds[ref] for ref in lead.refs if ref in rounds), None) if lead.kind in FOLDED_KINDS else None
        if home is None:
            kept.append(lead)
            continue
        home.facts.setdefault("stories_of_the_day", []).append(lead.headline)
        home.refs.extend(ref for ref in lead.refs if ref.startswith("person:") and ref not in home.refs)
    return kept


MARKET_KINDS = {"transfer_interest", "transfer_bid", "transfer_rumour", "loan_offer", "contract_running_down", "kings_wage"}
WINDOWS = ((6, 1, 8, 31), (1, 1, 1, 31))  # the summer and winter windows (month, day to month, day)
OFF_WINDOW = 0.35  # a market story outside the windows counts this much...
BIG_MARKET_PLAYER, BIG_MARKET_CLUB = 8000, 8500  # ... unless the player or a club is this well known (or a followed club's)


def in_window(day: date) -> bool:
    return any((m1, d1) <= (day.month, day.day) <= (m2, d2) for m1, d1, m2, d2 in WINDOWS)


def market_seasons(w: _World, leads: list[Lead]) -> None:
    """The market is news in its windows; outside them only for the biggest names and clubs, and for the followed clubs."""
    if in_window(w.until):
        return
    for lead in leads:
        if lead.kind not in MARKET_KINDS:
            continue
        clubs = [int(ref.split(":")[1]) for ref in lead.refs if ref.startswith("club:")]
        people = [int(ref.split(":")[1]) for ref in lead.refs if ref.startswith("person:")]
        star = max((r["reputation_world"] or 0 for r in w.rows(
            f"select reputation_world from people where fm_uid in ({','.join(str(u) for u in people) or '0'})")), default=0)
        if w.followed(*clubs) or star >= BIG_MARKET_PLAYER or any((w.rep(c) or 0) >= BIG_MARKET_CLUB for c in clubs):
            continue
        lead.score *= OFF_WINDOW


# -- titles ----------------------------------------------------------------------------------------
def _titles(w: _World) -> list[Lead]:
    leads = []
    for e in w.rows("select e.*, (select team_uid from edition_places p where p.competition_uid = e.competition_uid and p.year = e.year "
                    "and p.place = 1) winner, (select team_uid from edition_places p where p.competition_uid = e.competition_uid "
                    "and p.year = e.year and p.place = 2) runner_up from competition_editions e "
                    "where e.decided_on > :s and e.decided_on <= :u", s=str(w.since), u=str(w.until)):
        comp = w.comp(e["competition_uid"])
        if comp.get("scope") not in SENIOR or not e["winner"]:
            continue
        before = [r["year"] for r in w.rows("select year from edition_places where competition_uid = :c and team_uid = :t and place = 1 "
                                             "and year < :y order by year", c=e["competition_uid"], t=e["winner"], y=e["year"])]
        winners = [r["team_uid"] for r in w.rows("select p.team_uid from competition_editions e join edition_places p using "
                                                 "(competition_uid, year) where e.competition_uid = :c and p.place = 1 and e.year <= :y "
                                                 "order by e.year desc limit 200", c=e["competition_uid"], y=e["year"])]
        streak = next((i for i, t in enumerate(winners) if t != e["winner"]), len(winners))
        editions_before = w.rows("select count(*) n from competition_editions where competition_uid = :c and year < :y",
                                 c=e["competition_uid"], y=e["year"])[0]["n"]
        final = q.final_of(w.c, e["competition_uid"], e["decided_on"], e["winner"]) if comp["scope"] != "league" else None
        winner, runner_up = w.name(e["winner"]), w.name(e["runner_up"])
        facts = {"competition": comp["name"], "edition": e["season_label"], "decided_on": str(e["decided_on"]), "winner": winner,
                 "runner_up": runner_up, "titles_before": len(before), "last_title_before": before[-1] if before else None,
                 "titles_in_a_row": streak, "editions_played_before": editions_before}
        if final:
            scored, conceded, _ = q.result_for(final, e["winner"])
            facts["final"] = {"date": str(final["date"]), "score": f"{scored}-{conceded}",
                              "after_extra_time": final["home_goals_after_extra_time"] is not None,
                              "penalties": final["home_penalties"] is not None}
        if comp["scope"] == "cup":
            facts["winner_league_level"] = w.level(e["winner"], w.season - 1)[1] or w.level(e["winner"], w.season)[1]
            facts["runner_up_league_level"] = w.level(e["runner_up"], w.season - 1)[1] or w.level(e["runner_up"], w.season)[1]
        rarity, angle = 1.0, "won"
        if not before and editions_before >= 5:
            rarity, angle = 3.0, "won for the first time"
        elif before and e["year"] - before[-1] >= 20:
            rarity, angle = 2.2, f"won for the first time since {before[-1]}"
        elif streak >= 3:
            rarity, angle = 1.3 + min(streak, 10) * 0.08, f"won for the {_ordinal(streak)} time in a row"
        gap = (facts.get("winner_league_level") or 0) - (facts.get("runner_up_league_level") or 99)
        if comp["scope"] == "cup" and gap >= 1:
            rarity *= 1.0 + 0.6 * gap  # a lower-league winner against a higher-league runner-up
            angle += f" from level {facts['winner_league_level']}, beating a level {facts['runner_up_league_level']} side in the final"
        if comp["scope"] == "league" and (comp.get("level") or 1) >= 2:
            rarity *= 0.75 ** (comp["level"] - 1)  # titles below the top level are smaller news
        if comp["scope"] == "league" and comp.get("level"):
            season = _season_of(date.fromisoformat(str(e["decided_on"])))
            _, last_level = w.level(e["winner"], season - 1)
            if last_level and last_level > comp["level"]:
                rarity *= 1.8
                angle += ", a season after promotion"
                facts["promoted_last_season"] = True
        if comp["scope"] == "national_teams" and len(before) == 0:
            rarity *= 1.3
        winner_rep, usual = w.rep(e["winner"]), (comp.get("reputation") or 0) * 50  # a usual winner: 50 x the competition's
        facts["winner_reputation"], facts["runner_up_reputation"] = winner_rep, w.rep(e["runner_up"])
        if winner_rep is not None and usual - winner_rep >= 1000:
            rarity *= 1 + (usual - winner_rep) / 5000  # far below the usual winner's stature
            angle += ", an underdog"
            facts["underdog"] = True
        score = (w.weight(e["competition_uid"]) * 10 * rarity * (1.2 if comp["scope"] in ("continental", "national_teams") else 1))
        leads.append(Lead("title", f"{winner} {angle}: {comp['name']} {e['season_label']}", e["decided_on"] if isinstance(e["decided_on"], date)
                          else date.fromisoformat(str(e["decided_on"])), score, facts,
                          [f"edition:{e['competition_uid']}:{e['year']}", f"club:{e['winner']}"],
                          w.beats_for(e["winner"], e["runner_up"], competition_uid=e["competition_uid"])))
    return leads


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# -- matches ---------------------------------------------------------------------------------------
def _knockout_and_international_games(w: _World) -> list[dict]:
    """Senior cup, continental and national-team matches in the window."""
    comps = ",".join(str(u) for u, c in w.competitions.items() if c["scope"] in ("cup", "continental", "national_teams")) or "0"
    return w.rows(f"select * from fixtures where competition_uid in ({comps}) and date > :s and date <= :u and home_goals is not null",
                  s=str(w.since), u=str(w.until))


def _giant_killings(w: _World) -> list[Lead]:
    """Wins over a side two or more league levels higher (domestic cups), or with a reputation far
    above the winner's (any senior cup, continental or national-team match)."""
    leads = []
    for f in _knockout_and_international_games(w):
        winner_side = q.result_for(f, f["home_uid"])[2]
        if winner_side == "D":
            continue
        winner, loser = (f["home_uid"], f["away_uid"]) if winner_side == "W" else (f["away_uid"], f["home_uid"])
        comp = w.comp(f["competition_uid"])
        level_gap, winner_level, loser_level = 0, None, None
        if comp["scope"] == "cup":
            season = _season_of(date.fromisoformat(str(f["date"])))
            _, winner_level = w.level(winner, season)
            _, loser_level = w.level(loser, season)
            if winner_level and loser_level:
                level_gap = winner_level - loser_level
        winner_rep, loser_rep = w.rep(winner), w.rep(loser)
        rep_gap = (loser_rep - winner_rep) if winner_rep is not None and loser_rep is not None and loser_rep >= UPSET_LOSER else 0
        if level_gap < 2 and rep_gap < UPSET_GAP:
            continue
        scored, conceded, _ = q.result_for(f, winner)
        how = _how(f, winner)
        score = (w.weight(f["competition_uid"]) * 10 * (1 + 0.5 * max(level_gap, 0) + max(rep_gap, 0) / (2 * UPSET_GAP))
                 * (1.4 if loser_level == 1 or (loser_rep or 0) >= 8500 else 1))
        sides = (f"{w.name(winner)} (level {winner_level}) beat {w.name(loser)} (level {loser_level})" if level_gap >= 2
                 else f"{w.name(winner)} beat {w.name(loser)}")
        leads.append(Lead("giant_killing", f"{sides} {scored}-{conceded}{how} in the {comp['name']}",
                          date.fromisoformat(str(f["date"])), score,
                          {"competition": comp["name"], "date": str(f["date"]), "winner": w.name(winner), "winner_level": winner_level,
                           "loser": w.name(loser), "loser_level": loser_level, "winner_reputation": winner_rep,
                           "loser_reputation": loser_rep, "score": f"{scored}-{conceded}", "decided": how.strip(" ,") or None,
                           "attendance": f["attendance"]},
                          [f"match:{f['date']}:{f['home_uid']}:{f['away_uid']}", f"club:{winner}", f"club:{loser}"],
                          w.beats_for(winner, loser, competition_uid=f["competition_uid"])))
    return sorted(leads, key=lambda lead: -lead.score)[:30]


def _season_of(day: date) -> int:
    return day.year if day.month >= 7 else day.year - 1


def _how(fixture: dict, side: int) -> str:
    """ ", on penalties (5-4)" or " after extra time" for a match decided that way, from one side."""
    if fixture["home_penalties"] is not None:
        mine, theirs = ((fixture["home_penalties"], fixture["away_penalties"]) if fixture["home_uid"] == side
                        else (fixture["away_penalties"], fixture["home_penalties"]))
        return f", {mine}-{theirs} on penalties"
    return " after extra time" if fixture["home_goals_after_extra_time"] is not None else ""


def _big_wins(w: _World) -> list[Lead]:
    senior = ",".join(str(u) for u, c in w.competitions.items() if c["scope"] in SENIOR) or "0"
    leads = []
    for f in w.rows(f"select * from fixtures where competition_uid in ({senior}) and date > :s and date <= :u and home_goals is not null "
                    "and abs(coalesce(home_goals_after_extra_time, home_goals) - coalesce(away_goals_after_extra_time, away_goals)) >= 6",
                    s=str(w.since), u=str(w.until)):
        home = f["home_goals_after_extra_time"] if f["home_goals_after_extra_time"] is not None else f["home_goals"]
        away = f["away_goals_after_extra_time"] if f["away_goals_after_extra_time"] is not None else f["away_goals"]
        comp = w.comp(f["competition_uid"])
        if comp.get("scope") == "league" and (comp.get("level") or 9) > 2:
            continue
        score = w.weight(f["competition_uid"]) * 10 * (0.55 + 0.08 * abs(home - away))
        leads.append(Lead("big_win", f"{w.name(f['home_uid'])} {home}-{away} {w.name(f['away_uid'])} ({comp['name']})",
                          date.fromisoformat(str(f["date"])), score,
                          {"competition": comp["name"], "date": str(f["date"]), "home": w.name(f["home_uid"]),
                           "away": w.name(f["away_uid"]), "score": f"{home}-{away}"},
                          [f"match:{f['date']}:{f['home_uid']}:{f['away_uid']}"],
                          w.beats_for(f["home_uid"], f["away_uid"], competition_uid=f["competition_uid"]) | {"numbers"}))
    return leads


# -- seasons: promotions and relegations ----------------------------------------------------------------
def _promotions_and_relegations(w: _World) -> list[Lead]:
    """When a season ends in the window. Rises: clubs that went up a level in each of the last two
    seasons (level L+2, then L+1, then L in the finished season). Falls: clubs with three or more
    league and continental titles who play below the top level in the next season. The finished
    season is the one whose leagues were decided in the window."""
    leads = []
    decided = {r["competition_uid"]: r for r in w.rows("select competition_uid, decided_on from competition_editions "
                                                        "where decided_on > :s and decided_on <= :u", s=str(w.since), u=str(w.until))}
    if not decided:
        return leads
    finished = _season_of(max(date.fromisoformat(str(r["decided_on"])) for r in decided.values()))
    for season in (finished - 2, finished - 1, finished, finished + 1):
        w.level(0, season)  # reads the season's leagues
    older, before, now, nxt = (w._levels[s_] for s_ in (finished - 2, finished - 1, finished, finished + 1))
    for team, (league, level) in now.items():
        if not level or league not in decided:
            continue
        _, before_level = before.get(team, (None, None))
        older_league, older_level = older.get(team, (None, None))
        if before_level and older_level and before_level - level == 1 and older_level - before_level == 1:
            score = 10 * (5 - min(level, 4)) * 1.8
            leads.append(Lead("rise", f"{w.name(team)} up two levels in two seasons: level {older_level} to level {level}, "
                                      f"{w.comp(league).get('name')}",
                              w.until, score, {"club": w.name(team), "levels": [older_level, before_level, level],
                                               "leagues": [w.comp(x).get("name") for x in (older_league, before.get(team, (None,))[0], league)],
                                               "seasons": [q.season_label(x) for x in (finished - 2, finished - 1, finished)]},
                              [f"club:{team}"], w.beats_for(team, competition_uid=league)))
        next_league, next_level = nxt.get(team, (None, None))
        if level == 1 and next_level and next_level >= 2:
            titles = w.rows("select count(*) n from edition_places p join competitions c on c.fm_uid = p.competition_uid "
                            "where p.team_uid = :t and p.place = 1 and c.scope in ('league', 'continental')", t=team)[0]["n"]
            if titles >= 3:
                score = 10 * (3 + min(titles, 30) / 6)
                leads.append(Lead("fall", f"{w.name(team)} relegated from the {w.comp(league).get('name')} "
                                          f"({titles} league and continental titles)",
                                  w.until, score, {"club": w.name(team), "league": w.comp(league).get("name"), "major_titles": titles},
                                  [f"club:{team}"], w.beats_for(team, competition_uid=league)))
    return leads


# -- transfers ---------------------------------------------------------------------------------------
def _transfers(w: _World) -> list[Lead]:
    """The biggest moves of the window, and every record: world, league (the buying club's league)
    and club. Earlier records are kept in memory while the moves are read in date order."""
    leads = []
    w.level(0, w.season)
    league_of = {team: league for team, (league, _) in w._levels[w.season].items()}
    world_record, league_record, club_record = 0, defaultdict(int), defaultdict(int)
    for t in w.rows("select to_uid, fee from transfers where fee > 0 and date <= :s", s=str(w.since)):
        world_record = max(world_record, t["fee"])
        club_record[t["to_uid"]] = max(club_record[t["to_uid"]], t["fee"])
        if league_of.get(t["to_uid"]):
            league_record[league_of[t["to_uid"]]] = max(league_record[league_of[t["to_uid"]]], t["fee"])
    moves = w.rows("select t.*, p.name, p.birth_date, p.reputation_world from transfers t join people p on p.fm_uid = t.person_uid "
                   "where t.date > :s and t.date <= :u and t.kind in (1, 2, 3, 4, 7, 8) order by t.date", s=str(w.since), u=str(w.until))
    for t in moves:
        fee, to_uid = t["fee"] or 0, t["to_uid"]
        if not w.name(to_uid):
            continue  # released, no new club
        to_league = league_of.get(to_uid)
        angles, rarity = [], 1.0
        if fee and fee > world_record:
            angles.append("world record fee")
            rarity *= 2.5
        elif fee >= RECORD_FEE and to_league and fee > league_record[to_league] and league_record[to_league]:
            angles.append(f"{w.comp(to_league).get('name')} record fee")
            rarity *= 1.6
        elif fee >= RECORD_FEE and fee > club_record[to_uid] and club_record[to_uid]:
            angles.append(f"{w.name(to_uid)} record signing")
            rarity *= 1.3
        if fee:
            world_record = max(world_record, fee)
            club_record[to_uid] = max(club_record[to_uid], fee)
            if to_league:
                league_record[to_league] = max(league_record[to_league], fee)
        if t["kind"] != 3 and w.rows("select 1 from player_seasons where person_uid = :p and team_uid = :c and kind = 1 "
                                     "and season_start < :s limit 1", p=t["person_uid"], c=to_uid, s=w.season - 1):
            angles.append("returns to a former club")
            rarity *= 1.3
        star = t["reputation_world"] or 0
        if star >= 8000:
            angles.append("a world star")
        rarity *= 1 + max(star - 6000, 0) / 8000
        if not fee and not angles:
            continue
        size = math.log10(max(fee, 100_000)) if fee else 5
        score = 10 * size * rarity * 0.75
        day = date.fromisoformat(str(t["date"]))
        kind = q.TRANSFER_KINDS.get(t["kind"])
        leads.append(Lead("transfer", f"{t['name']} {kind.lower()}: {w.name(t['from_uid']) or 'no club'} to {w.name(to_uid)}"
                                      + (f", £{fee / 1e6:.1f}m" if fee else "") + (f" ({', '.join(angles)})" if angles else ""),
                          day, score,
                          {"player": t["name"], "age": q.age_on(t["birth_date"], day), "kind": kind, "from": w.name(t["from_uid"]),
                           "to": w.name(to_uid), "fee_pounds": fee or None, "fee_euros": round(fee * 1.166) if fee else None,
                           "world_reputation": t["reputation_world"], "angles": angles},
                          [f"person:{t['person_uid']}", f"club:{to_uid}"] + ([f"club:{t['from_uid']}"] if t["from_uid"] else []),
                          w.beats_for(to_uid, t["from_uid"], competition_uid=to_league) | {"transfers"}))
    leads = sorted(leads, key=lambda lead: -lead.score)[:25]
    for lead in leads:
        if lead.facts["kind"] != "Loan" or lead.score >= 40:
            lead.facts.update(now.transfer_context(w, int(lead.refs[0].split(":")[1]), int(lead.refs[1].split(":")[1]), lead.date))
    return leads


def _spending(w: _World) -> list[Lead]:
    """Clubs that spent the most in their league in the window, the world's biggest spenders, and the
    followed clubs' spending. Lower leagues count more: a level-5 club that outspends its league is news."""
    w.level(0, w.season)
    league_of = w._levels[w.season]
    spend, signings = defaultdict(int), defaultdict(list)
    for t in w.rows("select t.to_uid, t.from_uid, t.fee, p.name from transfers t join people p on p.fm_uid = t.person_uid "
                    "where t.date > :s and t.date <= :u and t.fee > 0 and t.kind in (1, 2, 8)", s=str(w.since), u=str(w.until)):
        spend[t["to_uid"]] += t["fee"]
        signings[t["to_uid"]].append((t["fee"], t["name"], w.name(t["from_uid"])))
    by_league: dict[int, list[int]] = defaultdict(list)
    for club, total in spend.items():
        league = league_of.get(club, (None, None))[0]
        if league:
            by_league[league].append(total)
    world_top = sorted(spend.values(), reverse=True)[:5]
    leads = []
    for club, total in spend.items():
        league, level = league_of.get(club, (None, None))
        top_of_league = bool(league) and total == max(by_league[league]) and len(by_league[league]) >= 3
        if not ((top_of_league and (level or 1) >= 2 and total >= 3 * RECORD_FEE) or total in world_top
                or (w.followed(club) and total >= RECORD_FEE)):
            continue
        biggest = sorted(signings[club], reverse=True)[:3]
        score = 10 * max(math.log10(total) - 5, 0.5) * 3 * (1 + 0.3 * ((level or 1) - 1)) * 0.6
        where = f"level {level}, {w.comp(league).get('name')}" if level else "abroad"
        leads.append(Lead("spending", f"{w.name(club)} ({where}) spend £{total / 1e6:.1f}m on {len(signings[club])} players"
                                      + (", the most in their league" if top_of_league else ""),
                          w.until, score, {"club": w.name(club), "league": w.comp(league).get("name"), "level": level,
                                           "club_reputation": w.rep(club), "spent_pounds": total, "signings": len(signings[club]),
                                           "most_in_league": top_of_league,
                                           "biggest": [{"player": n, "from": f, "fee_pounds": fee} for fee, n, f in biggest]},
                          [f"club:{club}"], w.beats_for(club, competition_uid=league) | {"transfers"}))
    return sorted(leads, key=lambda lead: -lead.score)[:10]


# -- players --------------------------------------------------------------------------------------------
def _rising_stars(w: _World) -> list[Lead]:
    """Players 21 or younger with a strong season at a level 1 or 2 league."""
    leads = []
    season = w.season - 1 if w.until.month < 9 else w.season
    for r in w.rows("select ps.person_uid, ps.team_uid, ps.league_uid, sum(ps.starts + ps.substitute_appearances) games, sum(ps.goals) goals, "
                    "sum(ps.assists) assists, sum(ps.rating_total) rt, sum(ps.rated_appearances) ra, p.name, p.birth_date, p.nation_uid, "
                    "p.reputation_world "
                    "from player_seasons ps join people p on p.fm_uid = ps.person_uid where ps.season_start = :s and ps.kind = 1 "
                    "group by ps.person_uid, ps.team_uid having games >= 15", s=season):
        age = q.age_on(r["birth_date"], w.until)
        level = w.comp(r["league_uid"]).get("level")
        rating = q.rating(r["rt"], r["ra"])
        if age is None or age > 21 or age < MIN_PLAYER_AGE or not level or level > 2 or not rating:
            continue
        if rating < 7.15 and (r["goals"] or 0) < 12:
            continue
        score = ((10 * (rating - 6.4) * 3 * (1.25 if level == 1 else 0.9) * (1 + (21 - age) * 0.12) + (r["goals"] or 0) * 0.6
                  + (r["reputation_world"] or 0) / 400))
        leads.append(Lead("rising_star", f"{r['name']} ({age}), {w.name(r['team_uid'])}: rating {rating}, {r['goals']} goals in "
                                         f"{r['games']} league games ({w.comp(r['league_uid']).get('name')})",
                          w.until, score, {"player": r["name"], "age": age, "club": w.name(r["team_uid"]),
                                           "league": w.comp(r["league_uid"]).get("name"), "season": q.season_label(season),
                                           "games": r["games"], "goals": r["goals"], "assists": r["assists"], "rating": rating,
                                           "world_reputation": r["reputation_world"],
                                           "nationality": (w.nations.get(r["nation_uid"]) or {}).get("adjective")},
                          [f"person:{r['person_uid']}", f"club:{r['team_uid']}"], w.beats_for(r["team_uid"], competition_uid=r["league_uid"]) | {"young"}))
    return sorted(leads, key=lambda lead: -lead.score)[:20]


SCOUT_AGES = (15, 19)
# What a good player of each age is at today (the game's current ability, 1-200): a scout
# judges a teenager against his age group first, and only then guesses his ceiling.
AGE_LEVEL = {15: 60, 16: 75, 17: 90, 18: 105, 19: 115}
SCOUT_IMPRESSION = 3.0  # the lowest impression that makes a report
SCOUT_SETTLED_DAYS = 60  # a player who moved more recently is a transfer story, not a scouting report
SIGNED_RECENTLY_DAYS = 365  # a scouting report on a player signed this recently says so


def scout_impression(age: int, ability: int, potential: int) -> float:
    """How strongly a teenager stands out to a scout: mostly how far ahead of his age he is now,
    then how high his ceiling looks. (18, 130, 150) scores above (17, 86, 198)."""
    ahead = max(ability - AGE_LEVEL[age], 0) / 6
    ceiling = max(potential - 160, 0) / 12
    return ahead + ceiling


def level_words(age: int, ability: int) -> str:
    """A scout's tag for the game's current ability, against his age group (for ranking; writers get scout_view)."""
    ahead = ability - AGE_LEVEL[age]
    for floor, tag in ((30, "far ahead"), (15, "well ahead"), (0, "on track")):
        if ahead >= floor:
            return tag
    return "behind, all promise"


# What a scout might say for each level, many ways, so the reports do not all repeat one tag
# ("far ahead of his age group" was in 14 of 167 articles).
SCOUT_VIEWS = {
    "far ahead": ("could step into a first team tomorrow", "makes decisions like a senior professional",
                  "the most complete teenager our scouts saw this year", "plays as if he were five years older",
                  "already does the hard things simply", "looks out of place in youth football, in the best way",
                  "senior players would not embarrass him", "his game has no obvious teenage gaps"),
    "well ahead": ("a step beyond most players his age", "ready for regular first-team minutes soon",
                   "already reliable in his best areas", "his game is maturing quickly", "one of the better prospects watched this season",
                   "coaches trust him in big moments already"),
    "on track": ("developing as hoped", "a raw talent being shaped carefully", "promising, with work still to do",
                 "his best moments hint at more", "a project the club believes in"),
    "behind, all promise": ("all promise for now", "years from the first team, but the tools are there",
                            "unpolished but intriguing", "a long-term bet"),
}


def _scouting(w: _World) -> list[Lead]:
    """Teenagers (15-19) who stand out to a scout (scout_impression): the scouts' reports. A player who has just moved
    is left to the transfer stories (Sascha Büchel, signed six days before, became "Chelsea scout a spark")."""
    leads = []
    born_after = w.until.replace(year=w.until.year - SCOUT_AGES[1] - 1)
    for r in w.rows("select p.fm_uid, p.name, p.birth_date, p.team_uid, p.current_ability, p.potential_ability, n.adjective "
                    "from people p left join nations n on n.fm_uid = p.nation_uid where p.current_ability is not null "
                    "and p.birth_date > :b and p.team_uid is not null", b=str(born_after)):
        age = q.age_on(r["birth_date"], w.until)
        if age is None or not SCOUT_AGES[0] <= age <= SCOUT_AGES[1]:
            continue
        potential = r["potential_ability"] if r["potential_ability"] > 0 else r["current_ability"]  # a range: unknown
        impression = scout_impression(age, r["current_ability"], potential)
        if impression < SCOUT_IMPRESSION:
            continue
        moves = w.rows("select t.date, t.kind, t.fee, f.name from_name from transfers t left join teams f on f.fm_uid = t.from_uid "
                       "where t.person_uid = :p and t.date <= :u order by t.date desc limit 1", p=r["fm_uid"], u=str(w.until))
        moved = moves[0] if moves else None
        if moved and (w.until - date.fromisoformat(str(moved["date"])[:10])).days < SCOUT_SETTLED_DAYS:
            continue
        snapshot = q.latest_attributes(w.c, r["fm_uid"])
        attributes = snapshot and snapshot["attributes"]
        if isinstance(attributes, str):
            attributes = json.loads(attributes)
        strengths = [name.lower() for name, value in sorted((attributes or {}).items(), key=lambda kv: -kv[1])[:4] if value >= 13]
        team = w.team(r["team_uid"])
        club_uid = team.get("club_uid") or r["team_uid"]
        season = w.season - 1 if w.until.month in (7, 8) else w.season  # in pre-season, the season just ended (January is this one's)
        # the season's games by club: a player's figures must not read as his current team's
        played = w.rows("select t.name team, sum(s.starts + s.substitute_appearances) games, sum(s.goals) goals from player_seasons s "
                        "join teams t on t.fm_uid = s.team_uid where s.person_uid = :p and s.season_start = :s and s.kind in (1, 2, 3) "
                        "group by s.team_uid order by games desc", p=r["fm_uid"], s=season)
        score = 10 * (1.5 + impression * 0.7)
        level = level_words(age, r["current_ability"])
        facts = {"player": r["name"], "age": age, "nationality": r["adjective"], "team": team.get("name"),
                 # Only what people can see: nobody knows a player's potential, so it chooses the stories but
                 # never reaches the writer; the level reaches him as one scout's words, different for each player.
                 "scout_view": random.Random(r["fm_uid"]).choice(SCOUT_VIEWS[level]),
                 "standout_traits": strengths or None,
                 "this_season": {"season": q.season_label(season),
                                 "by_club": [{"club": p["team"], "games": p["games"] or 0, "goals": p["goals"] or 0} for p in played]},
                 **_his_games(w, r["fm_uid"], season)}
        if moved and moved["kind"] != 14 and (w.until - date.fromisoformat(str(moved["date"])[:10])).days < SIGNED_RECENTLY_DAYS:
            # a signing of this year is no academy product (Büchel, from Bielefeld in August, read as four years at Chelsea)
            facts["signed"] = (f"{q.TRANSFER_KINDS.get(moved['kind'], 'joined').lower()} from {moved['from_name'] or 'no club'} "
                               f"on {moved['date']}" + (f", £{moved['fee'] / 1e6:.1f}m" if moved["fee"] else ""))
        leads.append(Lead("scouting", f"Scouting report: {r['name']} ({age}), {team.get('name')}", w.until, score, facts,
                          [f"person:{r['fm_uid']}", f"club:{club_uid}"], w.beats_for(r["team_uid"]) | {"young"}))
    leads.sort(key=lambda lead: -lead.score)
    return [lead for lead in leads if "home" not in lead.beats][:10] + [lead for lead in leads if "home" in lead.beats][:3]


SCOUT_GAMES = 8  # a scouting report lists this many of his newest games


def _his_games(w: _World, person: int, season: int) -> dict:
    """The games a scouted player was in this season, newest first, and those he sat out: the only games a writer may put
    him in (a 2-1 win over West Ham U21 that never happened, with him the best player on the pitch, 1 Nov 2026)."""
    rows = w.rows("select g.played_on, t.name team, o.name opponent, g.status, g.minutes, g.goals, g.assists, g.rating from player_games g "
                  "join teams t on t.fm_uid = g.team_uid left join teams o on o.fm_uid = g.opponent_uid where g.person_uid = :p "
                  "and g.played_on >= :s and g.played_on <= :u order by g.played_on desc", p=person, s=f"{season}-07-01", u=str(w.until))
    played = [r for r in rows if r["status"] == "played" and (r["minutes"] or 0) > 0]
    lines = [f"{r['played_on']}, {r['team']} v {r['opponent'] or 'unknown'}: {r['minutes']} minutes, {r['goals'] or 0} goals, {r['assists'] or 0} assists"
             + (f", rating {r['rating']:.1f}" if r["rating"] else "") for r in played[:SCOUT_GAMES]]
    unused = sum(1 for r in rows if r["status"] == "unused")
    facts = {"his_games_this_season": lines or "none: he has not played a game this season",
             "games_he_sat_out_as_an_unused_substitute": unused or None}
    if played and rows and rows[0]["status"] != "played":
        facts["his_teams_newest_game"] = f"{rows[0]['played_on']}, {rows[0]['team']} v {rows[0]['opponent'] or 'unknown'}: he was {rows[0]['status']}"
    return {k: v for k, v in facts.items() if v is not None}


def _academy_scorers(w: _World) -> list[Lead]:
    """Youth-team goalscorers in the window: the followed clubs' best, and extreme numbers anywhere."""
    leads = []
    for r in w.rows("select g.scorer_uid, t.fm_uid team_uid, t.club_uid, count(*) goals, count(distinct f.id) games, p.name, p.birth_date "
                    "from goals g join fixtures f on f.id = g.fixture_id "
                    "join teams t on t.fm_uid = (case when g.side = 0 then f.home_uid else f.away_uid end) "
                    "join people p on p.fm_uid = g.scorer_uid "
                    "where f.date > :s and f.date <= :u and t.age_limit is not null and g.kind in (0, 2) "
                    "group by g.scorer_uid, t.fm_uid having goals >= 8", s=str(w.since), u=str(w.until)):
        followed = w.followed(r["team_uid"])
        if not followed and r["goals"] < 25:
            continue
        age = q.age_on(r["birth_date"], w.until)
        score = 10 * (1.5 + r["goals"] / 12) * (1.2 if age and age <= 17 else 1)
        leads.append(Lead("academy", f"{r['name']} ({age}): {r['goals']} goals for {w.name(r['team_uid'])}",
                          w.until, score, {"player": r["name"], "age": age, "team": w.name(r["team_uid"]),
                                           "club": w.name(r["club_uid"]), "goals": r["goals"], "games_scored_in": r["games"]},
                          [f"person:{r['scorer_uid']}", f"club:{r['club_uid']}"], w.beats_for(r["team_uid"]) | {"young"}))
    leads.sort(key=lambda lead: -lead.score)
    return [lead for lead in leads if "home" not in lead.beats][:6] + [lead for lead in leads if "home" in lead.beats][:5]


def _awards(w: _World) -> list[Lead]:
    leads = []
    years = {w.since.year + 1, w.until.year} if w.since.year != w.until.year else {w.until.year}
    for r in w.rows(f"select ap.*, a.name award, p.name from award_places ap join awards a on a.fm_uid = ap.award_uid "
                    f"left join people p on p.fm_uid = ap.person_uid where ap.place = 1 and ap.year in ({','.join(map(str, years))})"):
        if not r["person_uid"]:
            continue
        youth_award = bool(YOUTH_NAMES.search(r["award"]))
        world_award = not youth_award and any(word in r["award"] for word in ("World", "Ballon", "Golden Ball", "European Golden",
                                                                               "Player of the Year"))
        young = r["age"] and r["age"] <= 21
        if not world_award and not young:
            continue
        score = 10 * (5.2 if world_award else 3.2) * (1.2 if young else 1) * (YOUTH_WEIGHT if youth_award else 1)
        leads.append(Lead("award", f"{r['name']} wins the {r['award']} {r['year']}", w.until, score,
                          {"award": r["award"], "year": r["year"], "player": r["name"], "age": r["age"], "club": w.name(r["club_uid"]),
                           "games": r["appearances"], "goals": r["goals"], "votes": r["votes"] or None},
                          [f"person:{r['person_uid']}"] + ([f"club:{r['club_uid']}"] if r["club_uid"] else []),
                          w.beats_for(r["club_uid"]) | ({"young"} if young else set())))
    return sorted(leads, key=lambda lead: -lead.score)[:12]


GOAL_AWARD_SIZE = {"Goal of the Month": 2.6, "Goal of the Season": 3.6, "Goal of the Year": 3.6, "Goal of the Tournament": 4.2, "Best Goal": 4.2}


def _goal_awards(w: _World) -> list[Lead]:
    """A goal award given in the cycle (4 Nov 2026: Hilgers' 24-metre drive at Luton, the Premier Division's Goal of the Month
    for February 2064): the three goals in order, each with its game, minute and how it went in (the shot records)."""
    leads = []
    for award in w.rows("select distinct g.award_uid, g.given, a.name, a.short_name from goal_awards g join awards a on a.fm_uid = g.award_uid "
                        "where g.given > :s and g.given <= :u", s=str(w.since), u=str(w.until)):
        goals = w.rows("select g.*, p.name from goal_awards g left join people p on p.fm_uid = g.person_uid "
                       "where g.award_uid = :a and g.given = :g order by g.place", a=award["award_uid"], g=str(award["given"]))
        if not goals or not goals[0]["person_uid"]:
            continue
        monthly = "Month" in award["name"]
        given = date.fromisoformat(str(award["given"])[:10])
        month = (given.replace(day=1) - timedelta(days=1)).strftime("%B %Y")
        facts = {"award": award["name"], "for": f"the goals of {month}" if monthly else "the season's best goals so far", "given": given.strftime("%B %Y"),
                 "the_goals_in_order": [_award_goal_line(w, g) for g in goals]}
        winner = goals[0]
        size = next((v for k, v in GOAL_AWARD_SIZE.items() if k in award["name"]), 2.6)
        score = 10 * size * (1 + (w.rep(winner["team_uid"]) or 3000) / 20_000) * (1.4 if w.followed(winner["team_uid"]) else 1)
        leads.append(Lead("goal_award", f"{winner['name']} ({w.name(winner['team_uid'])}) wins the {award['name']}" + (f" for {month}" if monthly else ""),
                          given, score, facts, [f"person:{winner['person_uid']}", f"club:{winner['team_uid']}"]
                          + [f"person:{g['person_uid']}" for g in goals[1:] if g["person_uid"]],
                          w.beats_for(winner["team_uid"]), slug=f"{award['award_uid']}:{given}"))
    return sorted(leads, key=lambda lead: -lead.score)[:4]


def _award_goal_line(w: _World, g: dict) -> str:
    """'1. Bruce Hilgers (Chelsea), 43' against Luton Town, 2064-02-16 (Chelsea 6-0 Luton Town, Chelsea won): a drive (struck
    low and hard) from outside the box, high, near a post'."""
    f = next(iter(w.rows("select * from fixtures where date = :d and ((home_uid = :t and away_uid = :o) or (home_uid = :o and away_uid = :t))",
                         d=str(g["scored_on"])[:10], t=g["team_uid"], o=g["against_uid"])), None)
    minute = f"{g['minute']}'"
    how = None
    if f:
        scored = w.rows("select minute, added_time from goals where fixture_id = :f and scorer_uid = :p and kind in (0, 2) order by abs(minute - :m) limit 1",
                        f=f["id"], p=g["person_uid"], m=g["minute"])
        if scored:
            minute = f"{scored[0]['minute']}" + (f"+{scored[0]['added_time']}" if scored[0]["added_time"] else "") + "'"
        how = next((line.split(": ", 1)[1] for line in now._how_goals_went_in(w, f) or [] if line.startswith(f"{g['name']} (")), None)
    if how is None:  # the award keeps the kind and the range of the shot itself
        how = {"header": "a header", "volley": "a volley", "drive": "a drive (struck low and hard)", "finish": "a shot with the foot"}.get(
            g["kind"] or "", "the kind of shot is not recorded") + {"close": " from close range", "outside": " from outside the box"}.get(g["range"] or "", "")
    game = f" ({now._result_line(w, f).split(': ', 1)[1]})" if f else ""
    return f"{g['place']}. {g['name'] or 'unknown'} ({w.name(g['team_uid'])}), {minute} against {w.name(g['against_uid'])}, {str(g['scored_on'])[:10]}{game}: {how}"


def _milestones(w: _World) -> list[Lead]:
    """Players whose club goals passed 100, 200, 300… in the last finished season."""
    leads = []
    season = w.season - 1 if w.until.month < 9 else w.season
    for r in w.rows("select ps.person_uid, p.name, sum(case when ps.season_start < :s then ps.goals else 0 end) before, "
                    "sum(ps.goals) total from player_seasons ps join people p on p.fm_uid = ps.person_uid "
                    "where ps.kind in (1, 2, 3) and ps.season_start <= :s group by ps.person_uid having total >= 100 "
                    "and person_uid in (select person_uid from player_seasons where season_start = :s and kind = 1 and goals > 0)", s=season):
        mark = next((m for m in (500, 400, 300, 250, 200, 150, 100) if r["before"] < m <= r["total"]), None)
        if not mark:
            continue
        team = w.rows("select team_uid from player_seasons where person_uid = :p and season_start = :s and kind = 1 "
                      "order by starts desc limit 1", p=r["person_uid"], s=season)
        team_uid = team[0]["team_uid"] if team else None
        score = 10 * (2.5 + mark / 100)
        leads.append(Lead("milestone", f"{r['name']} passes {mark} club goals ({r['total']} after {q.season_label(season)})", w.until, score,
                          {"player": r["name"], "milestone": f"{mark} club goals", "club_goals": r["total"], "season": q.season_label(season),
                           "club": w.name(team_uid)},
                          [f"person:{r['person_uid']}"], w.beats_for(team_uid) | {"numbers"}))
    return sorted(leads, key=lambda lead: -lead.score)[:12]


def _club_scorers(w: _World) -> list[Lead]:
    """Players who became their club's all-time top scorer (league, cup and continental goals) in the
    last finished season, at clubs with a reputation of 5000 or more, or a followed club."""
    leads = []
    season = w.season - 1 if w.until.month < 9 else w.season
    totals: dict[int, list[dict]] = defaultdict(list)
    for r in w.rows("select ps.team_uid, ps.person_uid, sum(ps.goals) total, sum(case when ps.season_start = :s then ps.goals else 0 end) now "
                    "from player_seasons ps join clubs c on c.fm_uid = ps.team_uid where ps.kind in (1, 2, 3) and ps.season_start <= :s "
                    "and ps.team_uid in (select team_uid from player_seasons where season_start = :s and kind = 1 and goals > 0) "
                    "group by ps.team_uid, ps.person_uid having total >= 30", s=season):
        totals[r["team_uid"]].append(r)
    for club, rows in totals.items():
        if (w.rep(club) or 0) < 5000 and not w.followed(club):
            continue
        rows.sort(key=lambda r: -r["total"])
        best, second = rows[0], rows[1] if len(rows) > 1 else None
        if not best["now"] or not second or best["total"] - best["now"] > second["total"]:
            continue
        name = w.rows("select name from people where fm_uid = :p", p=best["person_uid"])[0]["name"]
        previous = w.rows("select name from people where fm_uid = :p", p=second["person_uid"])[0]["name"]
        score = 10 * (2.5 + (w.rep(club) or 0) / 3000)
        leads.append(Lead("club_record", f"{name} becomes {w.name(club)}'s all-time top scorer: {best['total']} goals, past "
                                         f"{previous} ({second['total']})", w.until, score,
                          {"player": name, "club": w.name(club), "goals": best["total"], "goals_this_season": best["now"],
                           "previous_record_holder": previous, "previous_record": second["total"], "season": q.season_label(season)},
                          [f"person:{best['person_uid']}", f"club:{club}"], w.beats_for(club) | {"numbers"}))
    return sorted(leads, key=lambda lead: -lead.score)[:8]


def _careers_ended(w: _World) -> list[Lead]:
    """Long careers with no games in the newest season: 12 or more seasons and 300 or more club
    games, no club now, and no reputation (the game keeps one for every playing player)."""
    leads = []
    season = w.season
    if not w.rows("select 1 from player_seasons where season_start = :s and kind = 1 and starts > 0 limit 1", s=season):
        season -= 1  # the newest season has not started yet
    candidates = [r["person_uid"] for r in w.rows(
        "select distinct ps.person_uid from player_seasons ps join people p on p.fm_uid = ps.person_uid "
        "where ps.season_start = :last and ps.kind = 1 and p.team_uid is null and p.reputation_current is null "
        "and ps.person_uid not in "
        "(select person_uid from player_seasons where season_start >= :s and starts + substitute_appearances > 0)",
        last=season - 1, s=season)]
    for start in range(0, len(candidates), 500):
        marks = ",".join(str(int(u)) for u in candidates[start:start + 500])
        for r in w.rows("select ps.person_uid, p.name, count(distinct ps.season_start) seasons, sum(ps.starts + ps.substitute_appearances) games, "
                        f"sum(ps.goals) goals from player_seasons ps join people p on p.fm_uid = ps.person_uid where ps.person_uid in ({marks}) "
                        "and ps.kind in (1, 2, 3) group by ps.person_uid having seasons >= 12 and games >= 300"):
            titles = sum(1 for h in q.player_honours(w.c, r["person_uid"]) if h["place"] == 1)
            score = 10 * (2.2 + r["games"] / 400 + titles / 12 + max(r["goals"] - 200, 0) / 150)
            leads.append(Lead("career_end", f"{r['name']}: no games since {q.season_label(season - 1)} after {r['seasons']} seasons, "
                                            f"{r['games']} games, {r['goals']} goals, {titles} trophies",
                              w.until, score, {"player": r["name"], "seasons": r["seasons"], "games": r["games"], "goals": r["goals"],
                                               "trophies": titles, "last_season": q.season_label(season - 1)},
                              [f"person:{r['person_uid']}"], {"world", "history"}))
    return sorted(leads, key=lambda lead: -lead.score)[:8]


LEGEND_GOALS, LEGEND_GAMES = 400, 750  # club goals or club games that make a legend


def _legend_careers(w: _World) -> list[dict]:
    """Club careers (league, cup, continental) with LEGEND_GOALS goals or LEGEND_GAMES games."""
    return w.rows("select ps.person_uid, p.name, min(ps.season_start) first, max(case when ps.starts + ps.substitute_appearances > 0 "
                  "then ps.season_start end) last, sum(ps.starts + ps.substitute_appearances) games, sum(ps.goals) goals, "
                  "sum(ps.assists) assists from player_seasons ps join people p on p.fm_uid = ps.person_uid where ps.kind in (1, 2, 3) "
                  "group by ps.person_uid having sum(ps.goals) >= :g or sum(ps.starts + ps.substitute_appearances) >= :n",  # not the column goals
                  g=LEGEND_GOALS, n=LEGEND_GAMES)


def _legends(w: _World) -> list[Lead]:
    """History features about legends: 10, 20, 25… years since a legend's first or last season (in the
    last finished season), and the world's all-time top scorers."""
    leads = []
    season = w.season - 1 if w.until.month < 9 else w.season
    careers = _legend_careers(w)
    data_start = w.rows("select min(season_start) s from player_seasons")[0]["s"]  # earlier seasons are not in the save
    for r in careers:
        for label, at in (("first", r["first"]), ("last", r["last"])):
            years = season - (at or season)
            if years not in ANNIVERSARIES or (label == "first" and at == data_start):
                continue
            club = w.rows("select team_uid, sum(starts + substitute_appearances) g from player_seasons where person_uid = :p and kind = 1 "
                          "group by team_uid order by g desc limit 1", p=r["person_uid"])
            club_uid = club[0]["team_uid"] if club else None
            score = 10 * (2 + r["goals"] / 250 + r["games"] / 600)
            leads.append(Lead("legend", f"{years} years since {r['name']}'s {label} season ({q.season_label(at)}): "
                                        f"{r['goals']} goals in {r['games']} club games", w.until, score,
                              {"player": r["name"], "years": years, "season": q.season_label(at), "which": label, "games": r["games"],
                               "goals": r["goals"], "assists": r["assists"], "career": f"{q.season_label(r['first'])} to "
                                                                                     f"{q.season_label(r['last'])}",
                               "main_club": w.name(club_uid)},
                              [f"person:{r['person_uid']}"] + ([f"club:{club_uid}"] if club_uid else []),
                              w.beats_for(club_uid) | {"history"}))
    leads = sorted(leads, key=lambda lead: -lead.score)[:8]
    top = sorted(careers, key=lambda r: -r["goals"])[:10]
    if top:
        leads.append(Lead("all_time", f"The all-time top scorers: {top[0]['name']} {top[0]['goals']}, "
                                      + ", ".join(f"{r['name']} {r['goals']}" for r in top[1:5]), w.until, 40.0,
                          {"list": [{"player": r["name"], "goals": r["goals"], "games": r["games"],
                                     "career": f"{q.season_label(r['first'])} to {q.season_label(r['last'])}"} for r in top]},
                          [f"person:{r['person_uid']}" for r in top], {"world", "history", "numbers"}))
    return leads


# -- history features (for The Long Ball): each is told once, so they rotate from cycle to cycle ----------
LEGEND_PROFILES = 30
DYNASTY_RUN = 5  # titles in a row
DYNASTIES = 20
GREAT_FINALS = 20
FINAL_STATURE = 170  # the competition's reputation (0-200)


def _legend_profiles(w: _World) -> list[Lead]:
    """Profiles of the world's greatest careers: goals, games, trophies, awards and caps."""
    careers = sorted(_legend_careers(w), key=lambda r: -(r["goals"] / 100 + r["games"] / 250))[:LEGEND_PROFILES * 2]
    leads = []
    for r in careers:
        uid = r["person_uid"]
        honours = [h for h in q.player_honours(w.c, uid) if h["place"] == 1]
        clubs = w.rows("select team_uid, min(season_start) first, max(season_start) last, sum(starts + substitute_appearances) games, "
                       "sum(goals) goals from player_seasons where person_uid = :p and kind in (1, 2, 3) group by team_uid "
                       "having games > 0 order by games desc limit 4", p=uid)
        caps = w.rows("select sum(starts + substitute_appearances) games, sum(goals) goals from player_seasons "
                      "where person_uid = :p and kind = 4", p=uid)[0]
        awards = w.rows("select a.name, ap.year from award_places ap join awards a on a.fm_uid = ap.award_uid where ap.person_uid = :p "
                        "and ap.place = 1 order by ap.year", p=uid)
        main_club = clubs[0]["team_uid"] if clubs else None
        cards = q.group_honours(honours)[:6]
        names = q.competitions(w.c, {c["competition_uid"] for c in cards})
        playing = w.rows("select reputation_current from people where fm_uid = :p", p=uid)
        score = 10 * (2.5 + r["goals"] / 300 + len(honours) / 20 + len(awards) / 15)
        still_playing = bool(playing and playing[0]["reputation_current"] is not None)
        season = w.season if w.until.month >= 9 else w.season - 1
        facts = {"player": r["name"], "career": f"{q.season_label(r['first'])} to {q.season_label(r['last'])}",
                 "still_playing": still_playing,
                 "stopped_playing_after": None if still_playing else q.season_label(r["last"]),
                 "years_since_his_last_season": None if still_playing else season - r["last"],
                 "club_games": r["games"], "club_goals": r["goals"], "club_assists": r["assists"], "trophies": len(honours),
                 "clubs": [{"club": w.name(c["team_uid"]), "seasons": f"{q.season_label(c['first'])} to {q.season_label(c['last'])}",
                            "games": c["games"], "goals": c["goals"]} for c in clubs],
                 "trophies_by_competition": [{"competition": names.get(c["competition_uid"], {}).get("name"), "times": c["count"]}
                                             for c in cards],
                 "national_team": {"games": caps["games"], "goals": caps["goals"]} if caps["games"] else None,
                 "individual_awards": [f"{a['name']} {a['year']}" for a in awards[:10]] or None}
        leads.append(Lead("legend_profile", f"Legends: {r['name']}, {r['goals']} goals in {r['games']} club games, {len(honours)} trophies",
                          w.until, score, facts, [f"person:{uid}"] + ([f"club:{main_club}"] if main_club else []),
                          {"history"} | ({"home"} if w.followed(main_club) else set()), slug=f"person:{uid}"))
    return sorted(leads, key=lambda lead: -lead.score)[:LEGEND_PROFILES]


def _dynasties(w: _World) -> list[Lead]:
    """Runs of five or more titles in a row in a top league or a continental competition."""
    comps = {u: c for u, c in w.competitions.items() if (c["scope"] == "league" and c.get("level") == 1) or c["scope"] == "continental"}
    if not comps:
        return []
    marks = ",".join(str(int(u)) for u in comps)
    by_comp: dict[int, list[dict]] = defaultdict(list)
    for r in w.rows(f"select e.competition_uid, e.year, e.season_label, p.team_uid from competition_editions e join edition_places p "
                    f"using (competition_uid, year) where p.place = 1 and e.competition_uid in ({marks}) and e.decided_on <= :u "
                    "order by e.competition_uid, e.year", u=str(w.until)):
        by_comp[r["competition_uid"]].append(r)
    leads = []
    for comp_uid, rows in by_comp.items():
        start = 0
        for i in range(1, len(rows) + 1):
            if i < len(rows) and rows[i]["team_uid"] == rows[start]["team_uid"]:
                continue
            run = rows[start:i]
            start = i
            if len(run) < DYNASTY_RUN:
                continue
            team = run[0]["team_uid"]
            first_season, last_season = run[0]["year"] - 1, run[-1]["year"] - 1
            scorers = w.rows("select p.name, sum(ps.goals) goals from player_seasons ps join people p on p.fm_uid = ps.person_uid "
                             "where ps.team_uid = :t and ps.season_start between :a and :b and ps.kind in (1, 2, 3) "
                             "group by ps.person_uid order by goals desc limit 4", t=team, a=first_season, b=last_season)
            going = run[-1] is rows[-1] and rows[-1]["year"] >= w.until.year - 1
            score = 10 * (2 + len(run) / 4) * w.weight(comp_uid) / 4.5
            leads.append(Lead("dynasty", f"Dynasties: {w.name(team)}, {len(run)} {comps[comp_uid]['name']} titles in a row "
                                         f"({run[0]['season_label']} to {run[-1]['season_label']})", w.until, score,
                              {"club": w.name(team), "competition": comps[comp_uid]["name"], "titles_in_a_row": len(run),
                               "from": run[0]["season_label"], "to": run[-1]["season_label"], "still_going": going,
                               "leading_scorers_of_those_seasons": [{"player": s["name"], "goals": s["goals"]} for s in scorers]},
                              [f"club:{team}", f"competition:{comp_uid}"], {"history"} | ({"home"} if w.followed(team) else set()),
                              slug=f"club:{team}:{comp_uid}:{run[0]['year']}"))
    return sorted(leads, key=lambda lead: -lead.score)[:DYNASTIES]


def _great_finals(w: _World) -> list[Lead]:
    """Past finals of the biggest competitions that went to extra time or penalties, or had five or more goals."""
    comps = {u: c for u, c in w.competitions.items() if c["scope"] in ("continental", "national_teams")
             and (c.get("reputation") or 0) >= FINAL_STATURE}
    leads = []
    for e in w.rows("select e.*, (select team_uid from edition_places p where p.competition_uid = e.competition_uid and p.year = e.year "
                    "and p.place = 1) winner from competition_editions e where e.competition_uid in "
                    f"({','.join(str(int(u)) for u in comps) or '0'}) and e.decided_on < :s", s=str(w.since - timedelta(days=365))):
        if not e["winner"]:
            continue
        final = q.final_of(w.c, e["competition_uid"], e["decided_on"], e["winner"])
        if not final or final["home_goals"] is None:
            continue
        extra = final["home_goals_after_extra_time"] is not None
        home = final["home_goals_after_extra_time"] if extra else final["home_goals"]
        away = final["away_goals_after_extra_time"] if extra else final["away_goals"]
        drama = ["decided on penalties"] if final["home_penalties"] is not None else ["decided in extra time"] if extra else []
        if home + away >= 5:
            drama.append(f"{home + away} goals")
        if not drama:
            continue
        loser = final["away_uid"] if final["home_uid"] == e["winner"] else final["home_uid"]
        comp = comps[e["competition_uid"]]
        score = 10 * (2 + len(drama) * 0.6 + (home + away) / 10) * w.weight(e["competition_uid"]) / 5
        leads.append(Lead("great_final", f"Great finals: {w.name(e['winner'])} v {w.name(loser)}, {comp['name']} {e['season_label']} "
                                         f"({', '.join(drama)})", w.until, score,
                          {"competition": comp["name"], "season": e["season_label"], "winner": w.name(e["winner"]),
                           "beaten": w.name(loser), "why_it_is_remembered": drama},
                          [f"edition:{e['competition_uid']}:{e['year']}", f"club:{e['winner']}", f"club:{loser}"],
                          {"history"} | ({"home"} if w.followed(e["winner"], loser) else set()),
                          slug=f"edition:{e['competition_uid']}:{e['year']}"))
    return sorted(leads, key=lambda lead: -lead.score)[:GREAT_FINALS]


# -- from the game's match reports ------------------------------------------------------------------------
STAR_RATING = 8.8  # a match rating that makes a display news on its own
HAT_TRICK = 3
SMASH_AND_GRAB_XG = 1.0  # the loser's chances worth this many expected goals more than the winner's
MATCH_REPORT_LEADS = 15  # of each kind, at most


def _report_matches(w: _World) -> str:
    """The senior competitive matches of the window that have the game's report (an SQL id list)."""
    senior = ",".join(str(u) for u, c in w.competitions.items() if c["scope"] in SENIOR) or "0"
    ids = [r["id"] for r in w.rows(f"select f.id from fixtures f join match_reports r on r.fixture_id = f.id "
                                   f"where f.competition_uid in ({senior}) and f.date > :s and f.date <= :u",
                                   s=str(w.since), u=str(w.until))]
    return ",".join(str(i) for i in ids) or "0"


def _is_final(w: _World, f: dict) -> bool:
    if w.comp(f["competition_uid"]).get("scope") == "league":
        return False
    return bool(w.rows("select 1 from competition_editions where competition_uid = :c and decided_on between :d and date(:d, '+1 day')",
                       c=f["competition_uid"], d=str(f["date"])))


def _match_ref(f: dict) -> str:
    return f"match:{f['date']}:{f['home_uid']}:{f['away_uid']}"


def _performances(w: _World) -> list[Lead]:
    """Outstanding displays from the game's match reports: a rating of 8.8 or more, or a hat-trick."""
    leads = []
    for row in w.rows("select m.*, p.name, f.date, f.competition_uid, f.home_uid, f.away_uid, f.home_goals, f.away_goals, "
                      "f.home_goals_after_extra_time, f.away_goals_after_extra_time, f.home_penalties, f.away_penalties, "
                      "r.player_of_the_match_uid from match_players m join fixtures f on f.id = m.fixture_id "
                      "join match_reports r on r.fixture_id = m.fixture_id join people p on p.fm_uid = m.person_uid "
                      f"where m.fixture_id in ({_report_matches(w)}) and m.played = 1 and m.minute_on < 80 "
                      "and (m.rating >= :star or m.goals >= :hat)", star=STAR_RATING, hat=HAT_TRICK):
        team = row["home_uid"] if row["side"] == 0 else row["away_uid"]
        opponent = row["away_uid"] if row["side"] == 0 else row["home_uid"]
        scored, conceded, result = q.result_for(row, team)
        comp = w.comp(row["competition_uid"])
        feat = ("hat-trick" if row["goals"] == HAT_TRICK else f"{row['goals']} goals" if row["goals"] > HAT_TRICK
                else f"{row['rating']:.1f}-rated display")
        score = w.weight(row["competition_uid"]) * 10 * (0.45 + max(row["rating"] - 8, 0) * 0.4 + 0.12 * row["goals"]
                                                        + (0.15 if _is_final(w, row) else 0))
        leads.append(Lead("performance", f"{row['name']}'s {feat} for {w.name(team)} against {w.name(opponent)} ({comp['name']})",
                          date.fromisoformat(str(row["date"])), score,
                          {"competition": comp["name"], "date": str(row["date"]), "player": row["name"], "team": w.name(team),
                           "opponent": w.name(opponent), "score": f"{scored}-{conceded}" + _how(row, team),
                           "result": {"W": "won", "D": "drew", "L": "lost"}[result], "match_rating": round(row["rating"], 1),
                           "goals": row["goals"] or None, "assists": row["assists"] or None,
                           "player_of_the_match": row["player_of_the_match_uid"] == row["person_uid"], "final": _is_final(w, row) or None},
                          [f"person:{row['person_uid']}", _match_ref(row), f"club:{team}"],
                          w.beats_for(team, opponent, competition_uid=row["competition_uid"])))
    return sorted(leads, key=lambda lead: -lead.score)[:MATCH_REPORT_LEADS]


def _sendings_off(w: _World) -> list[Lead]:
    """Red cards in big matches: finals and the top competitions, more when the team then failed to win."""
    leads = []
    for row in w.rows("select m.*, p.name, f.date, f.competition_uid, f.home_uid, f.away_uid, f.home_goals, f.away_goals, "
                      "f.home_goals_after_extra_time, f.away_goals_after_extra_time, f.home_penalties, f.away_penalties "
                      "from match_players m join fixtures f on f.id = m.fixture_id join people p on p.fm_uid = m.person_uid "
                      f"where m.fixture_id in ({_report_matches(w)}) and m.red_cards > 0"):
        final = _is_final(w, row)
        weight = w.weight(row["competition_uid"])
        if weight < 4.2 and not final:
            continue
        team = row["home_uid"] if row["side"] == 0 else row["away_uid"]
        opponent = row["away_uid"] if row["side"] == 0 else row["home_uid"]
        scored, conceded, result = q.result_for(row, team)
        minute = f"{row['sent_off_minute']}+{row['added_time_off']}'" if row["added_time_off"] else f"{row['sent_off_minute']}'"
        comp = w.comp(row["competition_uid"])
        score = weight * 10 * (0.5 + (0.35 if final else 0) + (0.2 if result != "W" else 0) + (0.1 if row["sent_off_minute"] < 60 else 0))
        verb = {"W": "won", "D": "drew", "L": "lost"}[result]
        outcome = (f"{verb} on penalties ({scored}-{conceded}, {_how(row, team).split(', ')[1].replace(' on penalties', '')})"
                   if row["home_penalties"] is not None else f"{verb} {scored}-{conceded}{_how(row, team)}")
        leads.append(Lead("sending_off", f"{row['name']} sent off ({minute}) as {w.name(team)} {outcome} against {w.name(opponent)}"
                                         f"{' in the ' + comp['name'] + ' final' if final else ' (' + comp['name'] + ')'}",
                          date.fromisoformat(str(row["date"])), score,
                          {"competition": comp["name"], "date": str(row["date"]), "player": row["name"], "team": w.name(team),
                           "opponent": w.name(opponent), "minute": minute, "score": f"{scored}-{conceded}" + _how(row, team),
                           "result": {"W": "won", "D": "drew", "L": "lost"}[result], "final": final or None},
                          [f"person:{row['person_uid']}", _match_ref(row), f"club:{team}", f"club:{opponent}"],
                          w.beats_for(team, opponent, competition_uid=row["competition_uid"])))
    return sorted(leads, key=lambda lead: -lead.score)[:MATCH_REPORT_LEADS]


def _smash_and_grabs(w: _World) -> list[Lead]:
    """Wins against the run of play: the beaten side had far better chances (expected goals) and more shots."""
    leads = []
    sides = defaultdict(dict)
    for row in w.rows("select m.fixture_id, m.side, sum(m.xg) xg, sum(m.shots) shots from match_players m "
                      f"where m.fixture_id in ({_report_matches(w)}) group by m.fixture_id, m.side"):
        sides[row["fixture_id"]][row["side"]] = row
    for fixture_id, pair in sides.items():
        if len(pair) < 2:
            continue
        f = q._one(w.c, "select * from fixtures where id = :id", id=fixture_id)
        scored, conceded, result = q.result_for(f, f["home_uid"])
        if result == "D" or f["home_penalties"] is not None:
            continue
        winner_side = 0 if result == "W" else 1
        winner, loser = pair[winner_side], pair[1 - winner_side]
        if loser["xg"] - winner["xg"] < SMASH_AND_GRAB_XG or loser["shots"] < 2 * max(winner["shots"], 1):
            continue
        winner_uid = f["home_uid"] if winner_side == 0 else f["away_uid"]
        loser_uid = f["away_uid"] if winner_side == 0 else f["home_uid"]
        won, lost, _ = q.result_for(f, winner_uid)
        comp = w.comp(f["competition_uid"])
        score = w.weight(f["competition_uid"]) * 10 * (0.4 + 0.2 * (loser["xg"] - winner["xg"]) + (0.2 if _is_final(w, f) else 0))
        leads.append(Lead("smash_and_grab", f"{w.name(winner_uid)} beat {w.name(loser_uid)} {won}-{lost} against the run of play "
                                            f"({comp['name']})", date.fromisoformat(str(f["date"])), score,
                          {"competition": comp["name"], "date": str(f["date"]), "winner": w.name(winner_uid), "beaten": w.name(loser_uid),
                           "score": f"{won}-{lost}", "winner_shots": winner["shots"], "beaten_shots": loser["shots"],
                           "winner_expected_goals": round(winner["xg"], 2), "beaten_expected_goals": round(loser["xg"], 2)},
                          [_match_ref(f), f"club:{winner_uid}", f"club:{loser_uid}"],
                          w.beats_for(winner_uid, loser_uid, competition_uid=f["competition_uid"]) | {"numbers"}))
    return sorted(leads, key=lambda lead: -lead.score)[:MATCH_REPORT_LEADS]


# -- former players in staff jobs ------------------------------------------------------------------------
APPOINTMENT_GAMES = 200  # club games that make a former player's appointment news
WHERE_NOW = 20


def _former_players_in_jobs(w: _World, min_games: int) -> list[dict]:
    """People with a staff job now and a playing career of at least min_games club games."""
    return w.rows("select p.fm_uid, p.name, p.staff_job, p.staff_team_uid, p.staff_since, sum(ps.starts + ps.substitute_appearances) "
                  "games, sum(ps.goals) goals, min(ps.season_start) first, max(case when ps.starts + ps.substitute_appearances > 0 "
                  "then ps.season_start end) last from people p join player_seasons ps on ps.person_uid = p.fm_uid "
                  "where p.staff_job is not null and ps.kind in (1, 2, 3) group by p.fm_uid having games >= :n", n=min_games)


def _main_club(w: _World, uid: int) -> int | None:
    club = w.rows("select team_uid, sum(starts + substitute_appearances) g from player_seasons where person_uid = :p and kind = 1 "
                  "group by team_uid order by g desc limit 1", p=uid)
    return club[0]["team_uid"] if club else None


def _appointments(w: _World) -> list[Lead]:
    """Former players appointed to a staff job in the window: news."""
    leads = []
    for r in _former_players_in_jobs(w, APPOINTMENT_GAMES):
        since = date.fromisoformat(str(r["staff_since"])) if r["staff_since"] else None
        if since is None or not w.since < since <= w.until:
            continue
        main_club = _main_club(w, r["fm_uid"])
        team = r["staff_team_uid"]
        back = main_club is not None and w.team(main_club).get("club_uid") == w.team(team).get("club_uid")
        score = 10 * (2 + r["games"] / 400 + r["goals"] / 300) * (1.4 if back else 1) * (1.3 if r["staff_job"] == "Manager" else 1)
        leads.append(Lead("appointment", f"{r['name']} ({r['games']} club games as a player) appointed {r['staff_job']} at "
                                         f"{w.name(team)}" + (", back at his old club" if back else ""), since, score,
                          {"person": r["name"], "new_job": r["staff_job"], "at": w.name(team), "since": str(since),
                           "playing_career": f"{q.season_label(r['first'])} to {q.season_label(r['last'])}",
                           "club_games": r["games"], "club_goals": r["goals"], "main_club_as_a_player": w.name(main_club),
                           "returns_to_his_old_club": back},
                          [f"person:{r['fm_uid']}", f"club:{team}"] + ([f"club:{main_club}"] if main_club and main_club != team else []),
                          w.beats_for(team, main_club)))
    return sorted(leads, key=lambda lead: -lead.score)[:12]


def _where_are_they_now(w: _World) -> list[Lead]:
    """Legends who now work as coaches, scouts, directors or academy heads: told once each."""
    leads = []
    for r in _former_players_in_jobs(w, LEGEND_GAMES // 2):
        if r["goals"] < LEGEND_GOALS // 2 and r["games"] < LEGEND_GAMES:
            continue
        main_club = _main_club(w, r["fm_uid"])
        team = r["staff_team_uid"]
        since = date.fromisoformat(str(r["staff_since"])) if r["staff_since"] else None
        score = 10 * (2 + r["goals"] / 250 + r["games"] / 600)
        leads.append(Lead("where_now", f"Where are they now: {r['name']}, {r['goals']} goals in {r['games']} club games, now "
                                       f"{r['staff_job']} at {w.name(team)}", w.until, score,
                          {"person": r["name"], "playing_career": f"{q.season_label(r['first'])} to {q.season_label(r['last'])}",
                           "club_games": r["games"], "club_goals": r["goals"], "main_club_as_a_player": w.name(main_club),
                           "now": f"{r['staff_job']} at {w.name(team)}", "since": str(since) if since else None},
                          [f"person:{r['fm_uid']}", f"club:{team}"] + ([f"club:{main_club}"] if main_club and main_club != team else []),
                          {"history"} | ({"home"} if w.followed(team, main_club) else set()), slug=f"person:{r['fm_uid']}"))
    return sorted(leads, key=lambda lead: -lead.score)[:WHERE_NOW]


def _anniversaries(w: _World) -> list[Lead]:
    """Titles decided 10, 20, 25… years before a day in the window, in big competitions."""
    leads = []
    for years in ANNIVERSARIES:
        start, end = w.since.replace(year=w.since.year - years), w.until.replace(year=w.until.year - years)
        for e in w.rows("select e.*, (select team_uid from edition_places p where p.competition_uid = e.competition_uid and p.year = e.year "
                        "and p.place = 1) winner from competition_editions e where e.decided_on > :s and e.decided_on <= :u",
                        s=str(start), u=str(end)):
            comp = w.comp(e["competition_uid"])
            if comp.get("scope") not in ("continental", "national_teams") and not (comp.get("scope") == "league" and comp.get("level") == 1):
                continue
            weight = w.weight(e["competition_uid"])
            if weight < 4.3 or not e["winner"]:
                continue
            score = 10 * weight * (0.55 + years / 200)
            leads.append(Lead("anniversary", f"{years} years since {w.name(e['winner'])} won the {comp['name']} {e['season_label']}",
                              date.fromisoformat(str(e["decided_on"])).replace(year=date.fromisoformat(str(e["decided_on"])).year + years),
                              score, {"years": years, "competition": comp["name"], "edition": e["season_label"],
                                      "winner": w.name(e["winner"]), "decided_on": str(e["decided_on"])},
                              [f"edition:{e['competition_uid']}:{e['year']}", f"club:{e['winner']}"], {"history"}))
    return sorted(leads, key=lambda lead: -lead.score)[:10]
