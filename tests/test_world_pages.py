"""The world queries and the new pages under /world, on a small world."""

from __future__ import annotations

import time
from datetime import date

import pytest

from app import create_app
from app.extensions import db
from app.models import world
from app.routes import world as world_routes
from app.services import world_queries as q
from app.services.save_import import create_tables

CHELSEA, LIVERPOOL, ARSENAL, SPURS, ENGLAND = 630, 662, 650, 700, 765
LEAGUE, FA_CUP, AWARD = 11, 1301426, 102406
MYERS = 2002312311
AS_OF = date(2063, 7, 1)


def _fixture(day: str, home: int, away: int, score: tuple[int, int] | None, competition: int, stage: int, **extra) -> dict:
    row = {"date": date.fromisoformat(day), "home_uid": home, "away_uid": away, "competition_uid": competition, "stage": stage,
           "home_goals": score[0] if score else None, "away_goals": score[1] if score else None,
           "home_goals_after_extra_time": None, "away_goals_after_extra_time": None, "home_penalties": None, "away_penalties": None,
           "attendance": 40000, "away_fans": 3000, "as_of": AS_OF}
    row.update(extra)
    return row


@pytest.fixture()
def app():
    application = create_app("testing")
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def small_world(app):
    engine = db.engines[world.BIND]
    create_tables(engine)
    with engine.begin() as c:
        def insert(model, rows):
            c.execute(model.__table__.insert(), rows)

        insert(world.WorldSave, [{"file_name": "main.fm", "game_date": AS_OF}])
        insert(world.WorldCompetition, [
            {"fm_uid": LEAGUE, "name": "English Premier Division", "short_name": "Premier Division", "abbreviation": "EPD"},
            {"fm_uid": FA_CUP, "name": "English FA Cup", "short_name": "FA Cup", "abbreviation": "FAC"},
        ])
        insert(world.WorldNation, [{"fm_uid": ENGLAND, "name": "England", "short_name": "England", "abbreviation": "ENG", "adjective": "English"}])
        clubs = {CHELSEA: "Chelsea", LIVERPOOL: "Liverpool", ARSENAL: "Arsenal", SPURS: "Tottenham"}
        insert(world.WorldClub, [{"fm_uid": u, "name": n, "short_name": n, "as_of": AS_OF} for u, n in clubs.items()])
        insert(world.WorldTeam, [{"fm_uid": u, "club_uid": u, "nation_uid": None, "name": n, "age_limit": None, "as_of": AS_OF} for u, n in clubs.items()]
               + [{"fm_uid": 2000292640, "club_uid": CHELSEA, "nation_uid": None, "name": "Chelsea", "age_limit": None, "as_of": AS_OF},
                  {"fm_uid": ENGLAND, "club_uid": None, "nation_uid": ENGLAND, "name": "England", "age_limit": None, "as_of": AS_OF}])
        insert(world.WorldPerson, [{"fm_uid": MYERS, "name": "Ken Myers", "first_name": "Ken", "last_name": "Myers",
                                    "birth_date": date(2040, 3, 1), "as_of": AS_OF, "nation_uid": ENGLAND}])
        stats = {"rated_appearances": 0, "assists": 0, "player_of_match": 0, "goals_conceded": 0, "clean_sheets": 0,
                 "yellow_cards": 0, "red_cards": 0, "minutes": 0, "rating_total": 0, "as_of": AS_OF, "seq": 0, "substitute_appearances": 0}
        insert(world.WorldPlayerSeason, [
            {**stats, "person_uid": MYERS, "team_uid": CHELSEA, "season_start": 2062, "kind": 1, "league_uid": LEAGUE, "starts": 30, "goals": 12},
            {**stats, "person_uid": MYERS, "team_uid": CHELSEA, "season_start": 2062, "kind": 2, "league_uid": LEAGUE, "starts": 3, "goals": 1},
        ])
        insert(world.WorldEdition, [
            {"competition_uid": LEAGUE, "year": 2063, "season_label": "2062/63", "decided_on": date(2063, 5, 20)},
            {"competition_uid": FA_CUP, "year": 2062, "season_label": "2061/62", "decided_on": date(2062, 5, 27)},
            {"competition_uid": FA_CUP, "year": 2063, "season_label": "2062/63", "decided_on": date(2063, 5, 26)},
        ])
        insert(world.WorldEditionPlace, [
            {"competition_uid": LEAGUE, "year": 2063, "place": 1, "team_uid": CHELSEA},
            {"competition_uid": LEAGUE, "year": 2063, "place": 2, "team_uid": LIVERPOOL},
            {"competition_uid": FA_CUP, "year": 2063, "place": 1, "team_uid": LIVERPOOL},
            {"competition_uid": FA_CUP, "year": 2063, "place": 2, "team_uid": CHELSEA},
        ])
        insert(world.WorldFixture, [
            _fixture("2062-08-20", CHELSEA, LIVERPOOL, (2, 0), LEAGUE, 162),
            _fixture("2062-08-27", ARSENAL, SPURS, (1, 1), LEAGUE, 162),
            _fixture("2063-02-10", LIVERPOOL, ARSENAL, (3, 0), LEAGUE, 162),
            _fixture("2063-05-20", SPURS, CHELSEA, (0, 2), LEAGUE, 162),
            _fixture("2062-05-30", ARSENAL, SPURS, (1, 0), FA_CUP, 3030),  # the next edition's first game, before the season
            _fixture("2063-04-21", CHELSEA, ARSENAL, (1, 0), FA_CUP, 3031),
            _fixture("2063-04-22", LIVERPOOL, SPURS, (2, 0), FA_CUP, 3031),
            _fixture("2063-05-26", LIVERPOOL, CHELSEA, (1, 1), FA_CUP, 3034, home_goals_after_extra_time=2, away_goals_after_extra_time=1),
            _fixture("2063-08-12", CHELSEA, ARSENAL, None, LEAGUE, 162),
        ])
        final = c.execute(world.WorldFixture.__table__.select().where(world.WorldFixture.__table__.c.stage == 3034)).first().id
        insert(world.WorldGoal, [
            {"fixture_id": final, "seq": 0, "scorer_uid": MYERS, "side": 1, "minute": 19, "added_time": 0, "kind": 0},
            {"fixture_id": final, "seq": 1, "scorer_uid": None, "side": 0, "minute": 34, "added_time": 0, "kind": 0},
            {"fixture_id": final, "seq": 2, "scorer_uid": None, "side": 0, "minute": 105, "added_time": 1, "kind": 0},
        ])
        insert(world.WorldAward, [{"fm_uid": AWARD, "name": "World Golden Ball", "short_name": "Golden Ball"}])
        insert(world.WorldAwardPlace, [{"award_uid": AWARD, "year": 2062, "place": 1, "person_uid": MYERS, "club_uid": CHELSEA,
                                        "age": 22, "appearances": 40, "goals": 20, "assists": 5, "rating_total": 7500, "votes": 300}])
    return engine


