"""The public site (app/public): published stories only, each story once, and none of the archive's own pages."""

from datetime import date

import pytest

from app.extensions import db
from app.models.world_soccer_article import WorldSoccerArticle
from app.models.world_soccer_issue import WorldSoccerIssue
from app.public import create_public_app, section_of, stories


@pytest.fixture()
def site():
    app = create_public_app("testing")
    with app.app_context():
        db.create_all()

        def issue(day: date, status: str, articles: list[tuple[str, str, str, str]]) -> WorldSoccerIssue:
            made = WorldSoccerIssue(publication_date=day, knowledge_cutoff=day, status=status, edition_name="The English Daily")
            for order, (headline, section, key, prominence) in enumerate(articles):
                made.articles.append(WorldSoccerArticle(headline=headline, section=section, lead_key=key, prominence=prominence,
                                                        body="First paragraph.\n\nSecond <b>paragraph</b>.", byline="Sports desk",
                                                        standfirst=f"About {headline}.", article_type="report", sort_order=order))
            db.session.add(made)
            return made

        issue(date(2063, 10, 19), "published", [("Chelsea ten from ten", "Form", "run:630", "brief")])
        issue(date(2063, 10, 27), "published", [("Chelsea's run goes on", "Form", "run:630", "lead"),
                                                ("AEK v Chelsea: what to expect", "Coming up", "preview:1", "standard"),
                                                ("English clubs in Europe", "Europe", "european_nights:765", "brief")])
        issue(date(2063, 10, 27), "draft", [("A draft nobody should see", "Results", "big_win:1", "lead")])
        db.session.commit()
        yield app


