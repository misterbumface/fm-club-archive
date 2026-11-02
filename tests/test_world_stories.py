# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""Step 5: stories on the world database (docs/stories-plan.md)."""


from datetime import date

import pytest

from app.extensions import db
from app.models.match_text import GENERATED, MatchText
from app.models import world
from app.models.world_story import ClubSeasonStory, EditionStory
from app.services import player_notebook, story_notebooks, story_writer
from app.services import world_queries as q
from tests.test_world_pages import ARSENAL, AS_OF, CHELSEA, FA_CUP, LEAGUE, LIVERPOOL, MYERS, app, pages, small_world  # noqa: F401


def _final_id(engine) -> int:
    with engine.connect() as c:
        return c.exec_driver_sql("select id from fixtures where stage = 3034").scalar()


@pytest.fixture()
def fake_ai(monkeypatch):
    monkeypatch.setattr(story_writer, "get_deepseek_model", lambda: "test-model")
    monkeypatch.setattr(story_writer, "resolve_ai_api_key", lambda key, model=None: "test-key")
    drafts = []
    monkeypatch.setattr(story_writer, "_request", lambda model, key, messages: drafts.pop(0))
    return drafts


# -- notebooks ----------------------------------------------------------------------------------------
def test_the_season_notebook_has_the_table_finals_and_squad(small_world):
    with small_world.connect() as c:
        notebook = story_notebooks.club_season_notebook(c, CHELSEA, 2062)
    assert (notebook["club"], notebook["season"], notebook["status"]) == ("Chelsea", "2062/63", "finished")
    assert (notebook["league"]["position"], notebook["league"]["points"]) == (1, 6)
    final = notebook["finals"][0]
    assert (final["competition"], final["opponent"], final["score"], final["result"], final["scorers"]) == (
        "English FA Cup", "Liverpool", "1-2", "lost", ["Ken Myers 19'"])
    assert [p["name"] for p in notebook["squad"]] == ["Ken Myers"]
    assert all(r["competition"] != "English Premier Division" for r in notebook["finals"])  # a league has no final


def test_the_season_notebook_carries_the_club_background_but_not_an_ai_story(small_world, fake_ai):
    from app.models.world_story import ClubText

    db.session.add(ClubText(club_uid=CHELSEA, profile_summary="A club with more than a billion euros in the bank."))
    db.session.commit()
    with small_world.connect() as c:
        notebook = story_notebooks.club_season_notebook(c, CHELSEA, 2062)
    assert "billion euros" in notebook["club_background"]["text"]
    copy = "An old story. " * 30
    db.session.add(ClubSeasonStory(club_uid=CHELSEA, season=2062, text=copy, source="generated"))
    db.session.commit()
    with small_world.connect() as c:
        notebook = story_notebooks.club_season_notebook(c, CHELSEA, 2062)
    assert notebook["old_story"] is None  # an AI story is not given back to be copied
    notebook["old_story"] = {"text": copy}
    assert story_writer.copied_text("Intro. " + copy[:200] + " and more.", notebook)
    assert not story_writer.copied_text("A season told in new words.", notebook)


def test_the_edition_and_match_notebooks(small_world):
    with small_world.connect() as c:
        edition = story_notebooks.edition_notebook(c, FA_CUP, 2063)
        match = story_notebooks.match_notebook(c, _final_id(small_world))
    assert (edition["winner"], edition["runner_up"], edition["final"]["score"]) == ("Liverpool", "Chelsea", "2-1")
    assert [r["round"] for r in edition["rounds"]] == ["Round 1", "Semi-finals", "Final"]
    assert (match["round"], match["is_final"], match["score"], match["score_after_90_minutes"]) == ("Final", True, "2-1", "1-1")
    assert match["goals"][0] == {"minute": "19'", "team": "Chelsea", "scorer": "Ken Myers", "kind": "goal", "age": 23,
                                 "nationality": "English", "position": None}
    assert match["away_league_before"]["position"] == 1
    assert "at" not in match["goals"][0] and match["chances_that_were_not_goals"] is None  # no shot list for the final