@pytest.fixture()
def pages(app, small_world, monkeypatch):
    monkeypatch.setattr(world_routes, "_pictures", {"at": time.monotonic(), "faces": {}, "old": {}})  # no face pack in tests
    monkeypatch.setattr(world_routes.world_portraits, "ARCHIVE_FACES", world_routes.world_portraits.ARCHIVE_FACES / "none")
    monkeypatch.setattr(world_routes, "_picture_queue", {"current": None, "waiting": [], "text": "", "stop": False, "running": False,
                                                         "last": set()})
    return app.test_client()


MLS, MLS_CUP, US_CUP = 40, 73, 1301415
ST_LOUIS, RED_BULLS, GALAXY, NYCFC, AMATEURS = 2000153732, 72000160, 72000150, 72000170, 99


@pytest.fixture()
def mls_world(app):
    """A league that keeps no history: its champion is the MLS Cup winner."""
    engine = db.engines[world.BIND]
    create_tables(engine)
    with engine.begin() as c:
        c.execute(world.WorldCompetition.__table__.insert(), [
            {"fm_uid": MLS, "name": "Major League Soccer", "short_name": "MLS", "abbreviation": "MLS"},
            {"fm_uid": MLS_CUP, "name": "MLS Cup", "short_name": "MLS Cup", "abbreviation": "MLSC"},
            {"fm_uid": US_CUP, "name": "US Cup", "short_name": "US Cup", "abbreviation": "USC"},
        ])
        clubs = {ST_LOUIS: "St. Louis City SC", RED_BULLS: "New York Red Bulls", GALAXY: "LA Galaxy", NYCFC: "New York City FC", AMATEURS: "Amateurs"}
        c.execute(world.WorldClub.__table__.insert(), [{"fm_uid": u, "name": n, "short_name": n, "as_of": AS_OF} for u, n in clubs.items()])
        c.execute(world.WorldTeam.__table__.insert(), [{"fm_uid": u, "club_uid": u, "nation_uid": None, "name": n, "age_limit": None, "as_of": AS_OF}
                                                       for u, n in clubs.items()])
        c.execute(world.WorldPlayerSeason.__table__.insert(), [{
            "person_uid": 1, "team_uid": ST_LOUIS, "season_start": 2062, "kind": 1, "seq": 0, "league_uid": MLS, "starts": 30,
            "substitute_appearances": 0, "rated_appearances": 0, "goals": 0, "assists": 0, "player_of_match": 0, "goals_conceded": 0,
            "clean_sheets": 0, "yellow_cards": 0, "red_cards": 0, "minutes": 0, "rating_total": 0, "as_of": AS_OF}])
        c.execute(world.WorldFixture.__table__.insert(), [
            _fixture("2061-09-20", ST_LOUIS, GALAXY, (1, 0), MLS, 993),
            _fixture("2061-10-04", NYCFC, RED_BULLS, (0, 0), MLS, 993),
            _fixture("2062-03-03", ST_LOUIS, RED_BULLS, (1, 0), MLS, 993),
            _fixture("2062-05-10", GALAXY, NYCFC, (2, 2), MLS, 993),
            _fixture("2062-10-09", RED_BULLS, GALAXY, (3, 1), MLS, 993),
            _fixture("2062-11-12", ST_LOUIS, RED_BULLS, (2, 1), MLS_CUP, 638),
            _fixture("2062-06-01", AMATEURS, GALAXY, (0, 4), US_CUP, 700),
        ])
        c.execute(world.WorldEdition.__table__.insert(), [
            {"competition_uid": MLS_CUP, "year": 2061, "season_label": "2061", "decided_on": date(2061, 11, 7)},
            {"competition_uid": MLS_CUP, "year": 2062, "season_label": "2062", "decided_on": date(2062, 11, 13)},
            {"competition_uid": US_CUP, "year": 2062, "season_label": "2062", "decided_on": date(2062, 9, 1)},
        ])
        c.execute(world.WorldEditionPlace.__table__.insert(), [
            {"competition_uid": MLS_CUP, "year": 2061, "place": 1, "team_uid": GALAXY},
            {"competition_uid": MLS_CUP, "year": 2061, "place": 2, "team_uid": NYCFC},
            {"competition_uid": MLS_CUP, "year": 2062, "place": 1, "team_uid": ST_LOUIS},
            {"competition_uid": MLS_CUP, "year": 2062, "place": 2, "team_uid": RED_BULLS},
            {"competition_uid": US_CUP, "year": 2062, "place": 1, "team_uid": GALAXY},
            {"competition_uid": US_CUP, "year": 2062, "place": 2, "team_uid": AMATEURS},
        ])
    return engine