@pytest.fixture()
def a_logo(tmp_path, monkeypatch):
    """Chelsea's crest in a logo folder of the test's own: the real Logos pack is not in the repository."""
    from app.services import logo_assets

    root = tmp_path / "Logos"
    for size in ("Normal", "Small"):
        (root / "Club" / size).mkdir(parents=True)
        (root / "Club" / size / "630.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(logo_assets, "LOGO_ROOT", root)
    logo_assets.resolve_logo_path.cache_clear()  # a lookup made before, with the real folder, must not answer
    logo_assets.pack_index.cache_clear()
    yield root
    logo_assets.resolve_logo_path.cache_clear()
    logo_assets.pack_index.cache_clear()


def test_only_published_stories_each_once(site):
    with site.app_context():
        found = stories()
    assert [a.headline for a in found] == ["Chelsea's run goes on", "AEK v Chelsea: what to expect", "English clubs in Europe"]
    assert section_of("Coming up") == "coming-up" and section_of("Champions League") == "europe" and section_of("Odd") == "news"


def test_the_pages_show_the_stories_and_hide_drafts(site):
    pages = site.test_client()
    front = pages.get("/").data.decode()
    assert "Chelsea&#39;s run goes on" in front and "A draft nobody should see" not in front and "Coming up" in front
    europe = pages.get("/section/europe").data.decode()
    assert "English clubs in Europe" in europe and "AEK" not in europe
    with site.app_context():
        draft = WorldSoccerArticle.query.filter_by(headline="A draft nobody should see").one()
        published = WorldSoccerArticle.query.filter_by(headline="Chelsea's run goes on").one()
    assert pages.get(f"/story/{draft.id}").status_code == 404
    story = pages.get(f"/story/{published.id}/chelseas-run-goes-on").data.decode()
    assert "<p>First paragraph.</p>" in story and "Second &lt;b&gt;paragraph&lt;/b&gt;." in story  # the body is escaped


def test_none_of_the_archive_is_reachable(site):
    pages = site.test_client()
    for path in ("/desk", "/settings", "/people/1", "/news/world-soccer", "/matches/1", "/static/generated/../../fm_archive.sqlite3",
                 "/static/generated/notes.txt", "/section/nothing"):
        assert pages.get(path).status_code == 404, path
    assert pages.post("/").status_code == 405


def _league(app) -> None:
    """A league of three clubs: two results and a fixture in October 2063."""
    from app.models import world

    day = date(2063, 10, 1)
    with app.app_context():
        engine = db.engines[world.BIND]
        with engine.begin() as c:
            c.execute(world.WorldCompetition.__table__.insert(), [{"fm_uid": 11, "name": "English Premier Division", "short_name": "Premier Division",
                                                                    "abbreviation": "EPD", "scope": "league", "level": 1, "reputation": 190}])
            clubs = {630: "Chelsea", 662: "Liverpool", 650: "Arsenal"}
            c.execute(world.WorldClub.__table__.insert(), [{"fm_uid": u, "name": n, "short_name": n, "as_of": day} for u, n in clubs.items()])
            c.execute(world.WorldTeam.__table__.insert(), [{"fm_uid": u, "club_uid": u, "nation_uid": None, "name": n, "age_limit": None, "as_of": day}
                                                           for u, n in clubs.items()])
            rows = [(date(2063, 10, 5), 630, 662, 3, 0), (date(2063, 10, 12), 650, 630, 1, 1), (date(2063, 10, 19), 662, 650, None, None)]
            c.execute(world.WorldFixture.__table__.insert(), [{"date": d, "home_uid": h, "away_uid": a, "competition_uid": 11, "stage": 1,
                                                               "home_goals": hg, "away_goals": ag, "as_of": day} for d, h, a, hg, ag in rows])
            stats = {"substitute_appearances": 0, "rated_appearances": 0, "goals": 0, "assists": 0, "player_of_match": 0, "goals_conceded": 0,
                     "clean_sheets": 0, "yellow_cards": 0, "red_cards": 0, "minutes": 0, "rating_total": 0, "as_of": day, "seq": 0}
            c.execute(world.WorldPlayerSeason.__table__.insert(), [{**stats, "person_uid": 1, "team_uid": 630, "season_start": 2063, "kind": 1,
                                                                    "league_uid": 11, "starts": 2}])  # makes it a league


def test_the_scores_tables_and_club_pages(site):
    _league(site)
    pages = site.test_client()
    table = pages.get("/tables/11").data.decode()
    assert table.index("Chelsea") < table.index("Arsenal") < table.index("Liverpool")  # 4 points, 1, 0
    assert "<b>4</b>" in table
    scores = pages.get("/scores").data.decode()
    assert "1-1" in scores and "19 Oct" in scores and "3-0" not in scores  # the latest round only
    club = pages.get("/clubs/630/chelsea").data.decode()
    assert "1st in the" in club and "English Premier Division" in club and "3-0" in club
    front = pages.get("/").data.decode()
    assert "Latest scores" in front and "Table ›" in front
    assert pages.get("/clubs/1/nobody").status_code == 404 and pages.get("/tables/99").status_code == 404


def test_a_european_story_is_in_europe_and_in_its_own_section(site):
    from app.models import world
    from app.models.world_soccer_entity_link import WorldSoccerEntityLink

    _league(site)
    with site.app_context():
        with db.engines[world.BIND].begin() as c:
            c.execute(world.WorldCompetition.__table__.insert(), [{"fm_uid": 1301394, "name": "UEFA Champions League", "short_name": "Champions League",
                                                                    "abbreviation": "UCL", "scope": "continental", "reputation": 200}])
            c.execute(world.WorldFixture.__table__.insert(), [{"date": date(2063, 11, 6), "home_uid": 662, "away_uid": 630, "competition_uid": 1301394,
                                                               "stage": 1, "as_of": date(2063, 10, 27)}])
        preview = WorldSoccerArticle.query.filter_by(headline="AEK v Chelsea: what to expect").one()
        preview.entity_links.append(WorldSoccerEntityLink(entity_type="match", entity_id=662, world_ref="match:2063-11-06:662:630", label="AEK v Chelsea"))
        db.session.commit()
    pages = site.test_client()
    europe = pages.get("/section/europe").data.decode()
    assert "AEK v Chelsea" in europe and "English clubs in Europe" in europe
    assert "AEK v Chelsea" in pages.get("/section/coming-up").data.decode()


def test_a_played_score_opens_its_match_page(site):
    from app.models import world

    _league(site)
    with site.app_context():
        with db.engines[world.BIND].connect() as c:
            played, later = (c.exec_driver_sql(f"select id from fixtures where date = '{d}'").scalar() for d in ("2063-10-05", "2063-10-19"))
    pages = site.test_client()
    assert f"/matches/{played}/" in pages.get("/clubs/630/chelsea").data.decode()  # the 3-0 links to its match
    page = pages.get(f"/matches/{played}/chelsea-liverpool").data.decode()
    assert "<b>3</b><b>0</b>" in page and "Chelsea" in page and "Liverpool" in page and "no detailed report" in page
    coming = pages.get(f"/matches/{later}").data.decode()  # not played yet: its preview
    assert "Liverpool v Arsenal: preview" in coming and "Head to head" not in coming and "Form" in coming
    assert f"/matches/{later}/" in pages.get("/clubs/650/arsenal").data.decode()  # the fixture links to it
    assert pages.get("/matches/987654").status_code == 404


def test_a_player_page_and_the_links_to_it(site):
    from app.models import world
    from tests.test_story_leads import _match_player

    _league(site)
    with site.app_context():
        with db.engines[world.BIND].begin() as c:
            c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 2002756238, "name": "Pablo Ferrer", "as_of": date(2063, 10, 1),
                                                              "team_uid": 630, "birth_date": date(2039, 5, 1)}])
            played = c.exec_driver_sql("select id from fixtures where date = '2063-10-05'").scalar()
            c.execute(world.WorldMatchPlayer.__table__.insert(), [_match_player(played, 0, 0, 2002756238, 8.4, goals=2, xg=1.1,
                                                                                start_position="ST (C)", end_position="ST (C)", shirt_number=26)])
            c.execute(world.WorldGoal.__table__.insert(), [{"fixture_id": played, "seq": i, "scorer_uid": 2002756238, "side": 0, "minute": m,
                                                           "added_time": 0, "kind": 0} for i, m in enumerate((71, 88))])
    pages = site.test_client()
    page = pages.get("/players/2002756238/pablo-ferrer").data.decode()
    assert "Pablo Ferrer" in page and "Chelsea" in page and "<b>2</b>goals" in page and "8.4" in page
    match = pages.get(f"/matches/{played}").data.decode()
    assert match.count('href="/players/2002756238/pablo-ferrer"') >= 2  # the scorer and the player on the pitch
    assert "71&#39;" in match or "71'" in match
    assert pages.get("/players/1").status_code == 404


