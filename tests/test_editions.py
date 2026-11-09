# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""Editions, the desk and the newsstand (docs/media-plan.md, step 4)."""

from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models.outlet import Outlet
from app.models.world_soccer_article import WorldSoccerArticle
from app.models.world_soccer_issue import WorldSoccerIssue
from app.services import article_writer, editions, outlets
from app.services.story_leads import Lead
from tests.test_story_leads import _prepare
from tests.test_world_pages import AS_OF, CHELSEA, FA_CUP, LIVERPOOL, MYERS, app, small_world  # noqa: F401


def _lead(kind: str, score: float, beats: set[str], key: int = 0) -> Lead:
    return Lead(kind, f"{kind} {key}", date(2063, 5, 1), score, {}, [f"club:{CHELSEA + key}"], beats)


@pytest.fixture()
def fake_writer(monkeypatch):
    written = []

    def write(outlet, lead, published, prominence="standard", writer=None, form=None, earlier=None, related=None):
        written.append((outlet.name, lead.kind, prominence, writer and writer["name"], form and form["opening"], earlier))
        if lead.kind == "refused":
            raise article_writer.StoryError("twice wrong")
        return {"headline": f"{outlet.name}: {lead.headline}", "standfirst": "A standfirst.", "body": "One.\n\nTwo.",
                "byline": writer["name"], "writer_id": writer["id"], "model": "test-model", "notebook": {}}

    monkeypatch.setattr(article_writer, "write_article", write)
    return written


def test_the_plan_gives_each_outlet_its_beat_new_leads_and_their_prominence(app):
    all_outlets = outlets.ensure_outlets()
    world_soccer = next(o for o in all_outlets if o.slug == "world-soccer")
    long_ball = next(o for o in all_outlets if o.slug == "long-ball")
    leads = [_lead("title", 300 - i, {"world"}, i) for i in range(10)] + [_lead("legend", 80 - i, {"history"}, 20 + i) for i in range(3)]
    printed = {world_soccer.id: {leads[0].key}}
    plans = {plan.outlet.slug: plan for plan in editions.plan_editions(leads, all_outlets, printed)}
    ws = plans["world-soccer"].items
    assert [lead.headline for lead, _ in ws][0] == "title 1"  # the printed lead is left out
    assert len(ws) == world_soccer.articles_per_edition == 8
    assert [p for _, p in ws] == ["lead", "standard", "standard", "standard", "brief", "brief", "brief", "brief"]
    assert [p for _, p in plans["long-ball"].items] == ["lead", "feature", "feature"] and long_ball.id
    assert "transfer-wire" not in plans  # no transfer leads


def test_a_cycle_makes_published_editions_with_links_and_skips_refused_articles(app, small_world, fake_writer):
    _prepare(small_world)
    result = editions.make_editions(date(2063, 3, 24), AS_OF, progress=lambda text: None)
    assert result.editions and result.written == len(fake_writer)
    issue = db.session.get(WorldSoccerIssue, result.editions[0])
    assert issue.status == "published" and issue.outlet is not None and issue.headline == issue.articles[0].headline
    article = min(issue.articles, key=lambda a: a.sort_order)
    assert article.prominence == "lead" and article.lead_key and article.lead["refs"]
    assert {link.world_ref for link in article.entity_links} == set(article.lead["refs"])
    assert editions.cycle_window() is None  # nothing new after the cycle
    later = AS_OF + timedelta(days=4)
    assert editions.cycle_window(later) == (AS_OF, later)  # by hand, a few days are enough
    assert editions.cycle_window(later, editions.CYCLE_DAYS) is None  # after an import, two weeks are needed
    assert editions.next_cycle_due() == AS_OF + timedelta(days=editions.CYCLE_DAYS)
    assert editions.cycle_window(AS_OF + timedelta(days=14), editions.CYCLE_DAYS) == (AS_OF, AS_OF + timedelta(days=14))
    again = editions.make_editions(date(2063, 3, 24), AS_OF, progress=lambda text: None)
    assert again.written == 0  # each outlet prints a story once


