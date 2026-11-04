"""Editions: each outlet's newspaper for one news cycle (docs/media-plan.md, step 4).

A cycle runs from the end of the last cycle (the newest knowledge cutoff of any outlet's edition)
to the game date of the newest save. The story finder reads the cycle once; each active outlet
takes the leads on its beat that it has not printed before, best first; the article writer
writes them in parallel; each outlet gets one edition, published at once (a newspaper arrives on
the doorstep). The curator unpublishes an edition on the desk to correct it, and publishes it again. The first article is the front-page lead; then three standard articles; the rest
are briefs. A features outlet (The Long Ball) writes features after its lead.
"""

from __future__ import annotations

import json
import logging
import math
import random
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from flask import current_app

from app.extensions import db
from app.models import world as world_models
from app.models.outlet import Outlet
from app.models.world_soccer_article import WorldSoccerArticle
from app.models.world_soccer_entity_link import WorldSoccerEntityLink
from app.models.world_soccer_issue import WorldSoccerIssue
from app.models.world_soccer_thread import WorldSoccerStoryThread
from app.services import features, article_writer, outlets as outlet_service, story_leads, world_queries as q
from app.services.story_leads import Lead
from app.services.story_writer import StoryError
from app.services.world_soccer import world_ref_context

log = logging.getLogger(__name__)

FIRST_WINDOW_DAYS = 98  # the first cycle, when no edition exists yet
CYCLE_DAYS = 14  # the papers come out after an import once this much game time has passed since the last editions
STANDARD_ARTICLES = 3  # after the lead
FEATURE_BEATS = {"history"}  # outlets on these beats write features, not briefs
WRITERS = 6  # articles written at the same time

SECTIONS = {"title": "Champions", "giant_killing": "Upsets", "big_win": "Results", "rise": "Rising", "fall": "Fallen",
            "transfer": "Transfers", "spending": "Transfers", "rising_star": "Next generation", "scouting": "Scouting", "academy": "Academy",
            "award": "Awards", "milestone": "Records", "club_record": "Records", "career_end": "Farewells",
            "legend": "Legends", "all_time": "Legends", "anniversary": "On this day", "legend_profile": "Legends",
            "dynasty": "Dynasties", "great_final": "Great finals", "appointment": "Appointments",
            "where_now": "Where are they now", "league_round": "The round", "misfiring": "Numbers", "clinical": "Numbers", "tired_legs": "Form", "run": "Form", "derby": "Derbies", "preview": "Coming up",
            "injury_blow": "Injuries", "underperformer": "Form", "overperformer": "Form", "expectations": "Form", "check_in": "Check-in",
            "stat_watch": "Numbers", "performance": "Results", "sending_off": "Results", "smash_and_grab": "Results",
            "system_change": "Tactics", "position_change": "Tactics", "partnership": "Tactics", "tactical_trend": "Tactics",
            "dropped": "Selection", "breakthrough": "Next generation", "unsettled": "Dressing room", "dressing_room": "Dressing room",
            "settled": "Dressing room", "expected_points": "Numbers", "continental_round": "Europe", "tie_report": "Europe", "tie_preview": "Europe",
            "knockout_round": "Europe", "knockout_preview": "Coming up",
            "european_nights": "Europe", "international_round": "International", "national_team": "International",
            "promised_report": "Results", "transfer_interest": "Transfers", "transfer_bid": "Transfers",
            "tournament_report": "Tournament", "tournament_preview": "Tournament", "tournament_round_up": "Tournament",
            "our_players_at_tournament": "Tournament", "player_of_the_tournament": "Tournament",
            "club_report": "Results", "club_preview": "Coming up", "scouting_follow_up": "Scouting",
            "kings_wage": "Transfers", "contract_running_down": "Transfers", "manager_out": "Appointments",
            "travel_log": "Away days", "interview": "Interviews", "war_of_words": "War of words",
            "under_pressure": "Appointments", "crisis": "Form", "crisis_over": "Form", "vacancy": "Appointments", "transfer_rumour": "Transfers", "loan_offer": "Transfers"}
ARTICLE_TYPE = {"lead": "report", "standard": "report", "brief": "brief", "feature": "analysis", "column": "analysis", "matchday": "report"}
ISSUE_PROMINENCE = {"lead": "lead", "standard": "standard", "brief": "brief", "feature": "major", "column": "major", "matchday": "major"}


# The subject of a story, which ties articles into a thread across outlets and editions.
PERSON_STORIES = {"transfer", "rising_star", "scouting", "academy", "milestone", "career_end", "legend", "legend_profile",
                  "award", "club_record", "appointment", "where_now", "injury_blow", "underperformer", "overperformer", "stat_watch",
                  "position_change", "dropped", "breakthrough", "unsettled", "settled", "dressing_room"}
EARLIER_COVERAGE = 5  # the newest earlier articles a writer sees


def thread_key(lead: Lead) -> str | None:
    """'person:<uid>' for a player's story, 'club:<uid>' for a club's; None for the rest."""
    wanted = ("person:",) if lead.kind in PERSON_STORIES else ("club:",)
    return next((ref for ref in lead.refs if ref.startswith(wanted)), None)