def test_a_league_without_history_takes_its_champion_from_its_parts(mls_world):
    with mls_world.connect() as c:
        parts = q.league_parts(c, MLS)
        editions = q.competition_editions(c, MLS)
        season = q.edition(c, MLS, 2062)
    assert [p["competition_uid"] for p in parts] == [MLS_CUP]  # the US Cup has a team from outside the league
    assert [(e["year"], e["places"].get(1), e["own"]) for e in editions] == [(2062, ST_LOUIS, True), (2061, GALAXY, True)]
    assert (season["final"]["date"], season["places"][1]) == ("2062-11-12", ST_LOUIS)  # decided a day after the final
    assert [r["team_uid"] for r in season["table"]] == [RED_BULLS, ST_LOUIS, NYCFC, GALAXY]  # level on points and difference; more goals


def test_seasons_from_games_split_at_long_breaks(mls_world):
    with mls_world.connect() as c:
        seasons = q.fixture_seasons(c, MLS)
        window = q.league_window(c, MLS, 2062)
    assert [(s["year"], s["season_label"], s["start"], s["end"]) for s in seasons] == [
        (2062, "2062", "2062-03-03", "2062-10-09"), (2061, "2061", "2061-09-20", "2061-10-04")]
    assert window == ("2062-01-01", "2062-12-31")


def test_one_big_group_is_a_league_phase_with_a_table():
    teams = list(range(1, 11))
    games = [{"home_uid": h, "away_uid": a, "date": "2062-09-19", "home_goals": 1, "away_goals": 0} for h, a in zip(teams, teams[1:] + teams[:1])]
    rounds = q.round_names([games])
    assert rounds[0]["name"] == "League phase"
    assert len(q.table_from(games + [{**games[0], "home_goals": None}])) == 10  # an unplayed game does not count


def _games(*pairs, day="2063-01-01"):
    return [{"home_uid": h, "away_uid": a, "date": day} for h, a in pairs]


def test_rounds_are_named_back_from_the_final():
    quarter = _games((1, 2), (3, 4), (5, 6), (7, 8))
    quarter_second = _games((2, 1), (4, 3), (6, 5), (8, 7))
    semi = _games((1, 3), (5, 7))
    replay = _games((7, 5))
    third = _games((3, 5))
    final = _games((1, 7))
    groups = [_games((a, b), (b, c), (c, a)) for a, b, c in ((1, 2, 9), (3, 4, 10))]
    rounds = q.round_names([*groups, quarter, quarter_second, semi, replay, third, final])
    assert [(r["name"], len(r["games"]), r.get("legs")) for r in rounds] == [
        ("Group A", 3, None), ("Group B", 3, None), ("Quarter-finals", 8, "second legs"),
        ("Semi-finals", 3, "replays"), ("Third-place play-off", 1, None), ("Final", 1, None),
    ]


def test_an_unfinished_edition_counts_back_from_its_last_round():
    first = _games((1, 2), (3, 4), (5, 6), (7, 8), (9, 10))
    rounds = q.round_names([first, _games((1, 3), (5, 7))])
    assert [r["name"] for r in rounds] == ["Round 1", "Semi-finals"]