def test_the_match_notebook_tells_each_goal_to_the_second_and_the_chances_that_were_not_goals(small_world):
    """The save keeps every shot's second and where it crossed the line; the notebook gave only the goals' minute, so a
    writer who wanted moments invented them (31 Oct 2026)."""
    final = _final_id(small_world)
    with small_world.begin() as c:
        c.execute(world.WorldMatchShot.__table__.insert(), [
            {"fixture_id": final, "side": 1, "seq": 0, "person_uid": MYERS, "minute": 19, "second": 7, "outcome": "goal", "across": 2.9, "height": 0.4, "outside_box": None, "as_of": AS_OF},
            {"fixture_id": final, "side": 1, "seq": 1, "person_uid": MYERS, "minute": 12, "second": 27, "outcome": "saved", "across": 3.1, "height": 0.3, "outside_box": None, "as_of": AS_OF},
            {"fixture_id": final, "side": 1, "seq": 2, "person_uid": MYERS, "minute": 45, "second": 29, "outcome": "woodwork", "across": 3.7, "height": 0.2, "outside_box": None, "as_of": AS_OF},
            {"fixture_id": final, "side": 1, "seq": 3, "person_uid": MYERS, "minute": 49, "second": 2, "outcome": "off target", "across": 2.98, "height": 5.33,
             "outside_box": 1, "as_of": AS_OF},
            {"fixture_id": final, "side": 1, "seq": 4, "person_uid": MYERS, "minute": 62, "second": 42, "outcome": "blocked", "across": -2.7, "height": 0.18, "outside_box": None, "as_of": AS_OF}])
    with small_world.connect() as c:
        match = story_notebooks.match_notebook(c, final)
    assert match["goals"][0]["at"] == "19:07" and match["goals"][0]["where_it_went"] == "into the bottom corner"
    chances = match["chances_that_were_not_goals"]
    assert [(ch["at"], ch["what"]) for ch in chances] == [("45:29", "hit the post"), ("12:27", "saved low at the post"), ("62:42", "blocked"),
                                                          ("49:02", "over the bar")]
    assert chances[3]["from_outside_the_box"] is True and chances[0]["player"] == "Ken Myers" and chances[0]["team"] == "Chelsea"


def test_the_check_refuses_years_and_scores_not_in_the_notebook():
    notebook = {"season": "2062/63", "results": [{"score": "2-1"}], "date": "2063-05-26"}
    assert story_writer.text_faults("Chelsea won 1–2 away in 2063, then 2-1.", notebook) == []
    assert story_writer.text_faults("A 4-0 win in 2099.", notebook) == ["the year 2099", "the score 4-0"]


def test_the_check_refuses_a_draw_told_as_a_win_or_a_defeat():
    faults = story_writer.level_score_faults
    assert faults("The round was cruel to Sevilla, beaten 1-1 at home by Oviedo.") == ["'beaten 1-1': 1-1 is a draw, not a win or a defeat"]
    assert faults("A 2-2 defeat, then the 0-0 win.") == ["'2-2 defeat': 2-2 is a draw, not a win or a defeat",
                                                          "'0-0 win': 0-0 is a draw, not a win or a defeat"]
    for fine in ("Sevilla drew 1-1 with Oviedo.", "They won 4-3 on penalties after a 1-1 draw.", "Beaten 2-2 on aggregate, out on away goals.",
                 "Chelsea won the league; a 0-0 at Arsenal followed.", "They beat Oviedo 2-1."):
        assert faults(fine) == [], fine


def test_the_check_refuses_grounds_and_play_a_match_notebook_does_not_have():
    notebook = {"home": "Hertha Berlin", "home_form_before": [], "match_report": None, "stadium": "Anfield Stadium"}
    faults = story_writer.text_faults("At Anfield Stadium, then the Olympiastadion: a red card, but they booked their place.", notebook)
    assert faults == ["the ground 'Olympiastadion'", "'red card': the notebook has no match report, so tell nothing of the play beyond the goals"]
    assert story_writer.text_faults("A red card.", notebook | {"match_report": {"sent_off": ["X"]}}) == []