def _history(app) -> None:
    """Chelsea champions of 2061/62 and 2062/63 (Liverpool second), a 260-goal Chelsea striker who won the Golden Ball."""
    from app.models import world

    _league(app)
    with app.app_context():
        with db.engines[world.BIND].begin() as c:
            c.execute(world.WorldEdition.__table__.insert(), [{"competition_uid": 11, "year": y, "season_label": f"{y - 1}/{str(y)[2:]}",
                                                               "decided_on": date(y, 5, 20)} for y in (2062, 2063)])
            c.execute(world.WorldEditionPlace.__table__.insert(), [{"competition_uid": 11, "year": y, "place": p, "team_uid": t}
                                                                   for y in (2062, 2063) for p, t in ((1, 630), (2, 662))])
            c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 2002312311, "name": "Paul Malcolm", "as_of": date(2063, 10, 1), "team_uid": 630}])
            stats = {"substitute_appearances": 0, "rated_appearances": 0, "assists": 0, "player_of_match": 0, "goals_conceded": 0,
                     "clean_sheets": 0, "yellow_cards": 0, "red_cards": 0, "minutes": 0, "rating_total": 0, "as_of": date(2063, 10, 1), "seq": 0}
            c.execute(world.WorldPlayerSeason.__table__.insert(), [{**stats, "person_uid": 2002312311, "team_uid": 630, "season_start": s, "kind": 1,
                                                                    "league_uid": 11, "starts": 40, "goals": 26} for s in range(2052, 2062)])
            c.execute(world.WorldAward.__table__.insert(), [{"fm_uid": 102406, "name": "Golden Ball", "short_name": "Golden Ball"}])
            c.execute(world.WorldAwardPlace.__table__.insert(), [{"award_uid": 102406, "year": 2062, "place": 1, "person_uid": 2002312311, "club_uid": 630,
                                                                  "age": 30, "appearances": 40, "goals": 26, "assists": 5, "rating_total": 0, "votes": 0}])


def test_the_history_pages(site):
    from app.public import history

    _history(site)
    history._cache.clear()
    pages = site.test_client()
    comp = pages.get("/competitions/11/premier-division").data.decode()
    assert "2062/63" in comp and "2061/62" in comp and "Most titles" in comp and "<b>2</b>" in comp
    season = pages.get("/competitions/11/premier-division/2063").data.decode()
    assert "English Premier Division 2062/63" in season and "Champions" in season and "Chelsea" in season
    records = pages.get("/records").data.decode()
    assert "Paul Malcolm" in records and "<b>260</b>" in records
    legends = pages.get("/legends").data.decode()
    assert "Paul Malcolm" in legends
    hub = pages.get("/history").data.decode()
    assert "Paul Malcolm" in hub and "Competitions" in hub
    club = pages.get("/clubs/630/chelsea").data.decode()
    assert "Trophy cabinet" in club and "All-time top scorers" in club and "League finishes" in club
    award = pages.get("/awards/102406/golden-ball").data.decode()
    assert "Golden Ball" in award and "Paul Malcolm" in award
    player = pages.get("/players/2002312311/paul-malcolm").data.decode()
    assert "Golden Ball" in player and "/awards/102406/golden-ball" in player
    assert "Competitions" in pages.get("/competitions").data.decode()
    assert pages.get("/competitions/99999").status_code == 404 and pages.get("/competitions/11/x/1800").status_code == 404


