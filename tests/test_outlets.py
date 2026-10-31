# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""The outlets and the article writer (docs/media-plan.md, step 3)."""

import json
import random
from datetime import date

import pytest

from app.extensions import db
from app.models.outlet import Outlet
from app.services import article_writer, outlets, story_leads
from app.services.story_leads import Lead
from app.services.story_writer import StoryError
from tests.test_world_pages import AS_OF, CHELSEA, FA_CUP, LIVERPOOL, MYERS, app, small_world  # noqa: F401


def _lead() -> Lead:
    return Lead("title", "Liverpool won for the first time: English FA Cup 2062/63", date(2063, 5, 26), 140.0,
                {"competition": "English FA Cup", "edition": "2062/63", "winner": "Liverpool", "runner_up": "Chelsea",
                 "final": {"date": "2063-05-26", "score": "2-1"}},
                [f"edition:{FA_CUP}:2063", f"club:{LIVERPOOL}", f"person:{MYERS}"], {"world", "nation:765", "home"})


@pytest.fixture()
def fake_ai(monkeypatch):
    monkeypatch.setattr(article_writer, "get_papers_model", lambda: "test-model")
    monkeypatch.setattr(article_writer, "resolve_ai_api_key", lambda key, model=None: "test-key")
    answers = []
    monkeypatch.setattr(article_writer, "request_json", lambda model, key, messages, temperature=0.5: answers.pop(0))
    monkeypatch.setattr(article_writer, "request_with_questions",
                        lambda model, key, messages, tools, answer, most, temperature=0.5: (answers.pop(0), []))
    return answers


def test_the_first_outlets_are_made_once_and_take_leads_on_their_beat(app):
    first = outlets.ensure_outlets()
    assert [o.slug for o in first][:2] == ["world-soccer", "transfer-wire"] and len(outlets.ensure_outlets()) == len(first)
    names = [o.name for o in outlets.outlets_for(_lead(), first)]
    assert names == ["World Soccer", "The English Daily"]  # world and England, not transfers or Spain
    world_soccer = first[0]
    world_soccer.min_score = 200
    assert "World Soccer" not in [o.name for o in outlets.outlets_for(_lead(), first)]


def test_the_followed_clubs_setting(app):
    assert story_leads.followed_clubs() == story_leads.DEFAULT_FOLLOWED
    outlets.set_followed_clubs([CHELSEA, 5])
    db.session.commit()
    assert story_leads.followed_clubs() == tuple(sorted({CHELSEA, 5}))


def test_the_notebook_has_the_lead_and_its_people_and_clubs(small_world):
    with small_world.connect() as c:
        notebook = article_writer.article_notebook(c, _lead(), AS_OF)
    assert notebook["story"]["facts"]["final"]["score"] == "2-1"
    assert notebook["clubs"][0]["name"] == "Liverpool"
    person = notebook["people"][0]
    assert (person["name"], person["nationality"], person["career"]["club_goals"]) == ("Ken Myers", "English", 13)
    assert "character" not in person  # no snapshot with traits yet
    from app.models import world
    with small_world.begin() as c:
        save = c.exec_driver_sql("select id from saves limit 1").scalar()
        c.execute(world.WorldPlayerSnapshot.__table__.insert(), [{"person_uid": MYERS, "save_id": save, "height_cm": 180, "weight_kg": 75,
                                                                   "attributes": {}, "traits": {"Temperament": 4, "Important Matches": 19}}])
    with small_world.connect() as c:
        person = article_writer.article_notebook(c, _lead(), AS_OF)["people"][0]
    assert person["character"] == "a short fuse, a big-game player"  # the strongest mark first; the game's hidden traits, in words, for every story about him


def test_the_writer_retries_once_and_refuses_a_second_wrong_draft(small_world, fake_ai):
    outlet = outlets.ensure_outlets()[0]
    good = {"headline": "Liverpool end the wait", "standfirst": "A first FA Cup.", "body": "Liverpool beat Chelsea 2-1 in 2063."}
    fake_ai += [{"headline": "Liverpool", "standfirst": "", "body": "A 5-0 rout in 2099."}, good]
    article = article_writer.write_article(outlet, _lead(), AS_OF, "brief")
    assert (article["headline"], article["model"]) == ("Liverpool end the wait", "test-model")
    assert article["byline"] in [w.name for w in outlet.staff] and article["writer_id"]
    fake_ai += [{"headline": "x", "body": "In 2099."}, {"headline": "x", "body": "In 2098."}]
    with pytest.raises(StoryError, match="second draft"):
        article_writer.write_article(outlet, _lead(), AS_OF)


def test_the_pages(app, small_world, fake_ai):
    client = app.test_client()
    assert b"The Transfer Wire" in client.get("/settings/outlets").data
    outlet = Outlet.query.filter_by(slug="next-gen").one()
    response = client.post("/settings/outlets", data={"outlet_id": outlet.id, "name": "Wonderkids", "voice": "Bright.",
                                                       "beats": "young, nation:765", "byline": "", "articles_per_edition": "5",
                                                       "min_score": "10"})
    assert response.status_code == 302
    db.session.refresh(outlet)
    assert (outlet.name, outlet.beats, outlet.byline, outlet.active) == ("Wonderkids", ["young", "nation:765"], "Wonderkids", False)
    client.post("/settings/followed-clubs", data={"club": "Liverpool"})
    assert LIVERPOOL in story_leads.followed_clubs()
    fake_ai.append({"headline": "Liverpool end the wait", "standfirst": "A first FA Cup.", "body": "They beat Chelsea 2-1."})
    page = client.post("/world-soccer/leads/write", data={"outlet_id": outlets.ensure_outlets()[0].id, "prominence": "brief",
                                                         "published": AS_OF.isoformat(), "lead": json.dumps(article_writer.lead_to_dict(_lead()))})
    assert page.status_code == 200 and b"Liverpool end the wait" in page.data