def test_an_edition_starts_after_the_previous_one_was_decided(small_world):
    with small_world.connect() as c:
        edition = q.edition(c, FA_CUP, 2063)
        assert q.edition_window(c, edition["edition"]) == ("2062-05-28", "2063-05-26")
        assert [r["name"] for r in edition["rounds"]] == ["Round 1", "Semi-finals", "Final"]
        assert edition["final"]["home_uid"] == LIVERPOOL
        league = q.edition(c, LEAGUE, 2063)
        assert league["final"] is None and league["is_league"]


def test_the_player_gets_the_league_title_and_the_lost_final(small_world):
    with small_world.connect() as c:
        honours = q.player_honours(c, MYERS)
    assert [(h["competition_uid"], h["place"], h["season"]) for h in honours] == [(LEAGUE, 1, 2062), (FA_CUP, 2, 2062)]


def test_the_league_table_is_built_from_the_results(small_world):
    with small_world.connect() as c:
        table = q.league_table(c, LEAGUE, "2062-07-01", "2063-06-30")
    assert [(r["team_uid"], r["points"], r["difference"]) for r in table] == [
        (CHELSEA, 6, 4), (LIVERPOOL, 3, 1), (SPURS, 1, -2), (ARSENAL, 1, -3)]
    assert table[0]["form"] == ["W", "W"]


def test_friendlies_are_not_tournaments():
    assert [q.is_friendly(n) for n in ("Friendly", "Testimonial", "Under 21 International", "English FA Cup", "Italian Youth Club Invitational")] == [
        True, True, True, False, False]


def test_search_shows_only_the_first_team_of_a_club(small_world):
    with small_world.connect() as c:
        found = q.search(c, "Chelsea")
    assert [t["fm_uid"] for t in found["teams"]] == [CHELSEA]


def test_every_page_renders(pages, small_world):
    with small_world.connect() as c:
        final = c.exec_driver_sql("select id from fixtures where stage = 3034").scalar()
    urls = ["/", "/search?q=Myers", "/search", f"/people/{MYERS}", "/clubs", f"/clubs/{CHELSEA}",
            f"/clubs/{CHELSEA}/2062", "/competitions", f"/competitions/{FA_CUP}", f"/competitions/{FA_CUP}/2063",
            f"/competitions/{LEAGUE}/2063", f"/matches/{final}", "/nations", f"/nations/{ENGLAND}",
            "/awards", f"/awards/{AWARD}"]
    for url in urls:
        response = pages.get(url)
        assert response.status_code == 200, url
    person = pages.get(f"/people/{MYERS}").get_data(as_text=True)
    assert "Ken Myers" in person and "Generate picture" in person
    match = pages.get(f"/matches/{final}").get_data(as_text=True)
    assert "2–1" in match and "after extra time" in match and "› Final" in match
    competitions = pages.get("/competitions").get_data(as_text=True)
    assert 'class="tabs"' in competitions and "English FA Cup" in competitions  # England is under "Other" here: no international matches
    clubs = pages.get("/clubs").get_data(as_text=True)
    assert "Your clubs" in clubs and f"/clubs/{CHELSEA}" in clubs
    club = pages.get(f"/clubs/{CHELSEA}").get_data(as_text=True)
    assert 'data-tab="squad"' in club and 'id="history"' in club


@pytest.mark.parametrize("url", ["/people/1", "/clubs/1", "/competitions/1", "/competitions/11/1999",
                                 "/matches/999999", "/nations/1", "/awards/1"])
def test_unknown_ids_are_not_found(pages, url):
    assert pages.get(url).status_code == 404


# -- bios ---------------------------------------------------------------------------------------------
def test_the_notebook_holds_the_career_finals_and_milestones(small_world):
    from app.services.player_notebook import player_notebook

    with small_world.connect() as c:
        notebook = player_notebook(c, MYERS, old_bio="Born in Dartford.")
    assert notebook["player"]["name"] == "Ken Myers" and notebook["player"]["nationality"] == "English"
    assert [(s["season"], s["club"], s["club_league_position"], s["won"]) for s in notebook["seasons"]] == [
        ("2062/63", "Chelsea", 1, ["English Premier Division"])]
    assert [(h["competition"], h["result"]) for h in notebook["honours"]] == [("English Premier Division", "won"), ("English FA Cup", "runner-up")]
    final = notebook["goals_in_finals"][0]
    assert (final["opponent"], final["score"], final["result"], final["after_extra_time"], final["goal_minutes"]) == (
        "Liverpool", "1-2", "lost", True, ["19'"])
    assert {m["milestone"] for m in notebook["milestones"]} >= {"first club games, for Chelsea", "first club goal, for Chelsea", "first trophy"}
    assert notebook["old_bio"]["text"] == "Born in Dartford."


def test_a_keepers_notebook_says_he_is_a_goalkeeper_with_his_clean_sheets(small_world):
    """Dejan Markoski, Shamrock Rovers' goalkeeper with no attribute record, was written as a defender (2 Nov 2026): the
    position from the team sheets is in the notebook, and a keeper's seasons carry clean sheets and goals conceded."""
    from app.services import world_queries as q
    from app.services.player_notebook import player_notebook

    with small_world.connect() as c:
        real = q.main_positions
        q.main_positions = lambda connection, uids: {MYERS: "GK"}
        try:
            notebook = player_notebook(c, MYERS)
        finally:
            q.main_positions = real
    assert notebook["player"]["main_position"] == "goalkeeper"
    assert {"league_clean_sheets", "league_goals_conceded"} <= set(notebook["seasons"][0])