def test_the_uefa_and_international_pages(site):
    """A Champions League league phase in play (no edition recorded yet) and a qualifying group of national teams."""
    from app.models import world

    _league(site)
    day = date(2063, 10, 1)
    with site.app_context():
        with db.engines[world.BIND].begin() as c:
            c.execute(world.WorldCompetition.__table__.insert(), [
                {"fm_uid": 1301394, "name": "UEFA Champions League", "short_name": "Champions League", "abbreviation": "UCL", "scope": "continental", "reputation": 200},
                {"fm_uid": 100109, "name": "European Championship Qualifying", "short_name": "Euro Qualifying", "abbreviation": "ECQ", "scope": "national_teams", "reputation": None}])
            c.execute(world.WorldEdition.__table__.insert(), [{"competition_uid": 1301394, "year": 2063, "season_label": "2062/63",
                                                               "decided_on": date(2063, 5, 30)}])
            c.execute(world.WorldNation.__table__.insert(), [{"fm_uid": n, "name": name, "short_name": name, "abbreviation": name[:3].upper(), "adjective": name}
                                                             for n, name in ((765, "England"), (796, "Spain"), (771, "Germany"))])
            c.execute(world.WorldTeam.__table__.insert(), [{"fm_uid": n, "club_uid": None, "nation_uid": n, "name": name, "age_limit": None, "as_of": day}
                                                           for n, name in ((765, "England"), (796, "Spain"), (771, "Germany"))])
            games = [(date(2063, 10, 1), 630, 662, 2, 0, 1301394, 5), (date(2063, 10, 22), 650, 630, 1, 3, 1301394, 5),
                     (date(2063, 10, 12), 765, 796, 2, 1, 100109, 3), (date(2063, 10, 15), 771, 765, 0, 0, 100109, 3),
                     (date(2063, 11, 15), 796, 771, None, None, 100109, 3)]
            c.execute(world.WorldFixture.__table__.insert(), [{"date": d, "home_uid": h, "away_uid": a, "home_goals": hg, "away_goals": ag,
                                                               "competition_uid": comp, "stage": stage, "as_of": day} for d, h, a, hg, ag, comp, stage in games])
    pages = site.test_client()
    uefa = pages.get("/uefa").data.decode()
    assert "UEFA Champions League" in uefa and "Tables, results" in uefa
    cl = pages.get("/tournaments/1301394/uefa-champions-league").data.decode()
    assert "Chelsea" in cl and "2063/64" in cl and "1-3" in cl  # the season after the last one recorded (2062/63)
    national = pages.get("/international").data.decode()
    assert "European Championship Qualifying" in national and "England" in national
    group = pages.get("/tournaments/100109").data.decode()
    assert "England" in group and "Spain" in group and "Germany" in group and "15 Nov" in group
    assert pages.get("/tournaments/424242").status_code == 404


def test_the_club_page_shows_how_they_play(site):
    from app.models import world

    _league(site)
    day = date(2063, 10, 1)
    with site.app_context():
        engine = db.engines[world.BIND]
        with engine.begin() as c:
            c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 19, "name": "Lionel Messi", "as_of": day}])
            (made,) = c.execute(world.WorldTeamTactic.__table__.insert().values(
                team_uid=630, slot=1, name="GPT 5.6 x", style="Tiki-Taka", mentality="Positive", fluidity="Flexible",
                formation="4-3-3 DM Wide", as_of=day)).inserted_primary_key
            played = c.execute(world.WorldFixture.__table__.select().where(world.WorldFixture.__table__.c.home_goals.is_not(None))).first().id
            (old,) = c.execute(world.WorldTeamTactic.__table__.insert().values(
                team_uid=630, fixture_id=played, played_on=date(2063, 10, 5), name="Gardiola", style="Gegenpress", mentality="Positive",
                formation="4-3-3 DM Wide", as_of=day)).inserted_primary_key
            c.execute(world.WorldTeamTacticSlot.__table__.insert(), [
                {"tactic_id": made, "seq": 0, "position": "GK", "side": None, "duty": "Support", "role_key": "1.10", "role": "Sweeper Keeper", "person_uid": None},
                {"tactic_id": made, "seq": 1, "position": "AM (L)", "side": None, "duty": "Support", "role_key": "3.08", "role": "Inside Forward", "person_uid": None},
                {"tactic_id": made, "seq": 101, "position": "AM (L)", "side": None, "duty": "Attack", "role_key": "6.02", "role": "Inverted Winger", "person_uid": 19},
                {"tactic_id": old, "seq": 0, "position": "GK", "side": None, "duty": "Defend", "role_key": "0.01", "role": "Goalkeeper", "person_uid": None}])
    club = site.test_client().get("/clubs/630/chelsea").data.decode()
    assert "How they play" in club and "4-3-3 DM Wide" in club and "Tiki-Taka" in club and "Sweeper Keeper" in club
    assert "Lionel Messi" in club and "Inverted Winger, Attack" in club  # his personalised role
    assert "Systems over the years" in club and "Gardiola" in club and "Gegenpress" in club and "2063/64" in club


def test_a_preview_and_its_report_point_at_each_other(site):
    from app.models.world_soccer_entity_link import WorldSoccerEntityLink

    with site.app_context():
        preview = WorldSoccerArticle.query.filter_by(headline="AEK v Chelsea: what to expect").one()
        issue = preview.issue
        report = WorldSoccerArticle(headline="As previewed: AEK 0-2 Chelsea", section="Results", lead_key="promised_report:match:2063-10-27:1:630:2063",
                                    prominence="standard", body="Done.", byline="Desk", article_type="report", sort_order=9)
        issue.articles.append(report)
        for a in (preview, report):
            a.entity_links.append(WorldSoccerEntityLink(entity_type="match", entity_id=1, world_ref="match:2063-10-27:1:630", label="AEK v Chelsea"))
        db.session.commit()
        preview_id, report_id = preview.id, report.id
    pages = site.test_client()
    before = pages.get(f"/story/{preview_id}").data.decode()
    assert "The match report:" in before and "As previewed: AEK 0-2 Chelsea" in before
    after = pages.get(f"/story/{report_id}").data.decode()
    assert "What we said before the game:" in after and "AEK v Chelsea: what to expect" in after