def test_editions_are_published_at_once_and_corrected_on_the_desk(app, small_world, fake_writer):
    _prepare(small_world)
    result = editions.make_editions(date(2063, 3, 24), AS_OF, progress=lambda text: None)
    issue = max((db.session.get(WorldSoccerIssue, i) for i in result.editions), key=lambda i: len(i.articles))  # a story goes to one paper
    outlet = issue.outlet
    client = app.test_client()
    stand = client.get("/").data.decode()
    assert issue.status == "published" and outlet.name in stand and issue.headline in stand  # on the newsstand at once
    desk = client.get("/desk").data.decode()
    assert "Make editions now" in desk and "Unpublish to correct" in desk
    first, second = sorted(issue.articles, key=lambda a: a.sort_order)[:2]
    assert client.post(f"/desk/articles/{first.id}/drop").status_code == 409  # a published edition is fixed
    client.post(f"/desk/editions/{issue.id}/unpublish")
    assert db.session.get(WorldSoccerIssue, issue.id).status == "draft"
    client.post(f"/desk/articles/{second.id}/move", data={"direction": "up"})
    db.session.refresh(issue)
    assert issue.headline == second.headline  # the new front-page lead
    client.post(f"/desk/articles/{first.id}", data={"headline": "A better headline", "standfirst": "", "body": "New body."})
    assert db.session.get(WorldSoccerArticle, first.id).headline == "A better headline"
    published = client.post(f"/desk/editions/{issue.id}/publish")
    assert published.status_code == 302 and db.session.get(WorldSoccerIssue, issue.id).status == "published"
    assert "A better headline" in client.get(f"/news/{outlet.slug}").data.decode()
    linked = next(link.world_ref for link in db.session.get(WorldSoccerArticle, first.id).entity_links if link.world_ref.startswith("club:"))
    page = client.get("/clubs/" + linked.split(":")[1]).data.decode()
    assert "In the news" in page and "A better headline" in page


def test_writing_a_transfer_story_again_uses_corrected_position_facts(app, small_world, fake_writer, monkeypatch):
    """Write again kept the facts stored with the first version, so a position fix never reached the article."""
    _prepare(small_world)
    result = editions.make_editions(date(2063, 3, 24), AS_OF, progress=lambda text: None)
    issue = db.session.get(WorldSoccerIssue, result.editions[0])
    article = issue.articles[0]
    issue.status = "draft"
    stale = {"at_the_bidding_club": {"club": "Chelsea", "who_plays_there": ["Júnior: 20 starts there"]}}
    article.lead = article_writer.lead_to_dict(Lead("transfer_bid", "Chelsea bid", date(2063, 3, 20), 50.0, {"player": "Myers", "if_he_moves": stale},
                                                    [f"person:{MYERS}", f"club:{CHELSEA}", f"club:{LIVERPOOL}"], {"transfers"}, slug="bid"))
    db.session.commit()
    seen = []
    write = article_writer.write_article
    monkeypatch.setattr(article_writer, "write_article", lambda outlet, lead, *a, **k: (seen.append(lead), write(outlet, lead, *a, **k))[1])
    monkeypatch.setattr("app.services.story_leads_now.q.main_positions", lambda c, uids: {MYERS: "AM (L)"})
    editions.rewrite_article(article)
    assert "Júnior" not in str(seen[0].facts["if_he_moves"]) and seen[0].facts["player"] == "Myers"
    assert "Júnior" not in str(db.session.get(WorldSoccerArticle, article.id).lead["facts"])


def test_old_world_soccer_issues_belong_to_world_soccer(app):
    outlets.ensure_outlets()
    old = WorldSoccerIssue(publication_date=date(2063, 1, 1), knowledge_cutoff=date(2063, 1, 1), status="published")
    db.session.add(old)
    db.session.commit()
    assert editions.outlet_of(old).slug == "world-soccer"
    assert Outlet.query.filter_by(slug="world-soccer").one() and MYERS and FA_CUP and LIVERPOOL


