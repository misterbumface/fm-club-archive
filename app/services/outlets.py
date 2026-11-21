"""The news outlets and the followed clubs (docs/media-plan.md, step 3).

The first set of outlets is made the first time it is needed; after that the curator renames
them and changes their voices and beats on Settings › Outlets. Names are working names.
"""

from __future__ import annotations

import json

from app.extensions import db
from app.models import AppSetting
from app.models.outlet import Outlet, Writer
from app.services.writer_personas import FIRST_STAFF
from app.services.story_leads import DEFAULT_FOLLOWED, FOLLOWED_SETTING, Lead, followed_clubs

ENGLAND, SPAIN, ITALY, GERMANY, FRANCE = 765, 796, 776, 771, 769

_DAILY_VOICE = (
    "A national daily newspaper for {country}, written in English. Local and partisan: it cares most about {adjective} clubs, "
    "{adjective} players at home and abroad and the national team, and says so. Lively, opinionated headlines; exact facts."
)

DEFAULT_OUTLETS = [
    {"slug": "world-soccer", "name": "World Soccer", "tagline": "The world game, every month",
     "voice": "A measured global monthly with authority and perspective. It puts each story in its world context: what it means "
              "for the club, the nation and the game. Calm, precise, never breathless.",
     "beats": ["world"], "byline": "World Soccer staff", "articles_per_edition": 8, "min_score": 60},
    {"slug": "transfer-wire", "name": "The Transfer Wire", "tagline": "Every deal, first",
     "voice": "A fast, punchy tabloid about the transfer market. Short sentences, strong verbs, the fee and the stakes in the "
              "first line, playful headlines. Only completed moves: no rumours, no agents, no 'sources say'.",
     "beats": ["transfers"], "byline": "Transfer Wire desk", "articles_per_edition": 8, "min_score": 40},
    {"slug": "next-gen", "name": "Next Gen", "tagline": "The players of tomorrow",
     "voice": "An enthusiastic scouting magazine about young players. It reads age and numbers with a scout's eye and asks what "
              "the next step could be. Warm and hopeful, and not afraid to dream about what a player could become.",
     "beats": ["young"], "byline": "Next Gen scouting team", "articles_per_edition": 6, "min_score": 30},
    {"slug": "long-ball", "name": "The Long Ball", "tagline": "Football, remembered",
     "voice": "A reflective long-read magazine about football history. Unhurried prose with perspective across decades; it "
              "connects the past with the present and lets great careers and old finals breathe.",
     "beats": ["history"], "byline": "The Long Ball", "articles_per_edition": 4, "min_score": 30},
    {"slug": "by-the-numbers", "name": "By the Numbers", "tagline": "The game in figures",
     "voice": "A dry, data-led column. It leads with the number, sets it against records and averages from the facts, and lets "
              "the figures carry the story, with wry understatement.",
     "beats": ["numbers"], "byline": "By the Numbers", "articles_per_edition": 5, "min_score": 30},
    {"slug": "english-daily", "name": "The English Daily", "tagline": None,
     "voice": _DAILY_VOICE.format(country="England", adjective="English"),
     "beats": [f"nation:{ENGLAND}", "home"], "byline": "Sports desk", "articles_per_edition": 8, "min_score": 30},
    {"slug": "spanish-daily", "name": "The Spanish Daily", "tagline": None,
     "voice": _DAILY_VOICE.format(country="Spain", adjective="Spanish"),
     "beats": [f"nation:{SPAIN}"], "byline": "Sports desk", "articles_per_edition": 6, "min_score": 30},
    {"slug": "italian-daily", "name": "The Italian Daily", "tagline": None,
     "voice": _DAILY_VOICE.format(country="Italy", adjective="Italian"),
     "beats": [f"nation:{ITALY}"], "byline": "Sports desk", "articles_per_edition": 6, "min_score": 30},
    {"slug": "german-daily", "name": "The German Daily", "tagline": None,
     "voice": _DAILY_VOICE.format(country="Germany", adjective="German"),
     "beats": [f"nation:{GERMANY}"], "byline": "Sports desk", "articles_per_edition": 6, "min_score": 30},
    {"slug": "french-daily", "name": "The French Daily", "tagline": None,
     "voice": _DAILY_VOICE.format(country="France", adjective="French"),
     "beats": [f"nation:{FRANCE}"], "byline": "Sports desk", "articles_per_edition": 6, "min_score": 30},
]