def test_a_competition_story_shows_a_pool_picture_before_the_logo(site):
    from app.models.archive_image import ArchiveImageLink
    from app.models.world_soccer_entity_link import WorldSoccerEntityLink
    from app.services import image_library

    with site.app_context():
        image = image_library.register_archive_image("/static/generated/world/photos/competition-1-z.jpg", kind="photograph", source="generated")
        image.links.append(ArchiveImageLink(entity_type="competition", entity_id=1, relation="subject", world_ref="competition:1"))
        europe = WorldSoccerArticle.query.filter_by(headline="English clubs in Europe").one()
        europe.entity_links.append(WorldSoccerEntityLink(entity_type="competition", entity_id=1, world_ref="competition:1", label="Europe"))
        db.session.commit()
    page = site.test_client().get("/section/europe").data.decode()
    assert "/static/generated/world/photos/competition-1-z.jpg" in page


def test_the_player_page_has_the_transfer_talk(site):
    from app.models import world

    _league(site)
    day = date(2063, 11, 24)
    with site.app_context():
        engine = db.engines[world.BIND]
        with engine.begin() as c:
            c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 7, "name": "Júnior", "as_of": day, "team_uid": 630}])
            c.execute(world.WorldTransferInterest.__table__.insert(), [
                {"team_uid": 650, "person_uid": 7, "rumoured": 0, "wage": 40000, "offered_wage": 80000, "as_of": day},
                {"team_uid": 662, "person_uid": 7, "rumoured": 1, "wage": 40000, "offered_wage": 40000, "as_of": day}])
            c.execute(world.WorldTransferOffer.__table__.insert(), [
                {"team_uid": 650, "person_uid": 7, "squad_uid": 650, "fee": 43_000_000, "status": "collapsed", "offered_on": day, "until": day, "completes_on": day, "as_of": day}])
    page = site.test_client().get("/players/7/junior").data.decode()
    assert "Transfer talk" in page and "Wanted by" in page and "Arsenal" in page and "Rumoured" in page and "Liverpool" in page
    assert "bid £43,000,000" in page and "collapsed" in page


def test_the_player_page_lists_his_recent_games_for_each_team(site):
    from app.models import world

    _league(site)
    day = date(2063, 11, 24)
    with site.app_context():
        engine = db.engines[world.BIND]
        with engine.begin() as c:
            c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 7, "name": "Júnior", "as_of": day, "team_uid": 630}])
            c.execute(world.WorldPlayerGame.__table__.insert(), [
                {"person_uid": 7, "team_uid": 630, "played_on": date(2063, 11, 19), "opponent_uid": 650, "status": "played", "minutes": 90, "rating": 7.6,
                 "xg": 0.89, "goals": 1, "assists": 0, "passes": 26, "passes_completed": 22, "as_of": day},
                {"person_uid": 7, "team_uid": 630, "played_on": date(2063, 11, 16), "opponent_uid": 662, "status": "unused", "minutes": None, "rating": None,
                 "xg": None, "goals": None, "assists": None, "passes": None, "passes_completed": None, "as_of": day}])
    page = site.test_client().get("/players/7/junior").data.decode()
    assert "Recent games for" in page and "19 Nov 2063" in page and "7.6" in page and "0.89" in page and "unused substitute" in page


def test_a_match_without_a_report_shows_the_players_we_know_of(site):
    from app.models import world
    from app.services import story_notebooks

    _league(site)
    day = date(2063, 11, 24)
    with site.app_context():
        engine = db.engines[world.BIND]
        with engine.begin() as c:
            played = c.exec_driver_sql("select id from fixtures where date = '2063-10-05'").scalar()
            c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 7, "name": "Júnior", "as_of": day, "team_uid": 630},
                                                             {"fm_uid": 8, "name": "Marc", "as_of": day, "team_uid": 662}])
            empty = {"minutes": None, "minute_off": None, "rating": None, "xg": None, "goals": None, "assists": None, "passes": None, "passes_completed": None,
                     "key_passes": None, "shots": None, "shots_on_target": None, "tackles": None, "tackles_won": None, "headers": None, "headers_won": None,
                     "interceptions": None, "yellow_cards": None}
            c.execute(world.WorldPlayerGame.__table__.insert(), [
                {**empty, "person_uid": 7, "team_uid": 630, "played_on": date(2063, 10, 5), "opponent_uid": 662, "fixture_id": played, "status": "played",
                 "minutes": 90, "rating": 8.1, "xg": 0.89, "goals": 2, "assists": 0, "passes": 26, "passes_completed": 22, "key_passes": 3, "shots": 4,
                 "shots_on_target": 3, "tackles": 1, "tackles_won": 1, "headers": 7, "headers_won": 7, "interceptions": 0, "yellow_cards": 1, "as_of": day},
                {**empty, "person_uid": 8, "team_uid": 662, "played_on": date(2063, 10, 5), "opponent_uid": 630, "fixture_id": played, "status": "unused", "as_of": day}])
        page = site.test_client().get(f"/matches/{played}/chelsea-liverpool").data.decode()
        assert "The players we know of" in page and "Júnior" in page and "8.1" in page and "Marc" in page and "unused substitute" in page
        assert "no detailed report" not in page
        with engine.connect() as c:
            facts = story_notebooks.known_players_facts(c, played)
    assert facts["players"][0] == {"name": "Júnior", "team": "Chelsea", "position": None, "rating": 8.1, "minutes": 90, "replaced_at": None, "came_on": None,
                                   "did": "2 goals, 3 key passes, 4 shots, 3 on target, xG 0.89, a yellow card"}
    assert facts["players"][1] == {"name": "Marc", "team": "Liverpool", "note": "unused substitute"}