def test_every_outlet_has_a_staff_and_an_edition_deals_writers_and_forms(app):
    all_outlets = outlets.ensure_outlets()
    assert all(o.staff for o in all_outlets) and all(w.persona and w.style for o in all_outlets for w in o.staff)
    world_soccer = next(o for o in all_outlets if o.slug == "world-soccer")
    leads = [_lead("title", 300 - i, {"world"}, i) for i in range(6)]
    plan = editions.plan_editions(leads, [world_soccer], {})[0]
    assert len({w["name"] for w in plan.writers}) == len(world_soccer.staff)  # everyone writes
    assert len({f["opening"] for f in plan.forms}) == len(plan.items)  # no two open the same way


def test_bylines_link_to_the_writer_and_writers_can_move(app, small_world, fake_writer):
    _prepare(small_world)
    result = editions.make_editions(date(2063, 3, 24), AS_OF, progress=lambda text: None)
    issue = db.session.get(WorldSoccerIssue, result.editions[0])
    article = issue.articles[0]
    assert article.writer is not None and article.byline == article.writer.name
    client = app.test_client()
    client.post(f"/desk/editions/{issue.id}/publish")
    page = client.get(f"/writers/{article.writer_id}").data.decode()
    assert article.writer.persona[:20] in page and article.headline in page
    other = Outlet.query.filter_by(slug="long-ball").one()
    client.post(f"/settings/writers/{article.writer_id}", data={"name": "New Name", "persona": "P.", "style": "S.",
                                                               "outlet_id": other.id, "active": "on"})
    db.session.refresh(article)
    assert (article.writer.name, article.writer.outlet.slug) == ("New Name", "long-ball")
    client.post(f"/settings/outlets/{other.id}/writers", data={"name": "Fresh Face", "persona": "Young.", "style": "Bold."})
    assert "Fresh Face" in [w.name for w in db.session.get(Outlet, other.id).writers]


def test_articles_about_one_subject_join_a_thread_and_the_next_writer_sees_them(app, small_world, fake_writer):
    _prepare(small_world)
    first = editions.make_editions(date(2063, 3, 24), AS_OF, progress=lambda text: None)
    articles = [a for issue_id in first.editions for a in db.session.get(WorldSoccerIssue, issue_id).articles]
    threaded = [a for a in articles if a.story_thread is not None]
    assert threaded and all(a.story_thread.key == editions.thread_key(article_writer.lead_from_dict(a.lead)) for a in threaded)
    for issue_id in first.editions:  # make the first cycle an older one, and its stories not yet printed
        db.session.get(WorldSoccerIssue, issue_id).knowledge_cutoff = date(2063, 3, 25)
    for article in articles:
        article.lead_key = None
    db.session.commit()
    fake_writer.clear()
    editions.make_editions(date(2063, 3, 24), AS_OF, progress=lambda text: None)
    seen = [earlier for *_, earlier in fake_writer if earlier]
    assert seen and {"date", "outlet", "headline"} <= set(seen[0][0])
    story = next(a.story_thread for a in threaded)
    for issue_id in first.editions:
        db.session.get(WorldSoccerIssue, issue_id).status = "published"
    db.session.commit()
    page = app.test_client().get(f"/threads/{story.id}").data.decode()
    assert "The story so far" in page and story.title in page


def test_related_stories_share_a_player_or_a_club():
    title = Lead("title", "Chelsea win the league", date(2063, 5, 1), 100, {}, ["edition:1:2063", f"club:{CHELSEA}"], {"world"})
    move = Lead("transfer", "Myers leaves Chelsea", date(2063, 6, 1), 90, {}, [f"person:{MYERS}", f"club:{LIVERPOOL}", f"club:{CHELSEA}"],
                {"transfers"})
    other = Lead("title", "Liverpool win the cup", date(2063, 5, 26), 80, {}, ["edition:2:2063", f"club:{LIVERPOOL}"], {"world"})
    assert [r["story"] for r in editions.related_stories(title, [title, move, other])] == ["Myers leaves Chelsea"]
    assert [r["story"] for r in editions.related_stories(move, [title, move, other])] == ["Chelsea win the league", "Liverpool win the cup"]