# -- buttons ------------------------------------------------------------------------------------------
def test_the_season_story_button_writes_and_shows_the_story(pages, small_world, fake_ai):
    fake_ai.append("The final that got away\n\nChelsea lost the 2062/63 FA Cup final 2-1 after extra time.")
    page = pages.post(f"/clubs/{CHELSEA}/2062/story", follow_redirects=True).get_data(as_text=True)
    assert "New season story written." in page and "The final that got away" in page
    story = db.session.get(ClubSeasonStory, (CHELSEA, 2062))
    assert (story.source, story.model) == ("generated", "test-model")


def test_a_story_wrong_twice_is_refused(pages, small_world, fake_ai):
    fake_ai.extend(["Won 9-0 in 2099.", "Still 9-0."])
    page = pages.post(f"/competitions/{FA_CUP}/2063/story", follow_redirects=True).get_data(as_text=True)
    assert "No story: The AI model&#39;s second draft still had faults: the score 9-0" in page
    assert db.session.get(EditionStory, (FA_CUP, 2063)) is None


def test_an_own_match_report_is_only_replaced_when_asked(pages, small_world, fake_ai):
    final = _final_id(small_world)
    with small_world.connect() as c:
        f = c.exec_driver_sql("select date, home_uid, away_uid from fixtures where id = ?", (final,)).one()
    db.session.add(MatchText(date=date.fromisoformat(str(f[0])), home_uid=f[1], away_uid=f[2], kind="report", text="My own report.",
                             source="curator"))
    db.session.commit()
    page = pages.post(f"/matches/{final}/report", follow_redirects=True).get_data(as_text=True)
    assert "written by you or by the game" in page and db.session.query(MatchText).one().text == "My own report."

    fake_ai.append("Liverpool edge it\n\nLiverpool won 2-1 after extra time.")
    pages.post(f"/matches/{final}/report", data={"replace": "1"})
    report = db.session.query(MatchText).one()
    assert (report.source, report.text.splitlines()[0]) == (GENERATED, "Liverpool edge it")


def test_a_match_without_the_game_report_gets_a_summary_through_the_competition(pages, small_world, monkeypatch):
    seen = []
    monkeypatch.setattr(story_writer, "_write", lambda prompt, notebook, voice=None: seen.append((prompt, notebook)) or ("A headline\n\nText.", "m"))
    pages.post(f"/matches/{_final_id(small_world)}/report")
    prompt, notebook = seen[0]
    assert prompt is story_writer.MATCH_SUMMARY_PROMPT and notebook.get("match_report") is None
    assert set(notebook["road_so_far"]) == {"Liverpool", "Chelsea"}


def test_an_answer_that_is_not_json_is_asked_for_again_and_another_key_is_read(app, monkeypatch):
    answers = ["Here is the story: Chelsea won.", '{"story": "Chelsea won the league."}']

    class Response:
        status_code = 200

        def __init__(self, content):
            self.content = content

        def json(self):
            return {"choices": [{"message": {"content": self.content}, "finish_reason": "stop"}]}

    sent = []
    monkeypatch.setattr(story_writer.requests, "post", lambda url, headers, json, timeout: sent.append(json) or Response(answers.pop(0)))
    with app.app_context():
        assert story_writer._request("deepseek-v4-pro", "key", [{"role": "user", "content": "notebook"}]) == "Chelsea won the league."
    assert len(sent) == 2 and "only with a JSON object" in sent[1]["messages"][-1]["content"]