def test_a_world_cup_in_play_puts_the_site_in_tournament_mode(site, monkeypatch):
    from app.models import world
    from app.public import football

    _league(site)
    day = date(2063, 11, 24)
    with site.app_context():
        engine = db.engines[world.BIND]
        with engine.begin() as c:
            c.execute(world.WorldCompetition.__table__.insert(), [{"fm_uid": 1301385, "name": "World Cup", "short_name": "World Cup", "abbreviation": "WC",
                                                                    "scope": "national_teams", "level": None, "reputation": 200}])
            c.execute(world.WorldNation.__table__.insert(), [{"fm_uid": n, "name": name, "short_name": name, "abbreviation": name[:3].upper(), "adjective": name, "as_of": day}
                                                             for n, name in ((800, "Spain"), (801, "England"), (802, "Brazil"), (803, "Japan"))])
            c.execute(world.WorldTeam.__table__.insert(), [{"fm_uid": 900 + n, "nation_uid": 800 + n, "name": name, "age_limit": None, "as_of": day}
                                                           for n, name in enumerate(("Spain", "England", "Brazil", "Japan"))])
            games = [(date(2064, 6, 12), 900, 901, 1, 1, 1), (date(2064, 6, 12), 902, 903, 3, 0, 1), (date(2064, 6, 16), 900, 902, None, None, 1), (date(2064, 7, 10), 901, 903, None, None, 2)]
            c.execute(world.WorldFixture.__table__.insert(), [{"date": d, "home_uid": h, "away_uid": a, "competition_uid": 1301385, "stage": st,
                                                               "home_goals": hg, "away_goals": ag, "as_of": day} for d, h, a, hg, ag, st in games])
            c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 7, "name": "Júnior", "as_of": day, "team_uid": 630}])
            first = c.exec_driver_sql("select id from fixtures where date = '2064-06-12' and home_uid = 900").scalar()
            c.execute(world.WorldPlayerGame.__table__.insert(), [{"person_uid": 7, "team_uid": 900, "played_on": date(2064, 6, 12), "opponent_uid": 901, "fixture_id": first,
                                                                  "status": "played", "minutes": 90, "rating": 7.9, "xg": 0.4, "goals": 1, "assists": 0, "as_of": day}])
    monkeypatch.setattr(football, "today_of", lambda c: date(2064, 6, 13))
    with site.app_context():
        with db.engines[world.BIND].connect() as c:
            t = football.major_tournament(c)
            assert (t["name"], t["label"], t["phase"], t["day"], t["played"], t["games"]) == ("World Cup", "2064", "in play", 2, 2, 4)
    pages = site.test_client()
    front = pages.get("/").data.decode()
    assert 'class="hub"' in front and "World Cup 2064" in front and "Yesterday" in front and "Our players there" in front and "Júnior" in front
    page = pages.get("/tournaments/1301385/world-cup").data.decode()
    assert "Day 2" in page and "Our players at the tournament" in page and "Spain" in page
    monkeypatch.setattr(football, "today_of", lambda c: date(2064, 6, 1))  # eleven days before: not yet
    with site.app_context():
        with db.engines[world.BIND].connect() as c:
            assert football.major_tournament(c) is None


def test_the_public_site_shows_its_holding_page_when_taken_offline(site):
    from app.services import public_site

    _league(site)
    pages = site.test_client()
    assert pages.get("/").status_code == 200
    with site.app_context():
        public_site.set_offline(True)
        assert public_site.offline()
    answer = pages.get("/tables/11")
    assert answer.status_code == 503 and b"Back soon" in answer.data
    assert pages.get("/static/css/pages.css").status_code == 200  # the styles still serve
    with site.app_context():
        public_site.set_offline(False)
    assert pages.get("/").status_code == 200
    log = ('2026-10-25T08:34:04Z INF Updated to new configuration config="{\\"ingress\\":[{\\"hostname\\":\\"worldsport.uk\\", \\"service\\":\\"http://localhost:8080\\"}, '
           '{\\"hostname\\":\\"www.worldsport.uk\\", \\"service\\":\\"http://localhost:8080\\"}, {\\"service\\":\\"http_status:404\\"}]}"\n'
           '2026-10-25T08:40:00Z ERR Failed to dial\n')
    assert public_site.hostnames_from_log(log) == ["worldsport.uk", "www.worldsport.uk"]
    assert public_site.errors_from_log(log) == ["Failed to dial"]
    status = public_site.TunnelStatus(installed=True, running=True, hostnames=["www.worldsport.uk", "worldsport.uk"], reachable=True, http_status=200)
    assert status.address == "https://worldsport.uk" and status.word == "online"