def earlier_coverage(lead: Lead, before: date, leave_out: int | None = None) -> list[dict]:
    """What the outlets already wrote about the lead's people and its main subject, newest first: the
    articles linked to any of its players (a side character in an old story counts too) or in its
    subject's thread. Each says who of the lead's people it was about."""
    key = thread_key(lead)
    people = [ref for ref in lead.refs if ref.startswith("person:")]
    wanted = set(people) | ({key} if key else set())
    if not wanted:
        return []
    by_link = (db.session.query(WorldSoccerArticle.id).join(WorldSoccerEntityLink)
               .filter(WorldSoccerEntityLink.world_ref.in_(wanted)))
    by_thread = (db.session.query(WorldSoccerArticle.id).join(WorldSoccerStoryThread)
                 .filter(WorldSoccerStoryThread.key == key)) if key else None
    ids = {row[0] for row in by_link} | ({row[0] for row in by_thread} if by_thread is not None else set())
    ids.discard(leave_out or 0)
    if not ids:
        return []
    rows = (WorldSoccerArticle.query.join(WorldSoccerIssue)
            .filter(WorldSoccerArticle.id.in_(ids), WorldSoccerIssue.knowledge_cutoff < before)
            .order_by(WorldSoccerIssue.publication_date.desc(), WorldSoccerArticle.id.desc()).limit(EARLIER_COVERAGE).all())
    found = []
    for article in rows:
        refs = {link.world_ref for link in article.entity_links}
        about = [link.label for link in article.entity_links if link.world_ref in wanted]
        found.append({"date": str(article.issue.publication_date), "outlet": article.issue.edition_name,
                      "headline": article.headline, "standfirst": article.standfirst, "about": about or None,
                      "same_subject": bool(key and key in refs), "what_it_was": _what_it_was(article.lead_key, before)})
    return found


def _what_it_was(lead_key: str | None, before: date) -> str | None:
    """A preview is told as one, with the game's date, and whether that game has been played yet: a writer read "Chelsea's
    late show leaves Rovers dreaming" (a preview) as a match already played (1 Nov 2026)."""
    parts = (lead_key or "").split(":")
    if len(parts) >= 3 and parts[0].endswith("preview") and parts[1] == "match":
        try:
            day = date.fromisoformat(parts[2])
        except ValueError:
            return "a preview"
        return (f"a preview of a game on {day:%-d %B %Y}" +
                (": not yet played, so nothing in it has happened" if day >= before else ""))
    return None


RELATED_STORIES = 5


def related_stories(lead: Lead, leads: list[Lead]) -> list[dict]:
    """Other stories of the same cycle that share a player or a club with this one, best first, so the
    writers can cross-reference them."""
    mine = {ref for ref in lead.refs if ref.startswith(("person:", "club:"))}
    found = [other for other in leads if other.key != lead.key and mine & set(other.refs)]
    return [{"story": other.headline, "kind": other.kind.replace("_", " "), "date": str(other.date)}
            for other in found[:RELATED_STORIES]]


def _thread(key: str | None, title: str | None, started: date) -> WorldSoccerStoryThread | None:
    if key is None:
        return None
    thread = WorldSoccerStoryThread.query.filter_by(key=key).first()
    if thread is None:
        thread = WorldSoccerStoryThread(key=key, title=(title or key)[:240], status="ongoing", started_on=started)
        db.session.add(thread)
        db.session.flush()
    return thread


@dataclass
class Plan:
    outlet: Outlet
    items: list[tuple[Lead, str]] = field(default_factory=list)  # (lead, writer prominence)
    writers: list[dict] = field(default_factory=list)  # the byline of each item
    forms: list[dict] = field(default_factory=list)  # the opening and headline form of each item


@dataclass
class CycleResult:
    since: date
    until: date
    leads: int = 0
    editions: list[int] = field(default_factory=list)  # issue ids
    written: int = 0
    refused: list[str] = field(default_factory=list)
    illustrated: int = 0


def cycle_window(until: date | None = None, min_days: int = 0) -> tuple[date, date] | None:
    """(since, until] of the next cycle; None when the world has no game date, nothing is new, or
    less than ``min_days`` of game time has passed since the last editions."""
    with db.engines[world_models.BIND].connect() as connection:
        game_date = q.game_date(connection)
    until = until or game_date
    if until is None:
        return None
    last = (db.session.query(db.func.max(WorldSoccerIssue.knowledge_cutoff))
            .filter(WorldSoccerIssue.knowledge_cutoff <= until).scalar())
    since = last or until - timedelta(days=FIRST_WINDOW_DAYS)
    return (since, until) if (until - since).days >= max(min_days, 1) else None


def next_cycle_due(until: date | None = None) -> date | None:
    """The game date from which an import makes the next editions (None before the first ones)."""
    last = db.session.query(db.func.max(WorldSoccerIssue.knowledge_cutoff)).scalar()
    return last + timedelta(days=CYCLE_DAYS) if last else None