def test_a_replaced_story_is_kept_and_can_be_restored(pages, small_world, fake_ai):
    from app.models.world_story import StoryVersion

    fake_ai.extend(["First story\n\nChelsea won the league.", "Second story\n\nChelsea won it again, in new words."])
    pages.post(f"/clubs/{CHELSEA}/2062/story")
    pages.post(f"/clubs/{CHELSEA}/2062/story")
    assert db.session.get(ClubSeasonStory, (CHELSEA, 2062)).text.startswith("Second story")
    kept = db.session.query(StoryVersion).one()
    assert kept.text.startswith("First story") and (kept.kind, kept.subject_uid, kept.year) == ("season", CHELSEA, 2062)
    assert "Earlier versions" in pages.get(f"/clubs/{CHELSEA}/2062").get_data(as_text=True)

    pages.post(f"/story-versions/{kept.id}/restore")
    assert db.session.get(ClubSeasonStory, (CHELSEA, 2062)).text.startswith("First story")
    assert [v.text.split("\n")[0] for v in db.session.query(StoryVersion)] == ["Second story"]


# -- club relationships -------------------------------------------------------------------------------
def _relations(engine) -> None:
    rows = [(CHELSEA, "favourite", MYERS, 1, 100, 1), (CHELSEA, "rival", LIVERPOOL, 2, 90, 0), (CHELSEA, "rival", ARSENAL, 1, 75, 0),
            (LIVERPOOL, "disliked", MYERS, 1, 80, 1), (LIVERPOOL, "rival", CHELSEA, 2, 85, 0)]
    with engine.begin() as c:
        c.execute(world.WorldClubRelation.__table__.insert(), [
            {"club_uid": club, "relation": relation, "subject_uid": subject, "reason": reason, "level": level, "permanent": permanent,
             "names_stadium": 0, "as_of": date(2063, 7, 1)} for club, relation, subject, reason, level, permanent in rows])


def test_a_club_shows_its_legends_and_rivals_and_a_player_where_he_is_loved(small_world, pages):
    _relations(small_world)
    with small_world.connect() as c:
        groups = q.club_relations(c, CHELSEA)
    assert [r["name"] for r in groups["Legend"]] == ["Ken Myers"]
    assert [(r["name"], r["reason_name"], r["level"]) for r in groups["Rival"]] == [("Liverpool", "Competitive", 90), ("Arsenal", "Local", 75)]
    club = pages.get(f"/clubs/{CHELSEA}").get_data(as_text=True)
    assert 'id="relations"' in club and "Legends" in club and "Rival clubs" in club and "Competitive" in club
    person = pages.get(f"/people/{MYERS}").get_data(as_text=True)
    assert "Legend at Chelsea" in person and "Disliked at Liverpool" in person


def test_the_notebooks_carry_the_clubs_relationships(small_world):
    _relations(small_world)
    with small_world.connect() as c:
        season = story_notebooks.club_season_notebook(c, CHELSEA, 2062)
        match = story_notebooks.match_notebook(c, _final_id(small_world))
        player = player_notebook.player_notebook(c, MYERS)
    assert season["club_relationships"]["legends"] == [{"name": "Ken Myers", "as": "Player", "level_of_100": 100}]
    assert [r["club"] for r in season["club_relationships"]["rival_clubs"]] == ["Liverpool", "Arsenal"]
    assert match["rivalry"] == {"home_sees_the_other_as": {"rival_level_of_100": 85, "rivalry": "Competitive"},
                                "away_sees_the_other_as": {"rival_level_of_100": 90, "rivalry": "Competitive"}}
    myers = next(g for g in match["goals"] if g["scorer"] == "Ken Myers")
    assert sorted(myers["standing_with_these_clubs"]) == ["disliked at Liverpool", "legend at Chelsea"]
    assert [(s["club"], s["standing"]) for s in player["standing_with_clubs"]] == [
        ("Chelsea", "club legend"), ("Liverpool", "disliked by the club and its fans")]


