"""The editor: a cheap model reads every paper's candidate stories of a cycle and decides what each paper prints, in what
order, full or brief, and which few stories are the cycle's biggest (21 Nov 2026: "We don't have an editor, we just have a
point system which sometimes doesn't make any sense. Copa America is going on, and we don't have even one single article
about it. Euro is going on and the lead story is about Utrecht's manager retiring").

The points system still finds the stories and gives each paper its candidates, best first by points; the editor only
chooses among them. What a paper must carry stays whatever the editor says (the home paper's own games, a column). When the
editor cannot be asked (no key, an error, an answer that names no story), the points system's plan is printed as before.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from app.services.story_leads import Lead

log = logging.getLogger(__name__)

POOL = 24  # the candidates each paper shows the editor, best first by points
SIZES = ("full", "brief")
CHEAP_MODELS = ("gpt-6-luna", "deepseek-flash")  # the editor is always a cheap hosted model
FACT_WORDS = 160  # characters of a candidate's short facts

RULES = """You are the editor-in-chief of a group of football papers in a football world (a Football Manager save). Each cycle
the reporters bring you candidate stories for each paper. You decide what each paper prints.

For each paper, choose its stories from ITS OWN candidates only (by id), most important first. The first is its front-page
lead. Mark each "full" (an article) or "brief" (two or three lines in an In Brief column). Leave out what is not news.

Judge importance like a real editor, not by the points shown (they are a rough guide):
- A major international tournament in play (a World Cup, a Euro, a Copa America...) is the biggest story in football. A
  general paper (a world or national paper) leads with it when its candidates include it, unless a bigger event happened
  (a title decided, a record transfer of a world star). A national paper prefers its own country's games.
- A paper with its own tournament covers that tournament's games; the general papers carry only its biggest story or two.
- The same event (one game, one transfer, one injury, one appointment) is printed by ONE paper only. Never give two papers
  the same game or the same deal. One exception: a tournament's story may be in the tournament's own paper AND in one
  other paper: its biggest story (the round-up) in a world paper, a game in the national paper of one of the two countries.
- Not news, leave out or make a brief at most: an injury that costs no games, a small transfer between small clubs, a
  routine loan, a statistic with no story, a minor manager change at a small club, a second story on something already
  covered this cycle.
- A big club, a famous player, a large fee, a title, a relegation, a final, a record, a shock result: news.
- Prefer a varied paper: not five transfers in a row, not five numbers pieces.
- A paper prints at most its "room" stories; fewer is fine when the candidates are weak. At most "full_at_most" are full.
- Stories marked "must" stay in that paper (put them where they belong in the order).

Then name the "top" stories: the {top} biggest stories of the whole cycle across all papers (by id, each one you print in
full), the ones worth the best writer.

Answer only with JSON: {{"papers": [{{"paper": "<slug>", "stories": [{{"id": <id>, "size": "full" | "brief"}}]}}],
"top": [<id>, ...]}}"""


def _short_facts(lead: Lead) -> str:
    """A few of the lead's short facts, for the editor's eye (a fee, the games missed, a round)."""
    parts = []
    for key, value in lead.facts.items():
        if isinstance(value, (str, int, float)) and not isinstance(value, bool) and len(str(value)) <= 60:
            parts.append(f"{key.replace('_', ' ')}: {value}")
        if sum(len(p) for p in parts) > FACT_WORDS:
            break
    return "; ".join(parts)


def _event(lead: Lead) -> str | None:
    """What the story is about, so the same event is seen in two papers: its game, else its first person or club."""
    return next((r for r in lead.refs if r.startswith("match:")), None) or next(iter(lead.refs), None)


def brief_of(plan, candidates: list[Lead], must: set[str]) -> dict:
    """One paper as the editor sees it."""
    outlet = plan.outlet
    return {"paper": outlet.slug, "name": outlet.name, "about": (outlet.tagline or "") + (" — " + ", ".join(sorted(outlet.beats))),
            "room": len(plan.items), "full_at_most": sum(1 for _, size in plan.items if size != "brief"),
            "candidates": [{"id": None, "story": lead.headline, "kind": lead.kind.replace("_", " "), "points": round(lead.score),
                            "facts": _short_facts(lead) or None, "event": _event(lead), "must": lead.key in must or None}
                           for lead in candidates]}


def review(plans: list, candidates: dict[int, list[Lead]], must: dict[int, set[str]], ask: Callable[[list[dict]], dict] | None,
           progress: Callable[[str], None] = log.info) -> tuple[list, set[str]]:
    """The editor's plans: for each plan, the stories it keeps, in its order, each (lead, "full" | "brief"); and the keys of
    the cycle's top stories. ``candidates``: each outlet's candidates (by outlet id), best first by points; ``must``: the
    keys each outlet must keep; ``ask``: sends the messages to the model and returns its JSON (None: no editor). Any
    failure gives back the points system's plans unchanged and no top stories."""
    if ask is None or not plans:
        return [[(lead, "brief" if size == "brief" else "full") for lead, size in plan.items] for plan in plans], set()
    by_id: dict[int, tuple[int, Lead]] = {}
    papers = []
    for plan in plans:
        pool = candidates.get(plan.outlet.id) or [lead for lead, _ in plan.items]
        brief = brief_of(plan, pool, must.get(plan.outlet.id, set()))
        for row, lead in zip(brief["candidates"], pool):
            row["id"] = len(by_id) + 1
            by_id[row["id"]] = (plan.outlet.id, lead)
        brief["candidates"] = [{k: v for k, v in row.items() if v is not None} for row in brief["candidates"]]
        papers.append(brief)
    messages = [{"role": "system", "content": RULES.format(top=top_count() or 1)},
                {"role": "user", "content": json.dumps({"papers": papers}, ensure_ascii=False)}]
    try:
        answer = ask(messages)
    except Exception as exc:  # noqa: BLE001 - the papers come out without an editor rather than not at all
        progress(f"The editor could not be asked ({exc}); the points decide.")
        return review(plans, candidates, must, None)
    chosen = _read(answer, plans, by_id, must)
    if chosen is None:
        progress("The editor's answer named no stories; the points decide.")
        return review(plans, candidates, must, None)
    printed = {lead.key for items in chosen for lead, size in items if size == "full"}
    top, wanted = [], top_count()
    for value in answer.get("top") or []:
        found = by_id.get(value) if isinstance(value, int) else None
        if found and found[1].key in printed and found[1].key not in top:
            top.append(found[1].key)
    return chosen, set(top[:wanted])