REPEAT_DAYS = 14  # a story whose key carries the day it was found (a team's form "now") is not told again within this
# Kinds whose key carries the day only to mark the cycle's window of games: each cycle's is a new story (new games).
WINDOW_KINDS = {"knockout_round", "continental_round", "european_nights", "international_round", "national_team",
                "tournament_round_up", "our_players_at_tournament"}


def story_of(key: str, day: date) -> str:
    """A lead key without the day it was found on (a slug stamped with the cycle's day or month), so the same story found
    at a later import is known as the same; a key whose date is the event's own (a match, a round's first game) is kept."""
    head, _, last = key.rpartition(":")
    return head if head and last in (f"{day:%Y-%m-%d}", f"{day:%Y-%m}") else key


def printed_stories(until: date | None = None) -> dict[str, date]:
    """Every lead key printed or drafted in any paper, and each one's story (story_of), with the last day it was printed:
    a story is told once, in one paper (3 Nov 2026: the same Champions League preview on 13 and 16 Feb, the same derby
    preview in two papers)."""
    found: dict[str, date] = {}
    rows = (db.session.query(WorldSoccerArticle.lead_key, WorldSoccerIssue.publication_date).join(WorldSoccerIssue)
            .filter(WorldSoccerArticle.lead_key.isnot(None)).all())
    for key, day in rows:
        if until and day > until:
            continue
        for name in {key, story_of(key, day)}:
            if name not in found or day > found[name]:
                found[name] = day
    return found


def _told(lead: Lead, stories: dict[str, date] | None, until: date | None) -> bool:
    """Whether another paper (or an earlier cycle) told this story: its key printed anywhere, or its day-stamped story within
    REPEAT_DAYS. A story for one paper (Lead.outlet_id) is that paper's own and is left to its printed keys."""
    if not stories or lead.outlet_id is not None:
        return False
    if lead.key in stories:
        return True
    if lead.kind in WINDOW_KINDS:
        return False
    story = story_of(lead.key, until or lead.date)
    return story != lead.key and story in stories and until is not None and (until - stories[story]).days < REPEAT_DAYS


def printed_keys(outlet: Outlet) -> set[str]:
    """The lead keys this outlet has already printed or drafted."""
    rows = (db.session.query(WorldSoccerArticle.lead_key).join(WorldSoccerIssue)
            .filter(WorldSoccerIssue.outlet_id == outlet.id, WorldSoccerArticle.lead_key.isnot(None)).all())
    return {key for (key,) in rows}


# At most this many stories of one kind in an edition, so one kind does not fill a paper (an outlet
# whose leads are all of one kind, like The Transfer Wire, is not limited).
KIND_LIMITS = {"scouting": 3, "legend_profile": 1, "career_end": 1, "milestone": 1, "club_record": 1, "misfiring": 1, "clinical": 1, "tired_legs": 1, "academy": 2, "dynasty": 1, "great_final": 1,
               "where_now": 1, "appointment": 2, "performance": 2, "sending_off": 1, "smash_and_grab": 1,
               "league_round": 2, "run": 2, "derby": 2, "giant_killing": 2, "preview": 2, "injury_blow": 2, "underperformer": 1, "overperformer": 1,
               "expectations": 1, "check_in": 1, "stat_watch": 1, "system_change": 1, "position_change": 1, "dropped": 2, "breakthrough": 1,
               "partnership": 1, "tactical_trend": 1, "big_win": 2, "unsettled": 2, "dressing_room": 1, "settled": 1, "expected_points": 1, "continental_round": 2,
               "tie_report": 2, "tie_preview": 2, "knockout_round": 2, "knockout_preview": 2, "european_nights": 1, "international_round": 2, "national_team": 1, "promised_report": 4, "transfer_interest": 2, "transfer_bid": 2,
               "tournament_report": 6, "tournament_preview": 4, "tournament_round_up": 1, "our_players_at_tournament": 1, "player_of_the_tournament": 1,
               "club_report": 8, "club_preview": 8, "scouting_follow_up": 3, "kings_wage": 1, "contract_running_down": 2, "manager_out": 2,
               "travel_log": 1, "interview": 1, "war_of_words": 1,
               "under_pressure": 2, "crisis": 2, "crisis_over": 1, "vacancy": 1, "transfer_rumour": 2, "loan_offer": 2}
# While the leagues play (a league round-up is among the leads), a news paper (the world or a nation beat) reports
# what happens: its front story is news of the season, current news ranks higher, and it prints at most one story
# that looks back, one scouting report and three transfers. The history, young and transfer papers are as before.
NOSTALGIA = {"legend", "legend_profile", "where_now", "anniversary", "all_time", "career_end", "dynasty", "great_final"}
SEASON_NEWS = {"league_round", "misfiring", "clinical", "tired_legs", "run", "derby", "preview", "injury_blow", "underperformer", "overperformer", "expectations", "check_in",
               "stat_watch", "title", "giant_killing", "big_win", "performance", "sending_off", "smash_and_grab", "system_change",
               "position_change", "dropped", "breakthrough", "partnership", "unsettled", "dressing_room", "settled", "expected_points",
               "continental_round", "tie_report", "tie_preview", "knockout_round", "knockout_preview", "european_nights", "international_round", "national_team", "promised_report", "transfer_interest", "transfer_bid",
               "tournament_report", "tournament_preview", "tournament_round_up", "our_players_at_tournament", "player_of_the_tournament",
               "club_report", "club_preview", "contract_running_down", "manager_out", "war_of_words",
               "under_pressure", "crisis", "crisis_over", "vacancy", "transfer_rumour", "loan_offer"}