def test_the_paper_is_exported_as_static_pages_and_published_to_cloudflare(site, tmp_path, monkeypatch, a_logo):
    from app.services import site_publish

    _league(site)
    export = site_publish.export_site(site, tmp_path / "out", (630,))
    out = tmp_path / "out"
    assert (out / "index.html").exists() and (out / "tables" / "11" / "index.html").exists() and (out / "404.html").exists()
    assert (out / "clubs" / "630" / "chelsea" / "index.html").exists()  # the followed club
    assert export.pages > 5 and export.types["/index.html"] == "text/html" and any(k.startswith("/logos/") for k in export.types)
    # the crawl's rules
    assert site_publish.follow_link("club", "player", from_followed=True) and not site_publish.follow_link("club", "player", from_followed=False)
    assert not site_publish.follow_link("player", "match", False) and site_publish.follow_link("player", "club", False)
    assert site_publish.follow_link("story", "player", False) and site_publish.target_path("/clubs/630/chelsea") == "clubs/630/chelsea/index.html"
    assert not site_publish.follow_link("awards", "player", False) and not site_publish.follow_link("legends", "player", False)
    assert site_publish.follow_link("tournaments", "player", False) and not site_publish.follow_link("awards", "club", False)
    assert not site_publish.follow_link("match", "club", False) and site_publish.follow_link("tables", "club", False) and site_publish.follow_link("player", "club", False)
    assert not site_publish.follow_link("competitions", "club", False) and not site_publish.follow_link("competitions", "competitions", False)
    assert site_publish.follow_link("competitions", "tables", False) and site_publish.follow_link("story", "competitions", False)
    assert site_publish.kind_of("/competitions/11/english-premier-division/2063") == "edition" and site_publish.kind_of("/competitions/11/x") == "competitions"
    assert site_publish.follow_link("story", "edition", False) and not site_publish.follow_link("club", "edition", True)
    assert not site_publish.follow_link("player", "edition", False) and not site_publish.follow_link("match", "edition", False)
    assert site_publish.file_hash(b"<html>", ".html") == site_publish.file_hash(b"<html>", ".html") and len(site_publish.file_hash(b"x", ".css")) == 32
    assert site_publish.script_name("Sam Archer - Chelsea v2") == "fm-archive-sam-archer-chelsea-v2"
    assert site_publish.script_name("Sam Archer - Chelsea v2", "worldsport.uk") == "site-worldsport-uk"

    calls = []

    class FakeCloudflare(site_publish.Cloudflare):
        def call(self, method, path, *, bearer=None, **kwargs):
            calls.append((method, path.split("?")[0], bearer is not None))
            if path.endswith("/accounts?per_page=5"):
                return [{"id": "acc1"}]
            if path.endswith("assets-upload-session"):
                manifest = kwargs["json"]["manifest"]
                hashes = [entry["hash"] for entry in manifest.values()]
                return {"jwt": "upload-jwt", "buckets": [hashes[:3], hashes[3:]]}
            if "/workers/assets/upload" in path:
                assert bearer == "upload-jwt" and all(len(k) == 32 for k in kwargs["files"])
                return {"jwt": "completion-jwt"}
            if path.endswith("/subdomain") and method == "GET":
                return {"subdomain": "sam"}
            if path.startswith("/zones?name="):
                return [{"id": "zone1"}]
            return {}

    result = site_publish.publish(site, tmp_path / "out", "Sam Archer - Chelsea v2", (630,), "token", "worldsport.uk",
                                  client=FakeCloudflare("token"))
    assert result["address"] == "https://worldsport.uk" and result["files"] == export.pages + export.assets + 1 and result["pages"] == export.pages
    paths = [p for _, p, _ in calls]
    assert paths[:2] == ["/accounts", "/accounts/acc1/workers/scripts/site-worldsport-uk/assets-upload-session"]
    assert paths.count("/accounts/acc1/workers/assets/upload") == 2
    assert "/accounts/acc1/workers/scripts/site-worldsport-uk" in paths and "/accounts/acc1/workers/domains" in paths
    assert ("PUT", "/accounts/acc1/workers/scripts/site-worldsport-uk", False) in calls