def test_a_league_page_offers_its_missing_pictures_only_with_a_local_maker(pages, monkeypatch):
    from app.services import picture_makers

    with db.engines[world.BIND].begin() as c:  # Ken Myers is on Chelsea's books
        c.execute(world.WorldPerson.__table__.update().where(world.WorldPerson.__table__.c.fm_uid == MYERS).values(team_uid=CHELSEA))
    monkeypatch.setattr(picture_makers, "maker", lambda: "invoke")
    page = pages.get(f"/competitions/{LEAGUE}").get_data(as_text=True)
    assert "Make the missing pictures with InvokeAI (1)" in page  # Ken Myers, a Chelsea player with league games
    monkeypatch.setattr(picture_makers, "maker", lambda: "openai")
    page = pages.get(f"/competitions/{LEAGUE}").get_data(as_text=True)
    assert "only with a local maker" in page and "Make the missing pictures" not in page
    answer = pages.post(f"/competitions/{LEAGUE}/pictures", follow_redirects=True).get_data(as_text=True)
    assert "only with a local maker" in answer  # refused: nothing is drawn online


def test_a_leagues_pictures_are_made_one_after_another_and_fail_safely(pages, monkeypatch):
    from app.services import picture_makers

    with db.engines[world.BIND].begin() as c:
        c.execute(world.WorldPerson.__table__.update().where(world.WorldPerson.__table__.c.fm_uid == MYERS).values(team_uid=CHELSEA))
    monkeypatch.setattr(picture_makers, "maker", lambda: "invoke")
    monkeypatch.setattr(picture_makers, "online", lambda: (True, ""))
    monkeypatch.setattr(world_routes, "_forget_pictures", lambda: None)  # no real face pack in a test
    made = []
    monkeypatch.setattr(world_routes.world_portraits, "generate_one", lambda world_db, uid: made.append(uid))
    pages.post(f"/competitions/{LEAGUE}/pictures")
    for _ in range(100):
        if world_routes._picture_queue["text"].startswith("Done"):
            break
        time.sleep(0.02)
    assert made == [MYERS] and world_routes._picture_queue["text"] == "Done (finished): 1 new pictures."

    def broken(world_db, uid):
        raise world_routes.world_portraits.PortraitError("InvokeAI does not answer")

    monkeypatch.setattr(world_routes.world_portraits, "generate_one", broken)
    monkeypatch.setattr(world_routes, "LEAGUE_FAILURES", 1)
    pages.post(f"/competitions/{LEAGUE}/pictures")
    for _ in range(100):
        if world_routes._picture_queue["text"].startswith("Done"):
            break
        time.sleep(0.02)
    assert world_routes._picture_queue["text"].startswith("Done, with an error: 0 new pictures, then 1 failed in a row")


def test_a_squads_pictures_are_made_with_the_makers_setting(pages, monkeypatch):
    """The squad's "Generate missing pictures" ran outside the app: reading the maker failed, the thread died and the page
    said "Starting…" for ever (2 Nov 2026)."""
    from app.services import picture_makers

    monkeypatch.setattr(world_routes, "_forget_pictures", lambda: None)
    asked = []

    def generate_team(world_db, team, season, progress):
        asked.append(picture_makers.maker())  # needs the app's context
        return 1

    monkeypatch.setattr(world_routes.world_portraits, "generate_team", generate_team)
    pages.post(f"/clubs/{CHELSEA}/2062/pictures")
    for _ in range(100):
        if world_routes._team_jobs[(CHELSEA, 2062)].startswith("Done"):
            break
        time.sleep(0.02)
    assert asked == ["draw_things"] and world_routes._team_jobs[(CHELSEA, 2062)] == "Done: 1 new pictures."


def test_the_bio_check_refuses_years_and_scores_that_are_not_in_the_notebook():
    from app.services.bio_writer import Draft, bio_faults

    notebook = {"seasons": [{"season": "2062/63"}], "goals_in_finals": [{"score": "1-2", "penalties": None}]}
    good = Draft("Ken Myers won the league in 2063.", "He scored in the 2-1 defeat in the 2062/63 final.")
    bad = Draft("A 3-1 win in 2099.", "Nothing else.")
    assert bio_faults(good, notebook) == []
    assert bio_faults(bad, notebook) == ["the year 2099", "the score 3-1"]


@pytest.fixture()
def fake_ai(monkeypatch):
    from app.services import bio_writer

    monkeypatch.setattr(bio_writer, "get_deepseek_model", lambda: "test-model")
    monkeypatch.setattr(bio_writer, "resolve_ai_api_key", lambda key, model=None: "test-key")
    drafts = []
    monkeypatch.setattr(bio_writer, "_request", lambda model, key, messages: drafts.pop(0))
    return drafts


