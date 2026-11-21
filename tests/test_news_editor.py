# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""The editor: a cheap model chooses each paper's stories among its candidates (21 Nov 2026)."""

import json
from datetime import date

from app.services import editions, news_editor, outlets
from app.services.story_leads import Lead
from tests.test_world_pages import CHELSEA, app  # noqa: F401


def _lead(kind: str, score: float, beats: set[str], key: int, refs: list[str] | None = None) -> Lead:
    return Lead(kind, f"{kind} {key}", date(2064, 6, 20), score, {"fee": "£0.8m"} if kind == "transfer" else {},
                refs or [f"club:{CHELSEA + key}"], beats)


def _papers(app_):
    outlets.ensure_outlets()
    outlets.tournament_papers(["European Football Championship 2064"])
    return outlets.ensure_outlets()


def _ids(messages: list[dict]) -> dict[str, dict[str, int]]:
    """Each paper's candidates as the editor saw them: {paper slug: {story: id}}."""
    papers = json.loads(messages[1]["content"])["papers"]
    return {p["paper"]: {c["story"]: c["id"] for c in p["candidates"]} for p in papers}


def test_the_editor_leads_the_world_paper_with_the_tournament_and_drops_the_non_stories(app):
    all_outlets = _papers(app)
    retire = _lead("career_end", 300, {"world"}, 1)  # Utrecht's manager retires: the most points
    small = [_lead("transfer", 200 - i, {"world"}, 10 + i) for i in range(6)]
    euro = _lead("tournament_round_up", 120, {"world", "tournament:European Football Championship 2064"}, 30)
    plans = editions.plan_editions([retire, *small, euro], all_outlets, {})
    points = [lead.kind for lead, _ in next(p for p in plans if p.outlet.slug == "world-soccer").items]
    assert points[0] != "tournament_round_up"  # the points do not lead with the tournament

    seen = {}

    def editor(messages):
        ids = seen.setdefault("ids", _ids(messages))
        world = ids["world-soccer"]
        return {"papers": [{"paper": "world-soccer", "stories": [{"id": world["tournament_round_up 30"], "size": "full"},
                                                                  {"id": world["career_end 1"], "size": "brief"},
                                                                  {"id": 9999, "size": "full"}]}],  # an id that is no story
                "top": [world["tournament_round_up 30"], world["career_end 1"]]}

    edited = {p.outlet.slug: p for p in editions.edited(plans, ask=editor)}
    world = edited["world-soccer"]
    assert [(lead.kind, size) for lead, size in world.items] == [("tournament_round_up", "lead"), ("career_end", "brief")]
    assert world.top == {euro.key}  # a brief is never a top story
    assert "the same game or the same deal" in news_editor.RULES.replace("\n", " ") and "career_end 1" in json.dumps(seen["ids"])


def test_one_game_in_one_paper_and_the_home_paper_keeps_its_own_game(app):
    all_outlets = _papers(app)
    game = "match:2064-06-20:1:2"
    euro_paper_report = _lead("tournament_report", 100, {"world", "tournament:European Football Championship 2064"}, 1, [game])
    home_report = _lead("club_report", 90, {"world", "home"}, 2, [f"club:{CHELSEA}"])
    plans = editions.plan_editions([euro_paper_report, home_report], all_outlets, {})

    def editor(messages):
        ids = _ids(messages)
        stories = []
        for slug, candidates in ids.items():  # every paper asks for every story it can see
            stories.append({"paper": slug, "stories": [{"id": i, "size": "full"} for name, i in candidates.items() if "club_report" not in name]})
        return {"papers": stories, "top": []}

    edited = {p.outlet.slug: p for p in editions.edited(plans, ask=editor)}
    holders = [slug for slug, p in edited.items() if any(lead.key == euro_paper_report.key for lead, _ in p.items)]
    assert "tournament-special" in holders and len(holders) == 2  # the tournament's paper, and one other paper at most
    assert any(lead.key == home_report.key for lead, _ in edited["english-daily"].items)  # the editor left it out; it stays