SEASON_NEWS_BOOST = 1.25
# The market's kinds: the transfer paper's own; a news paper damps them and never leads with one (World Soccer led with a
# Saudi club's list three cycles in four, 8 to 19 Dec 2063, while Liverpool 1-3 Everton went unwritten).
TRANSFER_KINDS = {"transfer_interest", "transfer_bid", "kings_wage", "contract_running_down", "transfer_rumour", "loan_offer"}
TRANSFER_IN_NEWS = 0.7
# The season's reading of the numbers and the shape: good reading, but no news (4 Nov 2026: "Villarreal's nine-point
# mirage", an expected-points piece, led The Spanish Daily). A news paper damps them below the match news; By the
# Numbers (the numbers beat) keeps them.
NUMBERS_KINDS = {"expected_points", "misfiring", "clinical", "underperformer", "overperformer", "expectations", "stat_watch",
                 "tired_legs", "partnership", "system_change", "position_change", "check_in"}
NUMBERS_IN_NEWS = 0.7
# A kind (or a tag of a lead) printed anywhere within this many game days is not printed again: once a month for a king's
# wage and for a story of a club shopping widely ('Cash is king' every cycle, 14 to 19 Dec 2063).
COOLDOWN_DAYS = {"kings_wage": 28, "shopping": 28, "contract_running_down": 14}
NEWS_MIN_PAGES = 4  # a news paper in season prints at least this many pages, however short the cycle
IN_SEASON_LIMITS = {"scouting": 1, "transfer": 3, "spending": 1}
NOSTALGIA_IN_SEASON = 1
# Looking back, less (27 Oct 2026): a news paper scores the legends, farewells and landmarks lower, all year.
LANDMARKS = {"milestone", "club_record"}
BACKWARD_IN_NEWS = 0.6
# While the leagues play, a news paper carries at least this many of these kinds when it has them: they take the places of
# its weakest other stories (the front story stays).
IN_SEASON_MINIMUM = {"promised_report": 2, "preview": 1, "continental_round": 1, "european_nights": 1, "national_team": 1,
                     "tournament_report": 4, "tournament_preview": 2, "tournament_round_up": 1, "our_players_at_tournament": 1}
# The followed clubs' own paper (the home beat) carries every one of their games: the reports of the cycle and the previews
# of the next two weeks, all of them, up to its size less two places for other news.
HOME_MINIMUM = {"club_report": 99, "club_preview": 99}
# A column is its own page: when the paper has a lead for it, it is printed beyond the paper's size, so the news of a
# short cycle (three pages for four days) does not push it out. The Away Days column found no page on 1 Dec 2063.
COLUMN_KINDS = ("travel_log",)
# Kinds with a length of their own wherever they are printed: the column, and the match-day article that covers a round.
LONG_KINDS = {"travel_log": "column", "league_round": "matchday", "knockout_round": "matchday", "knockout_preview": "matchday"}
HOME_OTHER_NEWS = 2


def news_paper(outlet: Outlet) -> bool:
    return any(beat == "world" or beat.startswith("nation:") for beat in outlet.beats) \
        and not {"history", "young", "transfers"} & set(outlet.beats)


def outlet_score(lead: Lead, outlet: Outlet, in_season: bool = False) -> float:
    """A lead's score for one outlet: the followed clubs count more only on the outlets of the home beat, and the
    season's news more on the news papers while the leagues play."""
    home = "home" in outlet.beats and "home" in lead.beats
    now = in_season and lead.kind in SEASON_NEWS and news_paper(outlet)
    market = lead.kind in TRANSFER_KINDS and news_paper(outlet)
    backward = lead.kind in NOSTALGIA | LANDMARKS and news_paper(outlet)
    numbers = lead.kind in NUMBERS_KINDS and news_paper(outlet) and "numbers" not in outlet.beats
    return (lead.score * (story_leads.FOLLOWED_BOOST if home else 1.0) * (SEASON_NEWS_BOOST if now else 1.0)
            * (TRANSFER_IN_NEWS if market else 1.0) * (BACKWARD_IN_NEWS if backward else 1.0) * (NUMBERS_IN_NEWS if numbers else 1.0))


def cooldown_tags(lead: Lead) -> set[str]:
    """What a lead counts as for COOLDOWN_DAYS: its kind, and 'shopping' for interest from a club shopping widely."""
    tags = {lead.kind}
    if lead.kind == "transfer_interest" and lead.facts.get("also_on_the_lists_of"):
        tags.add("shopping")
    return tags