def ensure_outlets() -> list[Outlet]:
    """Every outlet, in order; the first set is made when there is none, and an outlet with no
    writers gets its first staff (writer_personas.FIRST_STAFF)."""
    if not db.session.query(Outlet.id).first():
        for order, row in enumerate(DEFAULT_OUTLETS):
            db.session.add(Outlet(sort_order=order, active=True, **row))
        db.session.commit()
    outlets = Outlet.query.order_by(Outlet.sort_order, Outlet.id).all()
    staffed = {outlet_id for (outlet_id,) in db.session.query(Writer.outlet_id).distinct()}
    added = False
    for outlet in outlets:
        staff = FIRST_STAFF.get(outlet.slug) or (FIRST_STAFF["tournament-special-2"] if outlet.slug.startswith(TOURNAMENT_SLUG) else None)
        if outlet.id not in staffed and staff:
            for order, row in enumerate(staff):
                db.session.add(Writer(outlet=outlet, sort_order=order, active=True, **row))
            added = True
    if added:
        db.session.commit()
    return outlets


# The paper of a major international tournament (18 Nov 2026: "a new paper dedicated to that tournament. Like in real life!"):
# made the first time one is in play, named after the tournament in play ("Euro 2064"), and printed only while one is in play
# (from two weeks before the first game, tournament_leads.LEAD_DAYS). Its stories are those on the tournament beat: the games,
# the groups, the field, the finals squads.
TOURNAMENT_SLUG = "tournament-special"
TOURNAMENT_NAME_SETTING = "tournament_paper_for"  # the tournament the paper was last named for: a curator's rename stays
SHORT_NAMES = {"European Football Championship": "Euro", "Africa Cup of Nations": "AFCON", "Confederations Cup": "Confed Cup"}
_TOURNAMENT_VOICE = ("The daily paper of the {name}, from the build-up to the final, written in English for neutral fans everywhere. "
                     "Every group, every squad, every game: who is in, who missed the cut, who can win it. Big-occasion energy "
                     "and exact facts.")


def tournament_paper(label: str | None) -> Outlet | None:
    """The paper of the one tournament in play (``label``: "European Football Championship 2064"); None, and the papers
    put away, when none is."""
    papers = tournament_papers([label] if label else [])
    return papers[0] if papers else None


def tournament_papers(labels: list[str]) -> list[Outlet]:
    """A paper for each tournament in play, the biggest first (21 Nov 2026: the Euro and the Copa América together). The
    first keeps the slug the papers always had; the others get one of their own. Each takes its tournament's beat; the
    first also the plain "tournament" beat. A paper whose tournament is over is put away."""
    papers = [_tournament_paper(index, label) for index, label in enumerate(labels)]
    for outlet in Outlet.query.filter(Outlet.slug.like(f"{TOURNAMENT_SLUG}%")).all():
        if outlet not in papers and outlet.active:
            outlet.active = False
    db.session.commit()
    if papers:
        ensure_outlets()  # their first staff
    return papers


def _tournament_paper(index: int, label: str) -> Outlet:
    slug = TOURNAMENT_SLUG if index == 0 else f"{TOURNAMENT_SLUG}-{index + 1}"
    key = TOURNAMENT_NAME_SETTING if index == 0 else f"{TOURNAMENT_NAME_SETTING}-{index + 1}"
    beats = (["tournament"] if index == 0 else []) + [f"tournament:{label}"]
    outlet = Outlet.query.filter_by(slug=slug).first()
    name = next((label.replace(long, short, 1) for long, short in SHORT_NAMES.items() if label.startswith(long)), label)
    if outlet is None:
        last = db.session.query(db.func.max(Outlet.sort_order)).scalar() or 0
        outlet = Outlet(slug=slug, name=name, tagline="Every group, every squad, every game", voice=_TOURNAMENT_VOICE.format(name=name),
                        beats=beats, byline=f"{name} desk", articles_per_edition=8, min_score=30, sort_order=last + 1, active=True)
        db.session.add(outlet)
    setting = AppSetting.query.filter_by(key=key).first()
    if setting is None or setting.value != label:  # a new tournament: its own name
        outlet.name, outlet.byline, outlet.voice = name, f"{name} desk", _TOURNAMENT_VOICE.format(name=name)
        if setting is None:
            db.session.add(AppSetting(key=key, value=label))
        else:
            setting.value = label
    outlet.beats = beats
    outlet.active = True
    db.session.flush()
    return outlet


def outlets_for(lead: Lead, outlets: list[Outlet]) -> list[Outlet]:
    """The active outlets whose beat the lead is on and for whom its score is news."""
    return [o for o in outlets if o.active and o.takes(lead.beats) and lead.score >= o.min_score]


def set_followed_clubs(uids: list[int]) -> None:
    setting = AppSetting.query.filter_by(key=FOLLOWED_SETTING).first()
    value = json.dumps(sorted(set(int(u) for u in uids)))
    if setting is None:
        db.session.add(AppSetting(key=FOLLOWED_SETTING, value=value))
    else:
        setting.value = value


__all__ = ["DEFAULT_FOLLOWED", "DEFAULT_OUTLETS", "ensure_outlets", "followed_clubs", "outlets_for", "set_followed_clubs", "tournament_paper",
           "tournament_papers"]