# -- the club's own page ------------------------------------------------------------------------------
def test_a_new_club_history_keeps_the_curators_text_which_can_be_restored(small_world, pages, fake_ai, monkeypatch):
    from app.models.world_story import ClubText, StoryVersion

    monkeypatch.setattr(story_writer, "request_json", lambda model, key, messages: fake_ai.pop(0))  # a JSON answer of two texts

    db.session.add(ClubText(club_uid=CHELSEA, profile_summary="Founded in 1905, the Blues.", historical_context="A long story of the club."))
    db.session.commit()
    with small_world.connect() as c:
        notebook = story_notebooks.club_notebook(c, CHELSEA)
    assert notebook["club"] == "Chelsea" and notebook["seasons"][0]["position"] == 1
    assert "1905" in notebook["club_background"]["text"]
    history = "Champions Again\n\nChelsea won the 2062/63 league with Ken Myers scoring.\n\nThe Blues go on."
    fake_ai.append({"summary": "Chelsea, champions of 2062/63.", "history": history})

    page = pages.post(f"/clubs/{CHELSEA}/about", follow_redirects=True).get_data(as_text=True)
    assert "New club history written" in page
    text = db.session.get(ClubText, CHELSEA)
    assert (text.profile_summary, text.source) == ("Chelsea, champions of 2062/63.", "generated")
    assert "<h3>Champions Again</h3>" in page and 'class="about-summary"' in page and "your own text" in page

    version = db.session.query(StoryVersion).filter_by(kind="club", subject_uid=CHELSEA).one()
    assert version.text.startswith("Founded in 1905") and version.source == "curator"
    pages.post(f"/story-versions/{version.id}/restore")
    text = db.session.get(ClubText, CHELSEA)
    assert (text.profile_summary, text.historical_context, text.source) == ("Founded in 1905, the Blues.", "A long story of the club.", None)
    assert db.session.query(StoryVersion).filter_by(kind="club", subject_uid=CHELSEA).one().source == "generated"


def test_a_trophys_runs_of_wins_are_counted_for_the_writer():
    seasons = ["1954/55", "2004/05", "2005/06"] + [f"{y}/{str(y + 1)[2:]}" for y in range(2022, 2063)]
    assert story_notebooks._runs(seasons) == {"longest_run": {"wins_in_a_row": 41, "from": "2022/23", "to": "2062/63"}}
    assert story_notebooks._runs(["2040", "2041", "2042", "2045", "2046"]) == {
        "longest_run": {"wins_in_a_row": 3, "from": "2040", "to": "2042"},
        "latest_run": {"wins_in_a_row": 2, "from": "2045", "to": "2046"}}


# -- voices and the style and number checks ------------------------------------------------------------
def test_figures_and_runs_of_titles_must_be_the_notebooks():
    facts = '{"crowd": 69344, "longest_run": {"wins_in_a_row": 41}, "old": "the 89,563-seat arena"}'
    assert story_writer.number_faults("a crowd of 69,344 in the 89,563-seat arena", facts) == []
    assert story_writer.number_faults("Ninety thousand souls, 90,000 of them", facts) == ["the number 90,000"]
    assert story_writer.number_faults("forty-one straight league titles", facts) == []
    assert len(story_writer.number_faults("Forty consecutive Premier League titles", facts)) == 1
    assert story_writer.number_faults("three goals in a row", facts) == []  # not a run of trophies
    assert story_writer.number_faults("a fifth straight title", '{"no": 1}') == []  # no runs counted: not checked


def test_a_stock_phrase_is_rewritten_once_then_tolerated(pages, small_world, fake_ai):
    assert story_writer.stock_phrases("A testament to their will, etched in blue.") == [
        "the stock phrase 'a testament to': say what you mean in concrete words of your own",
        "the stock phrase 'etched': say what you mean in concrete words of your own"]
    assert story_writer.stock_phrases("He sketched the plan.") == []
    fake_ai.extend(["The Cup\n\nA testament to Liverpool.", "The Cup\n\nStill a testament to Liverpool."])
    pages.post(f"/competitions/{FA_CUP}/2063/story")
    assert db.session.get(EditionStory, (FA_CUP, 2063)).text.endswith("Still a testament to Liverpool.")