def test_followed_clubs_rank_higher_only_on_the_home_beat_and_kinds_have_limits(app):
    all_outlets = outlets.ensure_outlets()
    world_soccer = next(o for o in all_outlets if o.slug == "world-soccer")
    english = next(o for o in all_outlets if o.slug == "english-daily")
    chelsea = _lead("title", 100, {"world", "nation:765", "home"}, 1)
    other = _lead("title", 120, {"world", "nation:765"}, 2)
    plans = {p.outlet.slug: [lead for lead, _ in p.items] for o in (world_soccer, english) for p in editions.plan_editions([other, chelsea], [o], {})}
    assert plans["world-soccer"] == [other, chelsea] and plans["english-daily"] == [chelsea, other]
    both = editions.plan_editions([other, chelsea], [world_soccer, english], {})
    assert [p.outlet.slug for p in both] == ["english-daily"]  # a story goes to one paper only: the club's own chooses first
    next_gen = next(o for o in all_outlets if o.slug == "next-gen")
    young = [_lead("scouting", 200 - i, {"young"}, i) for i in range(6)] + [_lead("rising_star", 50, {"young"}, 9)]
    kinds = [lead.kind for lead, _ in editions.plan_editions(young, [next_gen], {})[0].items]
    assert kinds.count("scouting") == editions.KIND_LIMITS["scouting"] and "rising_star" in kinds
    wire = next(o for o in all_outlets if o.slug == "transfer-wire")
    moves = [_lead("transfer", 100 - i, {"transfers"}, i) for i in range(10)]
    assert len(editions.plan_editions(moves, [wire], {})[0].items) == wire.articles_per_edition  # one kind: no limit


def test_earlier_coverage_finds_a_player_who_was_a_side_character(app):
    from app.models.world_soccer_entity_link import WorldSoccerEntityLink

    old = WorldSoccerIssue(publication_date=date(2063, 3, 1), knowledge_cutoff=date(2063, 3, 1), status="published", edition_name="World Soccer")
    article = WorldSoccerArticle(issue=old, headline="A final against his old club", body="…", byline="Eleanor Marsh")
    article.entity_links.append(WorldSoccerEntityLink(entity_type="person", entity_id=MYERS, world_ref=f"person:{MYERS}", label="Ken Myers"))
    article.entity_links.append(WorldSoccerEntityLink(entity_type="club", entity_id=LIVERPOOL, world_ref=f"club:{LIVERPOOL}", label="Liverpool"))
    db.session.add(old)
    db.session.commit()
    move = Lead("transfer", "Myers moves", date(2063, 6, 1), 90, {}, [f"person:{MYERS}", f"club:{CHELSEA}"], {"transfers"})
    found = editions.earlier_coverage(move, date(2063, 7, 1))
    assert [(f["headline"], f["about"]) for f in found] == [("A final against his old club", ["Ken Myers"])]
    assert editions.earlier_coverage(move, date(2063, 2, 1)) == []  # nothing from after the cutoff
    assert found[0]["what_it_was"] is None
    preview = WorldSoccerArticle(issue=old, headline="Chelsea's late show leaves Rovers dreaming", body="…", byline="E. M.",
                                 lead_key=f"club_preview:match:2063-03-05:{CHELSEA}:596:2063")
    preview.entity_links.append(WorldSoccerEntityLink(entity_type="person", entity_id=MYERS, world_ref=f"person:{MYERS}", label="Ken Myers"))
    db.session.add(preview)
    db.session.commit()
    kinds = {f["headline"]: f["what_it_was"] for f in editions.earlier_coverage(move, date(2063, 3, 2))}
    assert kinds["Chelsea's late show leaves Rovers dreaming"] == "a preview of a game on 5 March 2063: not yet played, so nothing in it has happened"
    kinds = {f["headline"]: f["what_it_was"] for f in editions.earlier_coverage(move, date(2063, 7, 1))}
    assert kinds["Chelsea's late show leaves Rovers dreaming"] == "a preview of a game on 5 March 2063"