def test_the_bio_button_saves_a_bio_and_the_page_shows_it(pages, fake_ai):
    from app.models.person_bio import PersonBio
    from app.services.bio_writer import Draft

    fake_ai.append(Draft("Ken Myers is a Chelsea forward.", "The final\n\nHe scored in the 2062/63 FA Cup final."))
    response = pages.post(f"/people/{MYERS}/bio", follow_redirects=True)
    page = response.get_data(as_text=True)
    assert "New bio written." in page and "Ken Myers is a Chelsea forward." in page and "The story of Ken Myers" in page
    bio = db.session.get(PersonBio, MYERS)
    assert (bio.model, bio.game_date) == ("test-model", AS_OF)


def test_a_bio_that_is_wrong_twice_is_refused(pages, fake_ai):
    from app.models.person_bio import PersonBio
    from app.services.bio_writer import Draft

    fake_ai.extend([Draft("Won in 2099.", "x"), Draft("Still 2099.", "x")])
    page = pages.post(f"/people/{MYERS}/bio", follow_redirects=True).get_data(as_text=True)
    assert "No bio: The AI model&#39;s second draft still had faults: the year 2099" in page
    assert db.session.get(PersonBio, MYERS) is None


def test_a_wrong_draft_gets_one_more_try(pages, fake_ai):
    from app.models.person_bio import PersonBio
    from app.services.bio_writer import Draft

    fake_ai.extend([Draft("Won in 2099.", "x"), Draft("Won the league in 2063.", "A Chelsea forward.")])
    pages.post(f"/people/{MYERS}/bio")
    assert db.session.get(PersonBio, MYERS).summary == "Won the league in 2063."


def test_a_retired_player_without_nationality_takes_the_nation_they_scored_for(small_world):
    with small_world.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 2000099999, "name": "Old Striker", "as_of": AS_OF}])
        c.execute(world.WorldFixture.__table__.insert(), [_fixture("2040-06-10", ENGLAND, SPURS, (1, 0), None, 1)])
        fixture = c.exec_driver_sql("select id from fixtures where date = '2040-06-10'").scalar()
        c.execute(world.WorldGoal.__table__.insert(), [{"fixture_id": fixture, "seq": 0, "scorer_uid": 2000099999, "side": 0,
                                                        "minute": 9, "added_time": 0, "kind": 0}])
    with small_world.connect() as c:
        assert q.person(c, 2000099999)["nationality"] == "English"
        assert q.person(c, MYERS)["nationality"] == "English"  # from the save itself


def test_the_squad_shows_running_loans_on_both_sides(small_world):
    def person(uid, name, team):
        return {"fm_uid": uid, "name": name, "birth_date": date(2040, 1, 1), "team_uid": team, "as_of": AS_OF}

    def move(uid, kind, frm, to, day):
        return {"person_uid": uid, "date": date.fromisoformat(day), "kind": kind, "from_uid": frm, "to_uid": to, "fee": 0, "fee_with_add_ons": 0}

    with small_world.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), [
            person(1, "Stays", CHELSEA), person(2, "Two-season loan", CHELSEA), person(3, "Loan ended", CHELSEA),
            person(4, "Loan with history at the loan club", LIVERPOOL)])
        c.execute(world.WorldTransfer.__table__.insert(), [
            move(2, 3, CHELSEA, ARSENAL, "2062-07-10"), move(3, 3, CHELSEA, SPURS, "2062-07-10"), move(4, 3, CHELSEA, LIVERPOOL, "2063-06-20")])
        stats = {"kind": 1, "seq": 0, "league_uid": LEAGUE, "starts": 0, "substitute_appearances": 0, "rated_appearances": 0, "goals": 0,
                 "assists": 0, "player_of_match": 0, "goals_conceded": 0, "clean_sheets": 0, "yellow_cards": 0, "red_cards": 0,
                 "minutes": 0, "rating_total": 0, "as_of": AS_OF}
        c.execute(world.WorldPlayerSeason.__table__.insert(), [{**stats, "person_uid": 2, "team_uid": ARSENAL, "season_start": 2063}])
    with small_world.connect() as c:
        chelsea, arsenal = q.club_squad(c, CHELSEA), q.club_squad(c, ARSENAL)
    assert [p["name"] for p in chelsea["teams"][0]["players"]] == ["Loan ended", "Stays"]
    assert {(o["name"], o["to_uid"]) for o in chelsea["out_on_loan"]} == {("Two-season loan", ARSENAL), ("Loan with history at the loan club", LIVERPOOL)}
    assert [(p["name"], p["on_loan_from"]) for p in arsenal["teams"][0]["players"]] == [("Two-season loan", CHELSEA)]



def test_old_world_addresses_redirect(pages):
    response = pages.get("/world/clubs/630?season=2062")
    assert response.status_code == 301 and response.headers["Location"].endswith("/clubs/630?season=2062")
    assert pages.get("/world/").headers["Location"].endswith("/")