def last_printed_tags(until: date | None = None) -> dict[str, date]:
    """When each cooldown tag was last printed, in any paper (needs an app context)."""
    rows = (db.session.query(WorldSoccerArticle.lead, WorldSoccerIssue.publication_date).join(WorldSoccerIssue)
            .filter(WorldSoccerArticle.lead.isnot(None)).order_by(WorldSoccerIssue.publication_date.desc()).limit(600).all())
    found: dict[str, date] = {}
    for stored, day in rows:
        if until and day > until:
            continue
        lead = json.loads(stored) if isinstance(stored, str) else (stored or {})
        tags = {lead.get("kind")}
        if lead.get("kind") == "transfer_interest" and (lead.get("facts") or {}).get("also_on_the_lists_of"):
            tags.add("shopping")
        for tag in tags & set(COOLDOWN_DAYS):
            if tag not in found or day > found[tag]:
                found[tag] = day
    return found


def cooling(lead: Lead, last_printed: dict[str, date] | None, until: date | None) -> bool:
    """Whether a lead waits: one of its tags was printed within its cooldown."""
    if not last_printed or until is None:
        return False
    return any(tag in last_printed and (until - last_printed[tag]).days < COOLDOWN_DAYS[tag] for tag in cooldown_tags(lead) & set(COOLDOWN_DAYS))


MIN_PAGES = 2  # a paper's smallest edition, however short the cycle


def edition_size(outlet: Outlet, days: int | None, in_season: bool = False) -> int:
    """How many stories the paper prints this cycle: its full size for a fortnight or more, fewer for a shorter cycle in
    proportion (two days give two pages), never under MIN_PAGES, nor under NEWS_MIN_PAGES for a news paper in season (a
    two-page English Daily held Chelsea's game and the next preview and nothing else). No cycle length given: the full size."""
    if days is None or days >= CYCLE_DAYS:
        return outlet.articles_per_edition
    floor = NEWS_MIN_PAGES if in_season and news_paper(outlet) else MIN_PAGES
    return max(floor, min(outlet.articles_per_edition, math.ceil(outlet.articles_per_edition * max(days, 0) / CYCLE_DAYS)))


def plan_editions(leads: list[Lead], outlets: list[Outlet], printed: dict[int, set[str]], days: int | None = None,
                  last_printed: dict[str, date] | None = None, until: date | None = None,
                  stories: dict[str, date] | None = None) -> list[Plan]:
    """Each active outlet's articles: its leads, best first for it, within the limits per kind, with their prominence. The
    paper's size follows the days the cycle covers (edition_size). A lead whose kind or tag was printed within its cooldown
    (COOLDOWN_DAYS, ``last_printed`` as of ``until``) waits."""
    plans = []
    in_season = any(lead.kind == "league_round" for lead in leads)
    leads = [lead for lead in leads if not cooling(lead, last_printed, until) and not _told(lead, stories, until)]
    taken: set[str] = set()  # a story goes to one paper a cycle: the club's own paper chooses first (it carries every game)
    for outlet in sorted(outlets, key=lambda o: "home" not in o.beats):
        if not outlet.active:
            continue
        room = edition_size(outlet, days, in_season)
        season_rules = in_season and news_paper(outlet)
        printed_games = {(game, _story_class(key.split(":")[0])) for key in printed.get(outlet.id, set()) if (game := _match_of_key(key))}
        candidates = sorted((lead for lead in leads if outlet_service.outlets_for(lead, [outlet]) and lead.outlet_id in (None, outlet.id)
                             and lead.key not in printed.get(outlet.id, set()) and not _printed_already(lead, printed_games)
                             and (lead.key not in taken or lead.outlet_id is not None)),
                            key=lambda lead: -outlet_score(lead, outlet, in_season))
        one_kind = len({lead.kind for lead in candidates}) == 1
        limits = {**KIND_LIMITS, **IN_SEASON_LIMITS} if season_rules else KIND_LIMITS
        chosen, per_kind = [], Counter()
        for lead in candidates:
            if len(chosen) == room:
                break
            if not one_kind and per_kind[lead.kind] >= limits.get(lead.kind, room):
                continue
            if _same_game(lead, chosen):  # one story of a game per paper: the best of its preview and report leads
                continue
            if season_rules and lead.kind in NOSTALGIA and sum(per_kind[k] for k in NOSTALGIA) >= NOSTALGIA_IN_SEASON:
                continue
            chosen.append(lead)
            per_kind[lead.kind] += 1
        if season_rules:
            _meet_minimum(chosen, candidates, per_kind, lambda lead: outlet_score(lead, outlet, in_season), room)
        if "home" in outlet.beats:  # the club's own paper: every game of the followed clubs, before anything else
            _meet_minimum(chosen, candidates, per_kind, lambda lead: outlet_score(lead, outlet, in_season), room,
                          {kind: max(1, room - HOME_OTHER_NEWS) for kind in HOME_MINIMUM}, protect_lead=False)  # a thin edition still has its club's game
        for kind in COLUMN_KINDS:
            if per_kind[kind] == 0 and (column := next((c for c in candidates if c.kind == kind), None)):
                chosen.append(column)
                per_kind[kind] += 1
        if not chosen:
            continue
        taken.update(lead.key for lead in chosen)
        front = next((lead for lead in chosen if lead.kind in SEASON_NEWS and lead.kind not in TRANSFER_KINDS), None) if season_rules else None
        if "home" in outlet.beats:  # the club's own paper leads with its game's report when there is one
            front = next((lead for lead in chosen if lead.kind == "club_report"), front)
        if front is not None:
            chosen.remove(front)
            chosen.insert(0, front)
        rest = "feature" if FEATURE_BEATS & set(outlet.beats) else None
        items = [(chosen[0], LONG_KINDS.get(chosen[0].kind, "lead"))]
        for index, lead in enumerate(chosen[1:]):  # a column is always the long column (27 Oct 2026: Sutton had no meat)
            items.append((lead, LONG_KINDS.get(lead.kind) or rest or ("standard" if index < STANDARD_ARTICLES else "brief")))
        plans.append(_staffed(Plan(outlet, items)))
    return sorted(plans, key=lambda plan: outlets.index(plan.outlet))