def test_the_voice_chosen_opens_the_prompt_and_settings_hold_the_default(pages, small_world, fake_ai, monkeypatch):
    from app.services import writing_voices

    seen = []
    monkeypatch.setattr(story_writer, "_request", lambda model, key, messages: seen.append(messages[0]["content"]) or "The Cup\n\nText.")
    pages.post(f"/competitions/{FA_CUP}/2063/story", data={"voice": "hornby"})
    assert seen[-1].startswith("THE VOICE. Write as Nick Hornby")
    pages.post(f"/competitions/{FA_CUP}/2063/story", data={"voice": "house"})
    assert not seen[-1].startswith("THE VOICE")
    pages.post("/settings/voice", data={"voice": "galeano"})
    assert writing_voices.default_voice() == "galeano"
    pages.post(f"/competitions/{FA_CUP}/2063/story")  # no voice chosen: the default
    assert "Eduardo Galeano" in seen[-1]
    page = pages.get(f"/competitions/{FA_CUP}/2063").get_data(as_text=True)
    assert 'name="voice"' in page and '<option value="galeano" selected>' in page


def test_the_finances_tab_and_notebooks_tell_the_clubs_money(small_world, pages):
    with small_world.begin() as c:
        c.execute(world.WorldClubFinance.__table__.insert(), [
            {"club_uid": CHELSEA, "game_date": date(2062, 7, 1), "bank": 750_000_000, "transfer_budget": 1, "wage_budget": 1},
            {"club_uid": CHELSEA, "game_date": date(2063, 7, 1), "bank": 891_033_986, "transfer_budget": 1_160_074_559,
             "wage_budget": 8_000_000, "yearly_income": 1_300_458, "debt": 48_944_118}])
        c.execute(world.WorldClubIncome.__table__.insert(), [{"club_uid": CHELSEA, "seq": 0, "kind": 3, "total": 3_901_374, "yearly": 1_300_458,
                                                              "starts": date(2062, 8, 1), "ends": date(2065, 8, 1), "renews": 1, "as_of": date(2063, 7, 1)}])
        c.execute(world.WorldClubDebt.__table__.insert(), [{"club_uid": CHELSEA, "seq": 0, "source": 3, "amount": 48_944_118, "monthly_repayment": 703_329,
                                                            "monthly_interest": 120_661, "conditional_repayment": 0, "starts": date(2057, 1, 1),
                                                            "ends": date(2063, 12, 31), "in_fpp": 1, "as_of": date(2063, 7, 1)}])
    page = pages.get(f"/clubs/{CHELSEA}").get_data(as_text=True)
    assert 'id="finances"' in page and "£891.0m" in page and "£1.16bn" in page and "Stadium Sponsor" in page and "CVA" in page
    assert "Over the saves" in page and "£750.0m" in page
    with small_world.connect() as c:
        money = story_notebooks.club_notebook(c, CHELSEA)["finances"]
    assert money["bank_balance"] == {"pounds": 891_033_986, "euros": round(891_033_986 * 1.16)}
    assert money["debts"][0]["from"] == "CVA" and money["bank_balance_earlier"]["pounds"] == 750_000_000


def test_the_staff_tab_groups_a_clubs_staff_and_a_person_shows_the_job(small_world, pages):
    with small_world.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), [
            {"fm_uid": 1, "name": "Carl Dimario", "staff_job": "Manager", "staff_team_uid": CHELSEA, "staff_since": date(2022, 6, 27), "as_of": date(2063, 7, 1)},
            {"fm_uid": 2, "name": "Jake Askew", "staff_job": "Goalkeeping Coach", "staff_team_uid": CHELSEA, "staff_since": date(2022, 6, 27), "as_of": date(2063, 7, 1)}])
        c.exec_driver_sql(f"update people set staff_job = 'Coach', staff_team_uid = {CHELSEA}, staff_since = '2060-01-01' where fm_uid = {MYERS}")
    with small_world.connect() as c:
        staff = q.club_staff(c, CHELSEA)
    assert [(g["department"], [p["name"] for p in g["people"]]) for g in staff] == [
        ("Management", ["Carl Dimario"]), ("Coaching", ["Ken Myers", "Jake Askew"])]  # a coach before a goalkeeping coach
    assert staff[1]["people"][0]["played"]["games"] == 30  # Ken Myers played for the club
    page = pages.get(f"/clubs/{CHELSEA}").get_data(as_text=True)
    assert 'id="staff"' in page and "played here 2062/63" in page
    assert "<b>Manager</b> of" in pages.get("/people/1").get_data(as_text=True)