def test_the_desk_lists_saves_and_refuses_files_outside_the_games_folder(app, tmp_path, monkeypatch):
    from app.services import save_jobs

    (tmp_path / "Old.fm").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("not a save")
    monkeypatch.setattr(save_jobs, "GAMES_DIR", tmp_path)
    saves = save_jobs.list_saves()
    assert [s.name for s in saves] == ["Old.fm"] and saves[0].new
    client = app.test_client()
    assert "Import a save" in client.get("/desk").data.decode() and "Old.fm" in client.get("/desk").data.decode()
    response = client.post("/desk/import", data={"save": "../outside.fm"}, follow_redirects=True)
    assert "not in the game" in response.data.decode()
    with pytest.raises(ValueError):
        save_jobs.start_import(app, "notes.txt")


def test_pictures_are_drawn_only_from_the_desk(app, small_world, fake_writer, monkeypatch):
    from app.services import article_pictures, save_jobs

    drawn = []
    monkeypatch.setattr(article_pictures, "illustrate_front_pages", lambda articles, progress: drawn.extend(articles) or len(articles))
    _prepare(small_world)
    result = editions.make_editions(date(2063, 3, 24), AS_OF, progress=lambda text: None)
    assert result.illustrated == 0 and not drawn  # a cycle draws nothing by itself
    started = []
    monkeypatch.setattr(save_jobs, "start_pictures", lambda app, issue_ids, again=False: started.append((issue_ids, again)))
    client = app.test_client()
    assert "Draw front-page pictures" in client.get("/desk").data.decode()
    client.post("/desk/pictures")
    client.post("/desk/pictures", data={"issue": result.editions[0]})
    assert started == [(result.editions, False), ([result.editions[0]], True)]
    assert article_pictures.illustrate_editions(result.editions, progress=lambda text: None) == len(result.editions)
    issue = db.session.get(WorldSoccerIssue, result.editions[0])
    assert drawn[0] == article_pictures.lead_article(issue)  # each edition's lead story


def test_while_the_leagues_play_a_news_paper_leads_with_news_and_prints_one_look_back_at_most(app):
    all_outlets = outlets.ensure_outlets()
    world_soccer = next(o for o in all_outlets if o.slug == "world-soccer")
    long_ball = next(o for o in all_outlets if o.slug == "long-ball")
    old = [_lead(kind, 200 - i, {"world", "history"}, i) for i, kind in enumerate(("legend_profile", "where_now", "dynasty", "great_final"))]
    news = [_lead("derby", 90, {"world"}, 10), _lead("injury_blow", 80, {"world"}, 11)]
    kinds = [lead.kind for lead, _ in editions.plan_editions(old + news, [world_soccer], {})[0].items]
    assert len(kinds) == 6  # no league is playing: the old stories fill the paper
    season = [_lead("league_round", 70, {"world"}, 12)]
    plans = {p.outlet.slug: [lead.kind for lead, _ in p.items] for p in editions.plan_editions(old + news + season, [world_soccer, long_ball], {})}
    assert plans["world-soccer"] == ["derby", "legend_profile", "injury_blow", "league_round"]  # news on the front
    assert plans["long-ball"] == ["where_now", "dynasty", "great_final"]  # the history paper looks back, at what is left