def _meet_minimum(chosen: list[Lead], candidates: list[Lead], per_kind: Counter, score: Callable[[Lead], float], room: int,
                  minimum: dict[str, int] | None = None, protect_lead: bool = True) -> None:
    """Put in the kinds a news paper must carry in season (IN_SEASON_MINIMUM, or the given minimum), best first: in a free
    place, or in the place of the weakest chosen story whose kind is not short of its minimum (never the best story, which
    leads, unless ``protect_lead`` is off: the home paper's game report outranks any lead; a two-page edition of 8 Dec 2063
    kept a transfer story and the next preview and had no page for Chelsea 3-0 Arsenal). A story of a game already chosen
    (its match ref) is never added twice."""
    minimum = IN_SEASON_MINIMUM if minimum is None else minimum
    for kind, wanted in minimum.items():
        for lead in [c for c in candidates if c.kind == kind and c not in chosen and not _same_game(c, chosen)][:max(0, wanted - per_kind[kind])]:
            if len(chosen) >= room:
                spare = [c for c in (chosen[1:] if protect_lead else chosen) if per_kind[c.kind] > minimum.get(c.kind, 0)]
                if not spare:
                    break
                weakest = min(spare, key=score)
                chosen.remove(weakest)
                per_kind[weakest.kind] -= 1
            chosen.append(lead)
            per_kind[lead.kind] += 1
    chosen.sort(key=lambda lead: -score(lead))


PREVIEW_KINDS = {"preview", "club_preview", "tournament_preview"}


def _story_class(kind: str) -> str:
    """A story of a game is a preview or a report: a paper prints one of each, never two reports (a followed club's game
    report beside the promised one) and never a preview twice. A preview does not block the report that keeps its promise
    (Middlesbrough 0-1 Chelsea, 4 Dec 2063, had its preview and no report)."""
    return "preview" if kind in PREVIEW_KINDS else "report"


def _printed_already(lead: Lead, printed_games: set[tuple[str, str]]) -> bool:
    match = next((r for r in lead.refs if r.startswith("match:")), None)
    return match is not None and (match, _story_class(lead.kind)) in printed_games


def _match_of_key(key: str) -> str | None:
    """The match ref inside a printed lead's key ("preview:match:2063-10-05:630:662:2063" -> "match:2063-10-05:630:662")."""
    parts = key.split(":")
    if "match" in parts and len(parts) >= parts.index("match") + 4:
        at = parts.index("match")
        return ":".join(parts[at:at + 4])
    return None


def _same_game(lead: Lead, chosen: list[Lead]) -> bool:
    """Whether a story of this game (a preview, a report) is already among the chosen."""
    match = next((r for r in lead.refs if r.startswith("match:")), None)
    return match is not None and any(match in c.refs for c in chosen)


COLUMNISTS = {"travel_log": features.COLUMNIST}  # a column keeps its one writer, whatever the paper's staff


def _staffed(plan: Plan, rng: random.Random | None = None) -> Plan:
    """Deal the outlet's writers round the articles (a different one leads each time) and give every
    article its own opening and headline form."""
    rng = rng or random.Random()
    staff = [w.as_dict() for w in plan.outlet.staff] or [article_writer.pick_writer(plan.outlet)]
    start = rng.randrange(len(staff))
    plan.writers = [COLUMNISTS.get(lead.kind) or staff[(start + i) % len(staff)] for i, (lead, _) in enumerate(plan.items)]
    plan.forms = article_writer.deal_forms(len(plan.items), rng)
    return plan