def test_without_an_editor_or_with_a_broken_answer_the_points_decide(app):
    all_outlets = _papers(app)
    leads = [_lead("title", 100 - i, {"world"}, i) for i in range(5)]
    plans = editions.plan_editions(leads, all_outlets, {})
    before = {p.outlet.slug: [lead.key for lead, _ in p.items] for p in plans}
    for ask in (False, lambda messages: {"nonsense": True}, lambda messages: (_ for _ in ()).throw(RuntimeError("down"))):
        after = {p.outlet.slug: [lead.key for lead, _ in p.items] for p in editions.edited(plans, progress=lambda text: None, ask=ask)}
        assert after == before


def test_the_room_and_the_full_articles_are_kept():
    picked = [(_lead("title", 1, {"world"}, i), "full") for i in range(6)]
    fitted = news_editor._fit(picked, room=4, full_at_most=2, must={picked[5][0].key})
    assert [lead.key for lead, _ in fitted] == [picked[i][0].key for i in (0, 1, 2, 5)]  # the must-carry stays
    assert [size for _, size in fitted] == ["full", "full", "brief", "full"]


def test_a_transfer_or_a_scouting_report_is_in_one_paper_only(app):
    """21 Nov 2026: the Hilgers transfer was in three papers, a scouting report in two."""
    all_outlets = _papers(app)
    deal = _lead("transfer", 150, {"world", "home", "transfers"}, 1, ["person:7", f"club:{CHELSEA}"])
    plans = editions.plan_editions([deal, *[_lead("title", 50 - i, {"world"}, 10 + i) for i in range(3)]], all_outlets, {})

    def editor(messages):
        ids = _ids(messages)
        return {"papers": [{"paper": slug, "stories": [{"id": i, "size": "full"} for i in candidates.values()]} for slug, candidates in ids.items()],
                "top": []}

    holders = [p.outlet.slug for p in editions.edited(plans, ask=editor) if any(lead.key == deal.key for lead, _ in p.items)]
    assert len(holders) == 1


def test_the_newsroom_settings_are_kept_and_used(app):
    from app.extensions import db
    from app.services import ai_settings

    page = app.test_client()
    text = page.get("/settings/").get_data(as_text=True)
    assert 'name="editor_model"' in text and 'name="top_model"' in text and 'name="top_count"' in text and 'name="text_checks"' in text
    assert (ai_settings.get_editor_model(), ai_settings.get_top_model(), ai_settings.get_top_count(), ai_settings.text_checks_on()) == \
        ("", "deepseek-v4-pro", 3, True)  # the first defaults
    done = page.post("/settings/newsroom", data={"editor_model": "off", "top_model": "", "top_count": "5"}, follow_redirects=True)
    assert "Editor: off. Top stories: none. Checks after the writing: off." in done.get_data(as_text=True)
    assert news_editor.editor_model() is None and news_editor.top_count() == 0  # no editor, and no top stories without a model
    page.post("/settings/newsroom", data={"editor_model": "deepseek-flash", "top_model": "deepseek-v4-pro", "top_count": "2", "text_checks": "1"})
    assert (ai_settings.get_editor_model(), news_editor.top_count(), ai_settings.text_checks_on()) == ("deepseek-flash", 2, True)
    bad = page.post("/settings/newsroom", data={"editor_model": "gpt-9", "top_count": "2"}, follow_redirects=True).get_data(as_text=True)
    assert "Choose GPT-6 Luna, DeepSeek V4.1 Flash, automatic or off" in bad and ai_settings.get_editor_model() == "deepseek-flash"
    db.session.rollback()


def test_the_checks_after_the_writing():
    from app.services.article_writer import polished

    body = ("The club receive £61.1m; the player receives a salary that makes the old wage ceiling look modest. Both sides call it a "
            "clean break.\n\nLeeds slam the door. The club receive £61.1m; the player receives a salary that makes the old wage ceiling look modest.")
    article = polished({"headline": "Leeds Slam Madrid’s Rufo Bid", "body": body + " Madrid made a bid for Rufo.", "standfirst": ""})
    assert article["body"].count("old wage ceiling") == 1 and article["headline"] == "Leeds slam Madrid’s Rufo bid"
    assert polished({"headline": "Belgium strike twice in a minute", "body": "x", "standfirst": ""})["headline"] == "Belgium strike twice in a minute"