def test_in_season_a_news_paper_carries_previews_and_its_european_story(app):
    all_outlets = outlets.ensure_outlets()
    english = next(o for o in all_outlets if o.slug == "english-daily")
    nation = next(beat for beat in english.beats if beat.startswith("nation:"))
    strong = [_lead(kind, 300 - i, {nation}, i) for i, kind in enumerate(
        ("derby", "run", "injury_blow", "performance", "big_win", "giant_killing", "milestone", "transfer", "scouting", "award"))]
    weak = [_lead("league_round", 40, {nation}, 20), _lead("preview", 36, {nation}, 21), _lead("preview", 35, {nation}, 22),
            _lead("preview", 34, {nation}, 23), _lead("european_nights", 33, {nation}, 24)]  # above the paper's lowest score
    kinds = [lead.kind for lead, _ in editions.plan_editions(strong + weak, [english], {})[0].items]
    assert len(kinds) == english.articles_per_edition and kinds[0] == "derby"
    assert kinds.count("preview") == 1 and kinds.count("european_nights") == 1  # in the places of the weakest stories (one preview, 27 Oct 2026)
    plain = [lead.kind for lead, _ in editions.plan_editions(strong + weak[1:], [english], {})[0].items]
    assert "preview" not in plain  # no league is playing: no minimum


def test_the_gallery_lists_and_deletes_pictures(app, small_world, tmp_path, monkeypatch):
    import config
    from app.models.archive_image import ArchiveImageLink
    from app.services import image_library

    monkeypatch.setattr(config, "GENERATED_DIR", tmp_path)
    image = image_library.register_archive_image("/static/generated/world/photos/competition-11-a.jpg", kind="photograph", source="generated",
                                                 caption="A wall of fans")
    image.links.append(ArchiveImageLink(entity_type="competition", entity_id=11, relation="subject", world_ref="competition:11"))
    issue = WorldSoccerIssue(publication_date=date(2063, 9, 3), knowledge_cutoff=date(2063, 9, 3), status="published", edition_name="x")
    story = WorldSoccerArticle(headline="Drawn story", section="Form", prominence="lead", body="x", byline="Desk", article_type="report",
                               sort_order=0, image_url="/static/generated/illustrations/article-9-abc.jpg")
    issue.articles.append(story)
    db.session.add(issue)
    db.session.commit()
    client = app.test_client()
    page = client.get("/desk/gallery").data.decode()
    assert "English Premier Division" in page and "A wall of fans" in page and "Drawn story" in page and "competition-11-a.jpg" in page
    assert client.post("/desk/gallery/delete", data={"image": [str(image.id)], "article": [str(story.id)]}).status_code == 302
    assert "competition-11-a.jpg" not in client.get("/desk/gallery").data.decode()
    assert db.session.get(WorldSoccerArticle, story.id).image_url is None


def test_a_short_cycle_makes_a_thin_edition(app):
    all_outlets = outlets.ensure_outlets()
    outlets_now = lambda: all_outlets  # noqa: E731
    world_soccer = next(o for o in all_outlets if o.slug == "world-soccer")
    assert world_soccer.articles_per_edition == 8
    assert [editions.edition_size(world_soccer, d) for d in (None, 14, 20, 7, 2, 1, 0)] == [8, 8, 8, 4, 2, 2, 2]
    leads = [_lead("title", 300 - i, {"world"}, i) for i in range(10)]
    thin = {plan.outlet.slug: plan for plan in editions.plan_editions(leads, outlets_now(), {}, days=2)}
    assert [p for _, p in thin["world-soccer"].items] == ["lead", "standard"]
    full = {plan.outlet.slug: plan for plan in editions.plan_editions(leads, outlets_now(), {}, days=14)}
    assert len(full["world-soccer"].items) == 8