def make_editions(since: date | None = None, until: date | None = None, progress: Callable[[str], None] = log.info,
                  publish: bool = True, illustrate: bool = False, min_days: int = 0) -> CycleResult | None:
    """Find the cycle's leads, write every outlet's articles and save one edition per outlet, published
    unless ``publish`` is false. After an import ``min_days`` is CYCLE_DAYS: a few days of game time
    are not worth a new edition of every paper."""
    if since is None:
        window = cycle_window(until, min_days)
        if window is None:
            due = next_cycle_due()
            progress(f"The next papers come out from {due:%-d %B %Y} (game date)." if min_days and due
                     else "Nothing new since the last editions.")
            return None
        since, until = window
    if until is None:
        with db.engines[world_models.BIND].connect() as connection:
            until = q.game_date(connection)
    progress(f"Finding the stories from {since} to {until}")
    with db.engines[world_models.BIND].connect() as connection:
        leads = story_leads.find_leads(connection, since, until)
    outlets = outlet_service.ensure_outlets()
    plans = plan_editions(leads, outlets, {o.id: printed_keys(o) for o in outlets}, days=(until - since).days,
                          last_printed=last_printed_tags(until), until=until, stories=printed_stories(until))
    result = CycleResult(since, until, leads=len(leads))
    # a writer cross-references only stories the papers print this cycle ("today's look at the dynasty" that no paper ran)
    printed_now = list({lead.key: lead for plan in plans for lead, _ in plan.items}.values())
    jobs = [(plan.outlet, lead, prominence, plan.writers[i], plan.forms[i], earlier_coverage(lead, until),
             related_stories(lead, printed_now)) for plan in plans for i, (lead, prominence) in enumerate(plan.items)]
    progress(f"{len(leads)} leads; writing {len(jobs)} articles for {len(plans)} outlets")
    drafts = _write_all(jobs, until, progress)
    for plan in plans:
        issue = WorldSoccerIssue(outlet_id=plan.outlet.id, edition_name=plan.outlet.name, publication_date=until,
                                 knowledge_cutoff=until, status="published" if publish else "draft",
                                 published_at=datetime.now() if publish else None)
        db.session.add(issue)
        order = 0
        for lead, prominence in plan.items:
            draft = drafts.get((plan.outlet.id, lead.key))
            if isinstance(draft, str):
                result.refused.append(f"{plan.outlet.name}: {lead.headline} ({draft})")
                continue
            order += 1
            _add_article(issue, lead, prominence, draft, order)
            result.written += 1
        if order:
            lead_article = min(issue.articles, key=lambda a: a.sort_order)
            issue.headline, issue.standfirst = lead_article.headline, lead_article.standfirst
            db.session.flush()
            result.editions.append(issue.id)
        else:
            db.session.expunge(issue)
    db.session.commit()
    if illustrate and not current_app.testing:  # tests never call Draw Things
        from app.services import article_pictures

        fronts = [min(db.session.get(WorldSoccerIssue, issue_id).articles, key=lambda a: a.sort_order) for issue_id in result.editions]
        result.illustrated = article_pictures.illustrate_front_pages(fronts, progress)
    progress(f"Done: {len(result.editions)} {'published' if publish else 'draft'} editions, {result.written} articles"
             + (f", {len(result.refused)} refused" if result.refused else ""))
    return result


def _write_all(jobs: list[tuple[Outlet, Lead, str, dict, dict, list, list]], published: date,
               progress: Callable[[str], None]) -> dict:
    """Every article, written at the same time by WRITERS threads: {(outlet id, lead key): draft or the refusal}."""
    app = current_app._get_current_object()
    done: dict = {}
    outlets = {outlet.id: (outlet.name, outlet.tagline, outlet.voice, outlet.byline) for outlet, *_ in jobs}

    def write(job) -> tuple:
        outlet, lead, prominence, writer, form, earlier, related = job
        name, tagline, voice, byline = outlets[outlet.id]
        detached = Outlet(id=outlet.id, name=name, tagline=tagline, voice=voice, byline=byline)
        with app.app_context():
            try:
                return (outlet.id, lead.key), article_writer.write_article(detached, lead, published, prominence, writer, form, earlier, related)
            except StoryError as exc:
                app.logger.warning("article refused for %s (%s): %s", lead.key, name, exc)
                return (outlet.id, lead.key), str(exc)
            except Exception as exc:  # noqa: BLE001 - one broken article must not stop the cycle
                app.logger.exception("article for %s failed", lead.key)
                return (outlet.id, lead.key), f"error: {exc}"
            finally:
                db.session.remove()

    with ThreadPoolExecutor(max_workers=WRITERS) as pool:
        for number, (key, draft) in enumerate(pool.map(write, jobs), 1):
            done[key] = draft
            progress(f"Written {number} of {len(jobs)}")
    return done


REGIONAL = {"continental_round", "european_nights", "tie_report", "tie_preview", "knockout_round"}


def section_of(lead: Lead) -> str:
    """The paper section of a lead: by its kind, and a continental story by its part of the world (the Copa Libertadores
    was printed under "Europe", 2 Nov 2026): Europe, or South America, Asia, Africa, North America, the world."""
    if lead.kind in REGIONAL and (lead.facts or {}).get("region") not in (None, "Europe"):
        return lead.facts["region"]
    return SECTIONS.get(lead.kind, "News")