def test_the_notebook_names_players_with_a_past_at_the_other_side(small_world):
    from app.models import world

    with small_world.begin() as c:  # Ken Myers, Chelsea's scorer, now plays for Liverpool, the FA Cup winners
        c.execute(world.WorldPerson.__table__.update().where(world.WorldPerson.fm_uid == MYERS).values(team_uid=LIVERPOOL))
    with small_world.connect() as c:
        notebook = article_writer.article_notebook(c, _lead(), AS_OF)
    link = notebook["connections"][0]
    assert (link["player"], link["now_at"], link["formerly_at"], link["goals_at_former_club"]) == ("Ken Myers", "Liverpool", "Chelsea", 13)


def test_a_transfers_own_player_is_no_connection(small_world):
    from app.models import world
    from app.services.story_leads import Lead

    with small_world.begin() as c:  # Ken Myers moves from Chelsea to Liverpool: his Chelsea years are no "return"
        c.execute(world.WorldPerson.__table__.update().where(world.WorldPerson.fm_uid == MYERS).values(team_uid=LIVERPOOL))
    move = Lead("transfer", "Ken Myers transfer: Chelsea to Liverpool", AS_OF, 50,
                {"player": "Ken Myers", "from": "Chelsea", "to": "Liverpool"}, [f"person:{MYERS}", f"club:{LIVERPOOL}", f"club:{CHELSEA}"],
                {"transfers"})
    with small_world.connect() as c:
        notebook = article_writer.article_notebook(c, move, AS_OF)
    assert "Ken Myers" not in [link["player"] for link in notebook.get("connections", [])]


def test_illustration_prompts_vary_within_a_kind():
    from types import SimpleNamespace

    from app.services import article_pictures

    article = SimpleNamespace(id=1, lead={"kind": "title", "refs": []})
    prompts = {article_pictures.illustration_prompt(article, random.Random(seed)) for seed in range(40)}
    assert len(prompts) > 20  # the same kind of story no longer gives the same picture
    assert all("soccer" in prompt and "no " not in prompt and "Not " not in prompt for prompt in prompts)  # named, never forbidden


def test_a_clubs_runs_of_titles_are_said_whole_still_going_or_ended(app, small_world):
    from app.models import world as world_models
    from app.services import article_writer

    with small_world.begin() as c:
        c.execute(world_models.WorldEdition.__table__.insert(), [
            {"competition_uid": 11, "year": 2061, "season_label": "2060/61", "decided_on": date(2061, 5, 20)},
            {"competition_uid": 11, "year": 2062, "season_label": "2061/62", "decided_on": date(2062, 5, 20)},
            {"competition_uid": FA_CUP, "year": 2060, "season_label": "2059/60", "decided_on": date(2060, 5, 27)},
            {"competition_uid": FA_CUP, "year": 2061, "season_label": "2060/61", "decided_on": date(2061, 5, 27)}])
        c.execute(world_models.WorldEditionPlace.__table__.insert(), [
            {"competition_uid": 11, "year": 2061, "place": 1, "team_uid": CHELSEA},
            {"competition_uid": 11, "year": 2062, "place": 1, "team_uid": CHELSEA},
            {"competition_uid": FA_CUP, "year": 2060, "place": 1, "team_uid": CHELSEA},
            {"competition_uid": FA_CUP, "year": 2061, "place": 1, "team_uid": CHELSEA},
            {"competition_uid": FA_CUP, "year": 2062, "place": 1, "team_uid": CHELSEA}])
    with small_world.connect() as c:
        runs = article_writer._title_runs(c, CHELSEA, date(2063, 8, 12))
    assert runs == ["English FA Cup: won 3 times, the last in 2061/62; runs: 3 in a row, 2059/60 to 2061/62, ended in 2062/63 (the club finished the runners-up)",
                    "English Premier Division: won 3 times, the last in 2062/63; runs: 3 in a row, 2060/61 to 2062/63, still going (won the latest, 2062/63)"]


def test_the_notebook_carries_the_people_in_his_life_and_his_frame_of_mind(small_world):
    """Personality matters in interviews (26 Oct 2026): whom he likes and dislikes, who likes him, with the game's reasons;
    for a player, his morale and open grievances too."""
    from app.models import world

    with small_world.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 901, "name": "Joe Haigh", "as_of": AS_OF}, {"fm_uid": 902, "name": "Rival Ron", "as_of": AS_OF},
                                                         {"fm_uid": 903, "name": "Boss Bob", "as_of": AS_OF}])
        c.execute(world.WorldPersonFeeling.__table__.insert(), [
            {"person_uid": MYERS, "other_uid": 901, "likes": 1, "reason": 11, "level": 100, "as_of": AS_OF},
            {"person_uid": MYERS, "other_uid": 902, "likes": 0, "reason": 0, "level": 25, "as_of": AS_OF},
            {"person_uid": MYERS, "other_uid": 903, "likes": 1, "reason": 6, "level": 20, "as_of": AS_OF},  # below the favoured level: not listed
            {"person_uid": 903, "other_uid": MYERS, "likes": 1, "reason": 7, "level": 60, "as_of": AS_OF}])
    with small_world.connect() as c:
        person = article_writer.article_notebook(c, _lead(), AS_OF)["people"][0]
    assert person["people_in_his_life"] == {"close_to": ["Joe Haigh (a coach who brought him through)"], "dislikes": ["Rival Ron"],
                                            "liked_by": ["Boss Bob (a player he managed)"]}
    assert "frame_of_mind" not in person  # no morale in the small world