def test_a_column_is_its_own_page_beyond_a_short_cycles_size(app):
    """World Soccer had three pages for four days in season and the minimum news took them all: no Away Days column (1 Dec 2063)."""
    all_outlets = outlets.ensure_outlets()
    news = [_lead("promised_report", 90 - i, {"world"}, i) for i in range(4)] + [_lead("preview", 80, {"world"}, 10), _lead("league_round", 70, {"world"}, 11)]
    trip = _lead("travel_log", 65, {"world"}, 12)
    plans = {plan.outlet.slug: [lead.kind for lead, _ in plan.items] for plan in editions.plan_editions(news + [trip], all_outlets, {}, days=4)}
    assert plans["world-soccer"].count("travel_log") == 1 and len(plans["world-soccer"]) == editions.edition_size(all_outlets[0], 4, in_season=True) + 1
    plans = {plan.outlet.slug: [lead.kind for lead, _ in plan.items] for plan in editions.plan_editions(news + [trip, _lead("travel_log", 66, {"world"}, 13)], all_outlets, {}, days=4)}
    assert plans["world-soccer"].count("travel_log") == 1  # one trip a cycle, the best
    for plan in editions.plan_editions(news + [trip], all_outlets, {}, days=4):  # a column is the long column in every paper
        assert all(size == "column" for lead, size in plan.items if lead.kind == "travel_log")


def test_a_printed_preview_does_not_block_the_report_of_its_game(app):
    """The English Daily previewed Middlesbrough v Chelsea on 27 Nov 2063 and then printed no report of the 0-1: the guard
    against two stories of one game took the preview's match ref as the game already told."""
    all_outlets = outlets.ensure_outlets()
    daily = next(o for o in all_outlets if "home" in o.beats)
    match = "match:2063-12-04:685:630"
    preview_key = f"club_preview:{match}:2063"
    report = Lead("club_report", "Middlesbrough 0-1 Chelsea", date(2063, 12, 4), 90, {}, [match, "club:685", "club:630"], {"home"})
    again = Lead("club_preview", "Next up: Middlesbrough v Chelsea", date(2063, 12, 4), 80, {}, [match, "club:685", "club:630"], {"home"})
    other = Lead("promised_report", "As previewed: Middlesbrough 0-1 Chelsea", date(2063, 12, 4), 85, {}, [match, "club:685", "club:630"], {"home"})
    plans = {plan.outlet.slug: [lead.headline for lead, _ in plan.items]
             for plan in editions.plan_editions([report, again, other], all_outlets, {daily.id: {preview_key}})}
    assert plans[daily.slug] == ["Middlesbrough 0-1 Chelsea"]  # the report is printed; the preview again is not; one report of the game
    printed = {daily.id: {preview_key, f"club_report:{match}:2063"}}
    plans = {plan.outlet.slug: [lead.headline for lead, _ in plan.items] for plan in editions.plan_editions([report, again, other], all_outlets, printed)}
    assert daily.slug not in plans  # a report printed: no second report of the game


def test_the_home_papers_two_page_edition_carries_the_clubs_game_report(app):
    """A two-day cycle gives the home paper two pages. A transfer story led and the next game's preview took the second page,
    and the minimum rule never replaces the lead: Chelsea 3-0 Arsenal (8 Dec 2063) had no report."""
    all_outlets = outlets.ensure_outlets()
    daily = next(o for o in all_outlets if "home" in o.beats)
    leads = [_lead("league_round", 70, {"world"}, 1), _lead("transfer_interest", 95, {"home", "transfers"}, 2),
             _lead("club_preview", 80, {"home"}, 3), _lead("club_report", 67, {"home"}, 4)]
    plans = {plan.outlet.slug: [lead.kind for lead, _ in plan.items] for plan in editions.plan_editions(leads, all_outlets, {}, days=2)}
    assert plans[daily.slug][:2] == ["club_report", "club_preview"]  # the report leads, the preview follows; four pages in season