def _add_article(issue: WorldSoccerIssue, lead: Lead, prominence: str, draft: dict, order: int) -> WorldSoccerArticle:
    article = WorldSoccerArticle(issue=issue, section=section_of(lead), article_type=ARTICLE_TYPE[prominence],
                                 headline=draft["headline"][:300], standfirst=draft["standfirst"] or None, body=draft["body"],
                                 byline=draft["byline"], prominence=ISSUE_PROMINENCE[prominence], sort_order=order,
                                 lead_key=lead.key, lead=article_writer.lead_to_dict(lead) | ({"verdict": draft["verdict"]} if draft.get("verdict") else {})
                                 | ({"picture": draft["picture"]} if draft.get("picture") else {}),
                                 model=draft["model"], writer_id=draft.get("writer_id"))
    db.session.add(article)
    set_links(article, lead.refs)
    key = thread_key(lead)
    title = next((link.label for link in article.entity_links if link.world_ref == key), None)
    article.story_thread = _thread(key, title, issue.publication_date)
    return article


def set_links(article: WorldSoccerArticle, refs: list[str]) -> None:
    """The article's "In this story" links, one per world reference of its lead."""
    article.entity_links.clear()
    seen = set()
    for ref in refs:
        kind, _, rest = ref.partition(":")
        parts = rest.split(":")
        if kind == "match":
            entity_id = int(parts[1])  # the home side; the world_ref names the match
        else:
            entity_id = int(parts[0])
        if (kind, entity_id) in seen:
            continue
        seen.add((kind, entity_id))
        article.entity_links.append(WorldSoccerEntityLink(entity_type=kind, entity_id=entity_id, world_ref=ref,
                                                          label=world_ref_context(ref)["label"][:240]))


def publish(issue: WorldSoccerIssue) -> None:
    issue.status, issue.published_at = "published", datetime.now()
    _sync_issue_headline(issue)
    db.session.commit()


def unpublish(issue: WorldSoccerIssue) -> None:
    """Take a published edition back to the desk, to correct it."""
    issue.status, issue.published_at = "draft", None
    db.session.commit()


def rewrite_article(article: WorldSoccerArticle) -> WorldSoccerArticle:
    """Write a draft article again from its lead, in its outlet's voice and at the same length."""
    if article.issue.status != "draft":
        raise StoryError("Only a draft edition's articles can be written again.")
    if not article.lead or article.issue.outlet is None:
        raise StoryError("This article has no story lead to write from.")
    lead = article_writer.lead_from_dict(article.lead)
    prominence = LONG_KINDS.get(lead.kind) or {value: key for key, value in ISSUE_PROMINENCE.items()
                                               if key not in ("column", "matchday")}.get(article.prominence, "standard")
    writer = COLUMNISTS.get(lead.kind) or (article.writer.as_dict() if article.writer else None)  # a column keeps its columnist
    earlier = earlier_coverage(lead, article.issue.knowledge_cutoff, leave_out=article.id)
    draft = article_writer.write_article(article.issue.outlet, lead, article.issue.knowledge_cutoff, prominence, writer,
                                         earlier=earlier)
    article.headline, article.standfirst, article.body = draft["headline"][:300], draft["standfirst"] or None, draft["body"]
    article.model, article.byline, article.writer_id = draft["model"], draft["byline"], draft.get("writer_id")
    article.lead = {k: v for k, v in article.lead.items() if k != "picture"} | ({"picture": draft["picture"]} if draft.get("picture") else {})
    _sync_issue_headline(article.issue)
    db.session.commit()
    return article


def move_article(article: WorldSoccerArticle, step: int) -> None:
    """Move an article up (step -1) or down (+1) in its draft edition; the first one leads."""
    articles = sorted(article.issue.articles, key=lambda a: (a.sort_order, a.id))
    index = articles.index(article)
    other = index + step
    if 0 <= other < len(articles):
        articles[index], articles[other] = articles[other], articles[index]
        for order, item in enumerate(articles, 1):
            item.sort_order = order
        _sync_issue_headline(article.issue)
    db.session.commit()


def _sync_issue_headline(issue: WorldSoccerIssue) -> None:
    if issue.articles:
        first = min(issue.articles, key=lambda a: (a.sort_order, a.id))
        issue.headline, issue.standfirst = first.headline, first.standfirst


def outlet_of(issue: WorldSoccerIssue) -> Outlet | None:
    """The issue's outlet; the World Soccer issues made before the outlets are World Soccer's."""
    if issue.outlet is not None:
        return issue.outlet
    return Outlet.query.filter_by(slug="world-soccer").first()


def articles_about(world_ref: str, limit: int = 12) -> list[WorldSoccerArticle]:
    """Published articles linked to one person, club or competition, newest first."""
    return (WorldSoccerArticle.query.join(WorldSoccerIssue).join(WorldSoccerEntityLink)
            .filter(WorldSoccerEntityLink.world_ref == world_ref, WorldSoccerIssue.status == "published")
            .order_by(WorldSoccerIssue.publication_date.desc(), WorldSoccerArticle.sort_order).limit(limit).all())