def test_a_publish_renders_again_only_when_the_archive_changed(site, tmp_path, a_logo):
    from datetime import datetime, timedelta

    from app.services import site_publish

    _league(site)
    out = tmp_path / "out"
    site_publish.export_site(site, out, (630,))
    with site.app_context():
        assert not site_publish.export_is_fresh(out)  # never stamped: render
        site_publish.set_setting(site_publish.EXPORTED_SETTING, site_publish.utc_now().isoformat(timespec="seconds"))
        assert not site_publish.export_is_fresh(out)  # stamped, but by no known app: render
        site_publish.set_setting(site_publish.EXPORTED_VERSION_SETTING, site_publish.app_version())
        assert site_publish.export_is_fresh(out)  # stamped after every import, by this app: fresh
        site_publish.set_setting(site_publish.EXPORTED_VERSION_SETTING, "an older app")
        assert not site_publish.export_is_fresh(out)  # a new app may draw the pages differently: render
        assert site_publish.archive_changed_since(site_publish.utc_now() - timedelta(days=4000))  # the fixtures' import lies after that
        assert not site_publish.archive_changed_since(site_publish.utc_now() + timedelta(minutes=1))
        site_publish.set_setting(site_publish.EXPORTED_VERSION_SETTING, site_publish.app_version())
        site_publish.set_setting(site_publish.EXPORTED_SETTING, (site_publish.utc_now() + timedelta(seconds=1)).isoformat(timespec="seconds"))
        assert site_publish.export_is_fresh(out)
        story = WorldSoccerArticle.query.first()
        story.body += " Corrected."  # a story corrected by hand, a little later: the export is stale
        story.updated_at = site_publish.utc_now() + timedelta(seconds=2)
        db.session.commit()
        assert not site_publish.export_is_fresh(out)
    kept = site_publish.export_from_folder(out)
    assert kept.pages > 5 and kept.assets >= 1 and "/404.html" in kept.types


def test_the_away_days_column_and_the_interviews_have_a_features_section(site):
    """The column's paper section "Away days" fell to the "news" bucket, which has no front row and no page (25 Oct 2026)."""
    with site.app_context():
        made = WorldSoccerIssue(publication_date=date(2063, 10, 28), knowledge_cutoff=date(2063, 10, 28), status="published", edition_name="World Soccer")
        made.articles.append(WorldSoccerArticle(headline="Away Days: a wet night in Molde", section="Away days", lead_key="travel_log:77", prominence="standard",
                                                body="One.\n\nTwo.", byline="Teodor Albescu", standfirst="Trip one.", article_type="column", sort_order=0))
        made.articles.append(WorldSoccerArticle(headline="Morel: I never wanted to be a tourist", section="Interviews", lead_key="interview:5", prominence="brief",
                                                body="One.\n\nTwo.", byline="Desk", standfirst="A talk.", article_type="report", sort_order=1))
        db.session.add(made)
        db.session.commit()
    pages = site.test_client()
    features = pages.get("/section/features").data.decode()
    assert "a wet night in Molde" in features and "never wanted to be a tourist" in features
    front = pages.get("/").data.decode()
    assert "/section/features" in front and "a wet night in Molde" in front


def test_every_paper_section_has_a_site_section():
    """Academy, Rising, Fallen, Great finals, International, On this day, Tournament and the two features fell to the "news"
    bucket, which has no front row and no page (25 Oct 2026)."""
    from app.public import SECTIONS
    from app.services import editions

    mapped = set().union(*(names for _, names in SECTIONS.values()))
    assert set(editions.SECTIONS.values()) - mapped == set()


def test_the_navigation_names_international_once(site):
    """The International section row doubled the hub's link in the menu (25 Oct 2026)."""
    front = site.test_client().get("/").data.decode()
    nav = front[front.index("<nav"):front.index("</nav>")]
    assert nav.count(">International<") == 1 and 'href="/international"' in nav and 'href="/section/features"' in nav


def test_the_scores_step_through_the_days(site, monkeypatch):
    """ESPN-style (26 Oct 2026): the front box shows the latest match day only, with the day before to step back to, and every
    match day has its own page with the days around it."""
    from app.public import football

    _league(site)
    monkeypatch.setattr(football, "today_of", lambda c: date(2063, 10, 15))
    pages = site.test_client()
    front = pages.get("/").data.decode()
    assert "Friday 12 October" in front and "1-1" in front and "3-0" not in front and "/scores/2063-10-05" in front
    day = pages.get("/scores/2063-10-05").data.decode()
    assert "3-0" in day and "1-1" not in day and "/scores/2063-10-12" in day and "Friday 5 October 2063" in day
    assert pages.get("/scores/2063-10-06").status_code == 404 and pages.get("/scores/nonsense").status_code == 404


def test_the_about_page_says_what_the_site_is(site):
    pages = site.test_client()
    about = pages.get("/about").data.decode()
    assert "About World Sport" in about and "Football Manager 2024" in about and "The stories are checked" in about
    assert "stories printed" not in about or "<b>4</b>" in about  # the world's numbers only when a world was read
    assert '/about"' in pages.get("/").data.decode()  # the footer links it, so the export prints it