def _leg(fid, day, home, away, score, pens=None):
    return {"id": fid, "date": day, "home_uid": home, "away_uid": away, "home_goals": score[0], "away_goals": score[1],
            "home_goals_after_extra_time": None, "away_goals_after_extra_time": None,
            "home_penalties": pens[0] if pens else None, "away_penalties": pens[1] if pens else None}


def test_knockout_ties_add_legs_decide_on_penalties_and_form_a_tree():
    from app.services import world_queries as q

    semis = [_leg(1, "2063-05-01", 1, 2, (1, 0)), _leg(2, "2063-05-08", 2, 1, (2, 1)),  # 2-2: team 1 through 6-5 on penalties
             _leg(3, "2063-05-02", 3, 4, (0, 3)), _leg(4, "2063-05-09", 4, 3, (1, 1))]
    semis[1]["home_penalties"], semis[1]["away_penalties"] = 5, 6
    final = [_leg(5, "2063-06-02", 4, 1, (2, 0))]
    tie = next(t for t in q.ties(semis) if 1 in t["teams"])
    assert (tie["teams"], tie["aggregate"], tie["penalties"], tie["winner"]) == ((1, 2), [2, 2], (6, 5), 1)
    stages = q.edition_stages([{"name": "Semi-finals", "games": semis}, {"name": "Final", "games": final}])
    semi_order = [t["teams"] for t in stages["bracket"][0]["ties"]]
    assert semi_order == [(3, 4), (1, 2)]  # in the final's order: team 4 (home) first, then team 1
    assert stages["bracket"][1]["ties"][0]["winner"] == 4


def test_next_round_names_only_what_the_tree_shows_on_the_day():
    from app.services import world_queries as q

    quarters = [_leg(1, "2063-04-17", 1, 2, (1, 0)), _leg(2, "2063-04-17", 3, 4, (3, 0)),
                _leg(3, "2063-04-18", 5, 6, (2, 1)), _leg(4, "2063-04-18", 7, 8, (0, 1))]
    semis = [_leg(5, "2063-05-01", 1, 3, (0, 2)), _leg(6, "2063-05-02", 5, 8, (2, 2))]
    stages = q.edition_stages([{"name": "Quarter-finals", "games": quarters}, {"name": "Semi-finals", "games": semis}])
    assert q.next_round(stages, quarters[0]) == {"winner": 1, "known": True, "round": "Semi-finals", "opponent": 3,
                                                 "next_game": {"home": 1, "away": 3, "date": "2063-05-01"}}
    after = q.next_round(stages, quarters[0] | {"date": "2063-04-16"})  # the other quarter-final was still to come
    assert after["winner_of"] == (3, 4) and "opponent" not in after
    assert q.next_round(stages, quarters[2], drawn=True)["draw_to_come"]  # a cup drawn round by round
    assert q.next_round(stages, semis[0]) == {"winner": 3, "known": False}  # no final in the save yet


def test_a_running_league_shows_the_latest_games_of_its_top_clubs():
    from app.services.world_queries import _big_games

    table = [{"team_uid": uid} for uid in (1, 2, 3, 4, 5, 6)]
    games = [{"home_uid": 5, "away_uid": 6}, {"home_uid": 3, "away_uid": 4}, {"home_uid": 2, "away_uid": 1}]
    now = {"finished": False, "recent": games, "table": table}
    assert _big_games(now) == [games[2], games[1], games[0]]
    assert _big_games(now, followed=(6,))[0] == games[0]  # the followed club's game first
    assert _big_games({**now, "finished": True}) == []  # a finished season shows its table only


def test_a_continental_appearance_wins_no_domestic_cup(small_world):
    from sqlalchemy import text as sql

    with small_world.begin() as c:
        c.execute(sql("update competitions set scope = 'cup' where fm_uid = :c"), {"c": FA_CUP})
        c.execute(sql("update player_seasons set kind = 3 where person_uid = :p and kind = 2"), {"p": MYERS})  # Europe only
    with small_world.connect() as c:
        assert [h["competition_uid"] for h in q.player_honours(c, MYERS)] == [LEAGUE]  # no FA Cup final for him
    with small_world.begin() as c:
        c.execute(sql("update player_seasons set kind = 2 where person_uid = :p and kind = 3"), {"p": MYERS})
    with small_world.connect() as c:
        assert [h["competition_uid"] for h in q.player_honours(c, MYERS)] == [LEAGUE, FA_CUP]  # a cup game: the final counts