def test_a_clubs_managers_and_a_managers_career_are_shown(small_world, pages):
    with small_world.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), [
            {"fm_uid": 7, "name": "Felipe Toro", "staff_job": None, "staff_team_uid": None, "staff_since": None, "as_of": date(2063, 7, 1)},
            {"fm_uid": 8, "name": "Francesco Secchi", "staff_job": "Manager", "staff_team_uid": ARSENAL, "staff_since": date(2063, 7, 10), "as_of": date(2063, 7, 1)}])
        c.execute(world.WorldManagerSpell.__table__.insert(), [
            {"team_uid": ARSENAL, "seq": 0, "person_uid": 7, "started": date(2062, 12, 3), "ended": date(2063, 7, 5), "won": 14, "drawn": 10,
             "lost": 12, "reason": 3, "league_wins": 0, "cup_wins": 1, "as_of": date(2063, 7, 1)}])
    page = pages.get(f"/clubs/{ARSENAL}").get_data(as_text=True)
    assert "Francesco Secchi" in page and "Felipe Toro" in page and "Left manager role" in page and "0 league, 1 cup" in page
    career = pages.get("/people/7").get_data(as_text=True)
    assert "Managerial career" in career and "Arsenal" in career and "214" in career  # days in the job


def test_an_action_photo_is_made_in_the_background_and_kept_in_his_photos(small_world, pages, monkeypatch, app, tmp_path):
    from app.routes import world as world_routes

    monkeypatch.setitem(app.config, "GENERATED_DIR", tmp_path / "generated")  # never the archive's own folder

    monkeypatch.setattr(world_routes.world_portraits, "make_action_photo", lambda db_path, uid: (b"\x89PNG fake", "In action for Chelsea"))

    class NowThread:  # run the job at once
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(world_routes.threading, "Thread", NowThread)
    pages.post(f"/people/{MYERS}/action-photo")
    page = pages.get(f"/people/{MYERS}").get_data(as_text=True)
    assert "Done: a new photo, in action for Chelsea." in page and "In action for Chelsea" in page
    assert "location.reload()" not in page  # the page reloads itself only while the photo is being made


def test_an_action_photo_job_ends_even_when_the_maker_needs_the_app_or_fails(small_world, pages, monkeypatch, app, tmp_path):
    """The maker reads its settings and key from the app (WaveSpeed): the thread runs in the app context, and any error ends
    the job with a message instead of "Making the photo…" for ever."""
    from flask import has_app_context

    from app.routes import world as world_routes
    from app.services import picture_makers

    monkeypatch.setitem(app.config, "GENERATED_DIR", tmp_path / "generated")

    class NowThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(world_routes.threading, "Thread", NowThread)

    def needs_the_app(db_path, uid):
        assert has_app_context() and picture_makers.maker() == "draw_things"  # a settings lookup: fails outside the context
        return b"\x89PNG fake", "Shooting for Chelsea"

    monkeypatch.setattr(world_routes.world_portraits, "make_action_photo", needs_the_app)
    pages.post(f"/people/{MYERS}/action-photo")
    assert world_routes._photo_jobs[MYERS] == "Done: a new photo, shooting for Chelsea."

    def breaks(db_path, uid):
        raise RuntimeError("Working outside of application context")

    monkeypatch.setattr(world_routes.world_portraits, "make_action_photo", breaks)
    pages.post(f"/people/{MYERS}/action-photo")
    assert world_routes._photo_jobs[MYERS] == "Done, with an error: RuntimeError: Working outside of application context"