def _read(answer: dict, plans: list, by_id: dict[int, tuple[int, Lead]], must: dict[int, set[str]]) -> list | None:
    """The editor's answer as each plan's (lead, size) list, checked: a story of another paper or one already given to a
    paper is left out, a story the paper must carry is put back, the room is kept. None: an answer with no story at all."""
    wanted = {str(row.get("paper")): row.get("stories") or [] for row in (answer.get("papers") or []) if isinstance(row, dict)}
    if not any(wanted.values()):
        return None
    from app.services.tournament_leads import is_tournament_beat

    taken_events: dict[str, set[int]] = {}  # each game's papers: its tournament's paper and at most one other
    key_holders: dict[str, set[int]] = {}  # each story's paper
    tournament_papers = {p.outlet.id for p in plans if any(is_tournament_beat(b) for b in p.outlet.beats)}
    result: list = [None] * len(plans)
    # a tournament's own paper has first claim on its games, then the home paper on its club's
    order = sorted(range(len(plans)), key=lambda i: (not any(is_tournament_beat(b) for b in plans[i].outlet.beats),
                                                       "home" not in plans[i].outlet.beats, i))
    for index in order:
        plan = plans[index]
        outlet_id, room = plan.outlet.id, len(plan.items)
        full_at_most = sum(1 for _, size in plan.items if size != "brief") or 1
        items: list[tuple[Lead, str]] = []
        for row in wanted.get(plan.outlet.slug, []):
            if not isinstance(row, dict) or not isinstance(row.get("id"), int):
                continue
            found = by_id.get(row["id"])
            if not found or found[0] != outlet_id or any(found[1].key == lead.key for lead, _ in items):
                continue
            lead = found[1]
            event = _event(lead)
            game = bool(event and event.startswith("match:"))
            holders = (key_holders.get(lead.key, set()) | (taken_events.get(event, set()) if game else set())) - {outlet_id}
            # a tournament's story (a game, the round-up) may be in its own paper and in one other paper too
            shared = (game or any(is_tournament_beat(b) for b in lead.beats)) and holders <= tournament_papers and outlet_id not in tournament_papers
            if holders and not shared and lead.key not in must.get(outlet_id, set()):
                continue  # this story, or this game, is already another paper's
            items.append((lead, "brief" if row.get("size") == "brief" else "full"))
        pool = [lead for oid, lead in by_id.values() if oid == outlet_id]
        for lead in pool:  # what the paper must carry goes back in, after the editor's choice
            if lead.key in must.get(outlet_id, set()) and all(lead.key != chosen.key for chosen, _ in items):
                items.append((lead, "full"))
        items = _fit(items, room, full_at_most, must.get(outlet_id, set()))
        for lead, _ in items:
            key_holders.setdefault(lead.key, set()).add(outlet_id)
            if (event := _event(lead)) and event.startswith("match:"):
                taken_events.setdefault(event, set()).add(outlet_id)
        result[index] = items
    return result


def _fit(items: list[tuple[Lead, str]], room: int, full_at_most: int, must: set[str]) -> list[tuple[Lead, str]]:
    """At most ``room`` stories (the last ones that need not stay go first) and at most ``full_at_most`` full ones (the
    later full ones become briefs; the must-carry ones stay full). The given list is not changed."""
    items = list(items)
    while len(items) > room:
        drop = next((i for i in range(len(items) - 1, -1, -1) if items[i][0].key not in must), None)
        if drop is None:
            break
        items.pop(drop)
    fitted, full = [], 0
    for lead, size in items:
        if size == "full" and full >= full_at_most and lead.key not in must:
            size = "brief"
        full += size == "full"
        fitted.append((lead, size))
    return fitted


def top_count() -> int:
    """How many of the cycle's stories the stronger model writes (Settings; none when no stronger model is chosen)."""
    from app.services import ai_settings

    return ai_settings.get_top_count() if ai_settings.get_top_model() else 0


def editor_model() -> tuple[str, str] | None:
    """The editor's model and key: the one chosen in Settings, else the papers' model when it is a cheap hosted one, else
    GPT-6 Luna, else DeepSeek Flash, the first with a key. None when the editor is off or no model has a key."""
    from app.services import ai_settings

    chosen = ai_settings.get_editor_model()
    if chosen == "off":
        return None
    papers = ai_settings.get_papers_model()
    for model in ([chosen] if chosen else []) + ([papers] if papers in CHEAP_MODELS else []) + list(CHEAP_MODELS):
        key = ai_settings.resolve_ai_api_key(model=model)
        if key:
            return model, key
    return None


def asker() -> Callable[[list[dict]], dict] | None:
    """A function that sends the editor's messages to its model; None when there is no model with a key."""
    chosen = editor_model()
    if chosen is None:
        return None
    model, key = chosen
    from app.services.story_writer import request_json

    return lambda messages: request_json(model, key, messages, temperature=0.2)