def test_a_players_national_team_titles_count_in_the_seasons_he_played_for_it(small_world):
    """The save files international games under the player's club: his nation and the season tell (World Cup 2062,
    decided in July, belongs to the season 2061/62)."""
    with small_world.begin() as c:
        c.execute(world.WorldCompetition.__table__.insert(), [{"fm_uid": 1301385, "name": "World Cup", "short_name": "World Cup",
                                                                "abbreviation": "WC", "scope": "national_teams"}])
        c.execute(world.WorldEdition.__table__.insert(), [{"competition_uid": 1301385, "year": y, "season_label": str(y), "decided_on": date(y, 7, 10)}
                                                          for y in (2058, 2062)])
        c.execute(world.WorldEditionPlace.__table__.insert(), [{"competition_uid": 1301385, "year": y, "place": 1, "team_uid": ENGLAND} for y in (2058, 2062)])
        stats = {"substitute_appearances": 0, "rated_appearances": 0, "assists": 0, "player_of_match": 0, "goals_conceded": 0,
                 "clean_sheets": 0, "yellow_cards": 0, "red_cards": 0, "minutes": 0, "rating_total": 0, "as_of": AS_OF, "seq": 0}
        c.execute(world.WorldPlayerSeason.__table__.insert(), [{**stats, "person_uid": MYERS, "team_uid": CHELSEA, "season_start": 2061, "kind": 4,
                                                                 "league_uid": None, "starts": 6, "goals": 2}])
    with small_world.connect() as c:
        titles = [(h["competition_uid"], h["year"], h["season"]) for h in q.player_honours(c, MYERS) if h["competition_uid"] == 1301385]
    assert titles == [(1301385, 2062, 2061)]  # not 2058: he was not capped then


def test_the_person_page_shows_the_hidden_traits_as_character(pages, small_world):
    """Parkins and Pardal (26 Oct 2026): the game's hidden traits on the page, the marked ones in words."""
    from app.services.world_queries import character_words

    traits = {"Adaptability": 11, "Ambition": 16, "Loyalty": 13, "Pressure": 18, "Professionalism": 15, "Sportsmanship": 9, "Temperament": 14,
              "Controversy": 9, "Dirtiness": 10, "Consistency": 17, "Important Matches": 20, "Injury Proneness": 3, "Versatility": 14}
    with small_world.begin() as c:
        save = c.exec_driver_sql("select id from saves limit 1").scalar()
        c.execute(world.WorldPlayerSnapshot.__table__.insert(), [{"person_uid": MYERS, "save_id": save, "height_cm": 180, "weight_kg": 75,
                                                                   "attributes": {"Finishing": 15}, "traits": traits}])
    person = pages.get(f"/people/{MYERS}").get_data(as_text=True)
    assert "Character: a big-game player, thrives under pressure, the same every week, fiercely ambitious." in person
    assert "Important Matches" not in person  # the numbers are for the writers, not the page
    assert character_words({"Temperament": 4, "Versatility": 19, "Controversy": 9}) == ["a short fuse", "plays anywhere he is asked"]
    assert character_words(None) == [] and character_words({"Ambition": 12}) == []


def test_the_user_can_give_a_player_their_own_picture(pages, monkeypatch):
    import io

    from app.services import world_portraits

    given = []
    monkeypatch.setattr(world_portraits, "use_own_picture", lambda uid, data: given.append((uid, data)))
    monkeypatch.setattr(world_routes, "_forget_pictures", lambda: None)  # no real face pack in a test
    assert "Use my picture" in pages.get(f"/people/{MYERS}").get_data(as_text=True)
    answer = pages.post(f"/people/{MYERS}/own-picture", data={"picture": (io.BytesIO(b"png"), "me.png")},
                        content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert given == [(MYERS, b"png")] and "Your picture is now his picture" in answer
    answer = pages.post(f"/people/{MYERS}/own-picture", data={}, content_type="multipart/form-data", follow_redirects=True)
    assert "Choose a picture first" in answer.get_data(as_text=True) and len(given) == 1


def test_the_pictures_page_lists_the_leagues_with_missing_pictures_and_queues_them(pages, monkeypatch):
    from app.services import picture_makers

    with db.engines[world.BIND].begin() as c:
        c.execute(world.WorldPerson.__table__.update().where(world.WorldPerson.__table__.c.fm_uid == MYERS).values(team_uid=CHELSEA))
    monkeypatch.setattr(picture_makers, "maker", lambda: "invoke")
    monkeypatch.setattr(picture_makers, "online", lambda: (True, ""))
    monkeypatch.setattr(world_routes, "_forget_pictures", lambda: None)
    page = pages.get("/pictures").get_data(as_text=True)
    assert f'name="league" value="{LEAGUE}" data-missing="1"' in page and "with InvokeAI" in page
    assert "Tick at least one league" in pages.post("/pictures", data={}, follow_redirects=True).get_data(as_text=True)
    made = []
    monkeypatch.setattr(world_routes.world_portraits, "generate_one", lambda world_db, uid: made.append(uid))
    pages.post("/pictures", data={"league": [str(LEAGUE)]})
    for _ in range(100):
        if world_routes._picture_queue["text"].startswith("Done"):
            break
        time.sleep(0.02)
    assert made == [MYERS] and world_routes._picture_queue["text"] == "Done (finished): 1 new pictures."
    assert not world_routes._picture_queue["running"] and world_routes._picture_queue["last"] == {LEAGUE}