def test_where_a_goal_went_in_words():
    from app.services.story_notebooks import shot_words

    assert shot_words(3.3, 0.4) == "into the bottom corner" and shot_words(-2.5, 2.1) == "into the top corner"
    assert shot_words(0.3, 0.5) == "low into the net" and shot_words(1.0, 2.0) == "high into the net"
    assert shot_words(2.8, 1.2) == "inside the post" and shot_words(0.0, 1.3) == "into the middle of the goal"


def test_a_count_of_goals_the_notebook_does_not_hold_is_a_fault():
    """'27 goals' and 'the Champions League defeat in Naples' were invented for a transfer story (Hilgers, 8 Dec 2063)."""
    from app.services.story_writer import text_faults

    notebook = {"player": "Bruce Hilgers", "his_time_at_the_club": "since 2049/50: 154 games, 17 goals in league, cup and continental games"}
    assert "the count '27 goals'" in text_faults("His Chelsea career has included 27 goals, many of them important.", notebook)
    assert not [f for f in text_faults("His 17 goals in 154 games for the club tell the story. One goal was enough.", notebook) if f.startswith("the count")]


def test_an_injury_a_ban_or_a_leading_scorer_the_notebook_lacks_is_a_fault():
    """'Ponferrada will miss the injured Fran Belmonte, their leading scorer': the notebook had no injury and Rocchi
    had more goals (1 Nov 2026)."""
    from app.services.story_writer import absence_faults, hard_faults

    notebook = {"home": "Ponferrada", "key_duels": ["Fran Belmonte (left winger) against Antonius Maertens"],
                "home_expected_line_up": "Rosen, Rocchi, Salvador"}
    faults = absence_faults("Ponferrada will miss the injured Fran Belmonte, their leading scorer in the league.", notebook)
    assert len(faults) == 2 and "Belmonte being injured" in faults[0] and "leading scorer" in faults[1]
    assert hard_faults(faults) == faults
    assert absence_faults("Rocchi is suspended and Salvador remains sidelined.", notebook) and \
        absence_faults("Their top scorer Federico Rocchi starts.", notebook)
    told = notebook | {"home_injured": ["Fran Belmonte (twisted knee)"], "home_suspended": ["Federico Rocchi"],
                       "home_top_scorers": ["Federico Rocchi 3 goals"]}
    assert absence_faults("The injured Fran Belmonte is out; Rocchi is suspended; top scorer Rocchi.", told) == []
    assert absence_faults("Ponferrada will miss nobody of note.", notebook) == []


def test_a_continental_game_may_tell_the_league_only_as_background():
    """"Seventeen points clear as Chelsea march on" reported Chelsea 3-1 Shamrock Rovers in the Champions League (3 Feb
    2064): the clubs' own leagues may be background, never the opening."""
    from app.services import story_writer

    notebook = {"competition": "UEFA Champions League", "home": "Chelsea", "away": "Shamrock Rovers",
                "home_in_their_own_league": "English Premier Division: 1, 67 points from 24 games. Background only: another competition, "
                                            "which this game does not count in"}
    bad = "Seventeen points clear as Chelsea march on\nThe champions won again.\nChelsea lead the English Premier Division. More."
    faults = story_writer.own_league_lead_faults(bad, notebook)
    assert any("'Seventeen points clear'" not in f and "points clear" in f for f in faults)
    assert any("'English Premier Division' in the opening" in f for f in faults)
    good = ("Markoski defies Chelsea, but the Blues win 3-1\nPardal broke the dam.\nChelsea beat Shamrock Rovers 3-1 in the Champions League. "
            "More.\n\nAt home they lead the English Premier Division, a different matter entirely.")
    assert story_writer.own_league_lead_faults(good, notebook) == []
    assert story_writer.own_league_lead_faults(bad, {"competition": "English Premier Division"}) == []  # a league game: no check