def test_a_news_paper_never_leads_with_the_market_and_a_kings_wage_waits_a_month(app):
    """World Soccer led with a Saudi club's list three cycles in four and 'Cash is king' ran every cycle (8 to 19 Dec 2063)."""
    all_outlets = outlets.ensure_outlets()
    wire = next(o for o in all_outlets if "transfers" in o.beats)
    leads = [_lead("league_round", 70, {"world"}, 1), _lead("derby", 60, {"world"}, 2), _lead("transfer_interest", 90, {"world", "transfers"}, 3),
             _lead("kings_wage", 80, {"transfers"}, 4)]
    leads[2].facts["also_on_the_lists_of"] = {"Al-Ittihad": "69 players on their list"}
    plans = {plan.outlet.slug: [lead.kind for lead, _ in plan.items] for plan in editions.plan_editions(leads, all_outlets, {}, days=14)}
    assert plans["world-soccer"][0] == "league_round" and "transfer_interest" in plans["world-soccer"]  # damped, never the front
    assert plans[wire.slug][0] in ("kings_wage", "transfer_interest")
    # printed a week ago: the king's wage and the shopping club's story wait, the rest goes on
    last = {"kings_wage": date(2063, 12, 7), "shopping": date(2063, 12, 7)}
    plans = {plan.outlet.slug: [lead.kind for lead, _ in plan.items]
             for plan in editions.plan_editions(leads, all_outlets, {}, days=14, last_printed=last, until=date(2063, 12, 14))}
    assert plans["world-soccer"] == ["league_round", "derby"] and wire.slug not in plans
    assert editions.cooldown_tags(leads[2]) == {"transfer_interest", "shopping"} and editions.cooldown_tags(leads[3]) == {"kings_wage"}
    plans = {plan.outlet.slug: [lead.kind for lead, _ in plan.items]
             for plan in editions.plan_editions(leads, all_outlets, {}, days=14, last_printed=last, until=date(2064, 1, 10))}
    assert "kings_wage" in plans[wire.slug]  # a month on: printed again


def test_a_match_reports_questions_are_answered_as_of_its_game_day():
    """The report of Chelsea 3-1 Shamrock Rovers (30 Jan 2064), written for the edition of 3 Feb, asked the archive for
    recent results and led with the 3-0 at Forest that day; a report's questions now stop at its game."""
    from app.services import article_writer
    from app.services.story_leads import Lead

    published = date(2064, 2, 3)
    report = Lead("club_report", "Chelsea 3-1 Shamrock Rovers", date(2064, 1, 30), 70, {}, ["match:2064-01-30:630:596", "club:630"])
    assert article_writer._as_of(report, published) == date(2064, 1, 30)
    preview = Lead("club_preview", "Chelsea v Aston Villa", published, 60, {}, ["match:2064-02-13:630:603", "club:630"])
    assert article_writer._as_of(preview, published) == published


def test_a_story_is_told_once_in_any_paper_and_a_day_stamped_one_not_again_within_two_weeks(app):
    """3 Nov 2026: the same Champions League play-off preview was written on 13 and 16 Feb 2064 (its key carried the day it
    was found), and one derby preview in two papers."""
    all_outlets = outlets.ensure_outlets()
    world_soccer = next(o for o in all_outlets if o.slug == "world-soccer")
    form = Lead("run", "Chelsea's run", date(2064, 2, 16), 90, {}, [f"club:{CHELSEA}"], {"world"}, slug=f"{CHELSEA}:2064-02-16")
    preview = Lead("preview", "Coming up", date(2064, 2, 16), 80, {}, ["match:2064-02-22:1:2"], {"world"})
    assert editions.story_of(form.key, date(2064, 2, 16)) == f"run:{CHELSEA}"
    assert editions.story_of("league_round:11:2064-02-14", date(2064, 2, 16)) == "league_round:11:2064-02-14"  # the round's own day
    stories = {f"run:{CHELSEA}": date(2064, 2, 13), preview.key: date(2064, 2, 13)}
    until = date(2064, 2, 16)
    assert editions.plan_editions([form, preview], [world_soccer], {}, until=until, stories=stories) == []
    later = {f"run:{CHELSEA}": date(2064, 1, 20)}
    plans = editions.plan_editions([form, preview], [world_soccer], {}, until=until, stories=later)
    assert {lead.key for lead, _ in plans[0].items} == {form.key, preview.key}
    nights = Lead("european_nights", "A night", until, 70, {}, ["nation:765"], {"world"}, slug=f"765:{until}")  # new games each cycle
    assert editions.plan_editions([nights], [world_soccer], {}, until=until, stories={"european_nights:765": date(2064, 2, 13)})
