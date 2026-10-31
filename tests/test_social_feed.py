"""The social feed: the clubs' posts from the save's events, once each, and the switch in Settings."""

from __future__ import annotations

from datetime import date

from sqlalchemy import text

from app.extensions import db
from app.models import AppSetting
from app.models import world
from app.models.social import SocialAccount, SocialPost
from app.services import social_feed
from tests.test_world_pages import ARSENAL, CHELSEA, LIVERPOOL, MYERS, _fixture, app, pages, small_world  # noqa: F401

UNTIL = date(2063, 5, 31)


def _world(engine):
    with engine.begin() as c:
        c.execute(text("update teams set reputation = 9000 where fm_uid = :c"), {"c": LIVERPOOL})
        c.execute(world.WorldFixture.__table__.insert(), [_fixture("2063-05-28", CHELSEA, ARSENAL, (9, 0), 2000229282, 1)])  # a pre-season tournament
        c.execute(world.WorldTransfer.__table__.insert(), [
            {"person_uid": MYERS, "date": date(2063, 5, 25), "kind": 1, "from_uid": ARSENAL, "to_uid": CHELSEA, "squad_uid": None,
             "fee": 30_000_000, "fee_with_add_ons": 30_000_000}])
        c.execute(world.WorldManagerSpell.__table__.insert(), [
            {"team_uid": CHELSEA, "seq": 0, "person_uid": MYERS, "started": date(2060, 1, 1), "ended": date(2063, 5, 22), "won": 50,
             "drawn": 10, "lost": 10, "reason": 2, "league_wins": 1, "cup_wins": 0, "as_of": UNTIL}])
    db.session.add(AppSetting(key=social_feed.UNTIL_KEY, value="2063-05-15"))
    db.session.commit()


def _texts(club: int) -> list[str]:
    account = SocialAccount.query.filter_by(kind="club", club_uid=club).one()
    return [p.text for p in SocialPost.query.filter_by(account_id=account.id).order_by(SocialPost.posted_on)]


def test_the_clubs_post_their_results_titles_transfers_and_managers_once(app, small_world):
    _world(small_world)
    made = social_feed.make_posts(UNTIL)
    chelsea = _texts(CHELSEA)
    assert any(t.startswith("FT | Tottenham 0–2 Chelsea") for t in chelsea)
    assert not any("9–0" in t for t in chelsea)  # a competition the save does not list is a friendly
    final = next(t for t in chelsea if t.startswith("FT | Liverpool 2–1 Chelsea (AET)"))
    assert "⚽ Myers 19'" in final
    assert any("English Premier Division winners 2062/63" in t for t in chelsea)
    assert any("Ken Myers" in t and "Arsenal" in t and "✍️" in t for t in chelsea)  # the signing
    sacking = SocialPost.query.filter_by(kind="manager_out").one()
    assert "Ken Myers" in sacking.text and sacking.account.club_uid == CHELSEA
    assert any("Ken Myers has joined Chelsea" in t or "move to Chelsea" in t or "heads to Chelsea" in t
               for t in _texts(ARSENAL))  # a big fee brings in the selling club
    assert any("English FA Cup winners 2062/63" in t for t in _texts(LIVERPOOL))  # a well-known club
    assert SocialAccount.query.filter_by(kind="club", club_uid=CHELSEA).one().handle == "ChelseaFC"
    assert any(t.endswith("#Chelsea #PremierDivision") for t in chelsea)  # hashtags
    myers = SocialAccount.query.filter_by(kind="player", person_uid=MYERS).one()
    assert myers.handle == "KenMyers" and myers.club_uid == CHELSEA
    assert SocialPost.query.filter_by(account_id=myers.id, kind="player_signing").one().text.endswith("#Chelsea")
    scoop = SocialPost.query.filter_by(kind="scoop").one()
    assert scoop.account.name == "The Transfer Wire" and "£30.0m" in scoop.text and "#TransferNews" in scoop.text
    assert made == SocialPost.query.count()
    assert social_feed.make_posts(UNTIL) == 0  # nothing new since
    db.session.get(AppSetting, AppSetting.query.filter_by(key=social_feed.UNTIL_KEY).one().id).value = "2063-05-15"
    assert social_feed.make_posts(UNTIL) == 0  # the same days again: each event is posted once


def test_the_feed_page_links_its_posts_and_can_be_turned_off(app, small_world, pages):
    _world(small_world)
    social_feed.make_posts(UNTIL)
    page = pages.get("/feed/").data.decode()
    assert "@ChelseaFC" in page and f'href="/people/{MYERS}"' in page and "The match ›" in page
    assert f'href="/clubs/{LIVERPOOL}"' in page
    assert ">Feed</a>" in pages.get("/today").data.decode()
    pages.post("/settings/social", data={"social": "off"})
    assert not social_feed.enabled()
    assert pages.get("/feed/").status_code == 404 and ">Feed</a>" not in pages.get("/today").data.decode()
    assert social_feed.make_posts(date(2063, 6, 30)) == 0  # nothing posted while off
    pages.post("/settings/social", data={"social": "on"})
    assert pages.get("/feed/").status_code == 200


def test_handles_are_plain_letters():
    assert social_feed.handle_for("Chelsea") == "ChelseaFC"
    assert social_feed.handle_for("Borussia Mönchengladbach") == "BorussiaMonchengladbachFC"
    assert social_feed.handle_for("Manchester United") == "ManchesterUnited"
    assert social_feed.handle_for("Real Madrid FC") == "RealMadridFC"


def _fans_world(engine, monkeypatch, second_try_fixed: bool):
    """The feed of _world, Arsenal a rival of Chelsea, and a fake model that knows the cast from the notebook."""
    import json as json_module

    from app.services import social_fans

    _world(engine)
    with engine.begin() as c:
        c.execute(world.WorldClubRelation.__table__.insert(), [
            {"club_uid": CHELSEA, "relation": "rival", "subject_uid": ARSENAL, "reason": 1, "level": 80, "permanent": 0,
             "names_stadium": 0, "as_of": UNTIL}])
    social_feed.make_posts(UNTIL)
    calls = []

    def fake(model, key, messages, temperature=0.5, retry=True):
        notebook = json_module.loads(messages[1]["content"])
        calls.append(notebook)
        by_club = {}
        for fan in notebook["cast"]:
            if "supports" in fan:  # a player in the thread plays for his club
                by_club.setdefault(fan["supports"], []).append(fan["handle"])
        home = next(iter(notebook["clubs"]))
        mine, theirs = by_club[home][0], next((h[0] for club, h in by_club.items() if club != home), by_club[home][1])
        wrong = len(calls) % 2 == 1 or not second_try_fixed
        return {"replies": [
            {"handle": mine, "reply_to": None, "text": "Get in! What a day to be one of us 🔥"},
            {"handle": theirs, "reply_to": mine, "text": "We'll beat you 7-6 next time" if wrong else "Enjoy it while it lasts 😂"},
            {"handle": "@nobody", "reply_to": None, "text": "Not in the cast"},
        ]}

    monkeypatch.setattr(social_fans, "request_json", fake)
    monkeypatch.setattr(social_fans, "resolve_ai_api_key", lambda *args, **kwargs: "test-key")
    return social_fans, calls


def test_fans_write_checked_threads_under_the_best_club_posts(app, small_world, pages, monkeypatch):
    social_fans, calls = _fans_world(small_world, monkeypatch, second_try_fixed=True)
    post = next(p for p in social_fans.thread_candidates(100) if p.text.startswith("FT | Tottenham 0–2 Chelsea"))
    assert social_fans.write_thread(post) == 2  # the fixed second draft; the reply from outside the cast is dropped
    notebook = calls[0]
    assert notebook["post"]["text"].startswith("FT | Tottenham 0–2 Chelsea")
    assert any("Arsenal" in fan["supports"] for fan in notebook["cast"])  # a rival's fan winds them up
    assert any("2-0" in result for result in notebook["clubs"]["Chelsea"]["recent_results"])
    assert notebook["clubs"]["Chelsea"]["last_twelve_months"] == ["won English Premier Division 2062/63"]
    recent = notebook["clubs"]["Chelsea"]["recent_results"]
    assert recent[0] == "20 May 2063, English Premier Division, the day of the post: Tottenham 0-2 Chelsea"
    assert "21 April 2063, English FA Cup, 4 weeks before the post: Chelsea 1-0 Arsenal" in recent
    replies = SocialPost.query.filter_by(kind="reply").order_by(SocialPost.id).all()
    assert replies[0].reply_to_id == post.id and replies[1].reply_to_id == replies[0].id  # an answer to a fan
    assert {r.account.kind for r in replies} == {"fan"}
    fans = SocialAccount.query.filter_by(kind="fan", club_uid=CHELSEA).all()
    assert len(fans) == social_fans.FANS_PER_CLUB and len({f.persona["temper"] for f in fans}) == len(fans)
    assert post not in social_fans.thread_candidates()  # one thread a post
    page = pages.get(f"/feed/posts/{post.id}").data.decode()
    assert "Get in! What a day" in page and "Enjoy it while it lasts" in page and " fan · " in page
    assert "Get in! What a day" in pages.get("/feed/").data.decode()  # the first replies show under the post
    assert pages.get(f"/feed/?account={replies[0].account_id}").status_code == 200  # the fan's own page
    with small_world.connect() as connection:
        assert social_fans.fans_of(connection, CHELSEA) == fans  # kept, not made again


def test_a_reply_that_stays_wrong_is_dropped(app, small_world, monkeypatch):
    social_fans, calls = _fans_world(small_world, monkeypatch, second_try_fixed=False)
    post = next(p for p in social_fans.thread_candidates(100) if p.text.startswith("FT | Tottenham 0–2 Chelsea"))
    assert social_fans.write_thread(post) == 1 and len(calls) == 2
    assert "7-6" not in " ".join(p.text for p in SocialPost.query.filter_by(kind="reply"))


def test_the_fans_write_by_themselves_with_no_button(app, small_world, pages):
    page = pages.get("/feed/").data.decode()
    assert "Write the fans' reactions" not in page and "answer the latest posts by themselves" in page
    assert pages.post("/feed/fans").status_code in (404, 405)


def test_the_slow_writer_writes_one_thread_at_a_time_with_a_local_model(app, small_world, pages, monkeypatch):
    from app.services import ai_settings, save_jobs

    social_fans, calls = _fans_world(small_world, monkeypatch, second_try_fixed=True)
    models = []
    real_write = social_fans.write_thread
    monkeypatch.setattr(social_fans, "write_thread",
                        lambda post, model=None, drip=False: models.append(model) or real_write(post, model, drip))
    monkeypatch.setattr(save_jobs, "busy", lambda app=None: False)
    monkeypatch.setattr(ai_settings, "ollama_models", lambda: [])
    assert not social_fans.trickle_once()  # no Ollama: nothing
    monkeypatch.setattr(ai_settings, "ollama_models", lambda: [{"name": "qwen3:14b", "size_gb": 9.3, "parameters": "14B"},
                                                                {"name": "gemma4:12b", "size_gb": 8.0, "parameters": "12B"}])
    assert social_fans.slow_model() == "ollama:gemma4:12b"  # DeepSeek is chosen in Settings: a local model instead
    before = pages.get("/feed/status").get_json()["replies"]
    assert social_fans.trickle_once() and models == ["ollama:gemma4:12b"]
    waiting = social_fans.pending_replies()
    assert waiting > 0 and pages.get("/feed/status").get_json()["replies"] == before  # written, not shown yet
    times = sorted(p.shown_at for p in SocialPost.query.filter(SocialPost.shown_at.isnot(None)))
    assert all(10 <= (b - a).total_seconds() <= 40 for a, b in zip(times, times[1:]))  # one by one, 10 to 40 seconds apart
    monkeypatch.setattr(social_fans, "SLOW_BUFFER", 1)
    assert not social_fans.trickle_once()  # enough is waiting: no writing ahead
    monkeypatch.setattr(social_fans, "SLOW_BUFFER", 12)
    from datetime import datetime, timedelta

    first = SocialPost.query.filter(SocialPost.shown_at.isnot(None)).order_by(SocialPost.shown_at).first()
    first.shown_at = datetime.now() - timedelta(days=1)  # its time has come
    db.session.commit()
    assert pages.get("/feed/status").get_json()["replies"] == before + 1 and social_fans.slow_state()["done"] == 1
    assert "New replies from the fans" in pages.get("/feed/").data.decode()
    SocialPost.query.filter(SocialPost.shown_at.isnot(None)).update({"shown_at": None})
    db.session.commit()
    monkeypatch.setattr(social_fans, "SLOW_LIMIT", 1)
    assert not social_fans.trickle_once()  # the limit is reached
    social_fans.write_thread(social_fans.thread_candidates(1)[0])  # the button's threads do not count
    assert social_fans.slow_state()["done"] == 1
    db.session.add(SocialPost(account_id=SocialAccount.query.filter_by(kind="club").first().id, posted_on=date(2063, 6, 20),
                              kind="result", event_key="result:new", text="FT | A new import", likes=10, reposts=1))
    db.session.commit()
    assert social_fans.slow_state()["done"] == 0  # a newer post: the count starts again
    monkeypatch.setattr(social_fans, "SLOW_LIMIT", 24)
    pages.post("/settings/social", data={"slow": "off"})
    assert not social_fans.slow_enabled() and not social_fans.trickle_once()
    pages.post("/settings/social", data={"slow": "on"})
    monkeypatch.setattr(save_jobs, "busy", lambda app=None: True)
    assert not social_fans.trickle_once()  # never while an import or another job runs


def test_hashtags_trending_and_the_social_tabs(app, small_world, pages):
    _world(small_world)
    social_feed.make_posts(UNTIL)
    feed = pages.get("/feed/").data.decode()
    assert "<title>Feed · FM World Archive</title>" in feed  # the page's script stays out of its title
    assert 'class="hashtag" href="/feed/?tag=Chelsea"' in feed and "Trending" in feed and "#PremierDivision" in feed
    tagged = pages.get("/feed/?tag=PremierDivision").data.decode()
    assert "<h1>#PremierDivision</h1>" in tagged
    assert "Tottenham</a> 0–2 Chelsea" in tagged and "joins from Arsenal" not in tagged and "DONE DEAL" not in tagged
    club = pages.get(f"/clubs/{CHELSEA}").data.decode()
    assert 'data-tab="social"' in club and "The club on the Feed" in club and "Tottenham</a> 0–2 Chelsea" in club
    person = pages.get(f"/people/{MYERS}").data.decode()
    assert 'data-tab="social"' in person and "@KenMyers on the Feed" in person and "Ken Myers" in person
    social_feed.set_enabled(False)
    db.session.commit()
    assert 'data-tab="social"' not in pages.get(f"/clubs/{CHELSEA}").data.decode()


def test_the_writers_post_their_lead_stories(app, small_world, pages):
    from app.models import Outlet, WorldSoccerArticle, WorldSoccerIssue, Writer
    from app.services.outlets import ensure_outlets

    ensure_outlets()
    outlet = Outlet.query.filter_by(slug="transfer-wire").one()
    writer = Writer(outlet=outlet, name="Dana Reyes", persona="A transfer reporter.", style="Short.")
    issue = WorldSoccerIssue(outlet_id=outlet.id, edition_name=outlet.name, publication_date=UNTIL, knowledge_cutoff=UNTIL,
                             status="published")
    db.session.add_all([writer, issue])
    db.session.flush()
    db.session.add(WorldSoccerArticle(issue=issue, headline="Myers moves", body="Text.", byline="Dana Reyes", sort_order=1,
                                      writer_id=writer.id))
    db.session.commit()
    assert social_feed.post_articles([issue.id]) == 1 and social_feed.post_articles([issue.id]) == 0
    post = SocialPost.query.filter_by(kind="article").one()
    assert post.account.name == "Dana Reyes" and "Myers moves" in post.text
    page = pages.get("/feed/").data.decode()
    assert "Read the article ›" in page and f"/news/transfer-wire/{issue.id}#article-" in page and "The Transfer Wire" in page


def test_a_player_with_an_account_joins_the_thread(app, small_world, monkeypatch):
    social_fans, calls = _fans_world(small_world, monkeypatch, second_try_fixed=True)
    post = SocialPost.query.filter_by(kind="signing").one()
    with small_world.connect() as connection:
        notebook, cast = social_fans.thread_notebook(connection, post)
    player = next(c for c in notebook["cast"] if c.get("plays_for"))
    assert player["handle"] == "KenMyers" and "media-trained" in player["temper"]
    assert notebook["hashtags"][0] == "#Chelsea"


def test_injuries_show_on_the_person_page_and_the_clubs_post_them(app, small_world, pages):
    _world(small_world)
    with small_world.begin() as c:
        c.execute(text("update people set team_uid = :c where fm_uid = :p"), {"c": CHELSEA, "p": MYERS})
        base = {"person_uid": MYERS, "severity": "Major", "cause": "Sprinting", "treatment": "Specialist", "as_of": UNTIL}
        c.execute(world.WorldInjury.__table__.insert(), [
            {**base, "seq": 0, "injury": "twisted ankle", "injured_on": date(2062, 10, 1), "days": 12, "severity": "Moderate",
             "place": "In match", "team_uid": CHELSEA, "current": 0},
            {**base, "seq": 1, "injury": "torn hamstring", "injured_on": date(2063, 5, 25), "days": 61, "place": "In match",
             "team_uid": CHELSEA, "current": 1}])
    page = pages.get(f"/people/{MYERS}").data.decode()
    assert 'data-tab="injuries"' in page and "Injured: Torn Hamstring" in page and "back about 25 July" in page
    section = page[page.index('id="injuries"'):]
    assert "Twisted Ankle" in section
    assert "12 days" in section
    assert "9 weeks expected" in section
    assert "1 past injury" in section
    social_feed.make_posts(UNTIL)
    update = SocialPost.query.filter_by(kind="injury").one()
    assert update.account.handle == "ChelseaFC" and "torn hamstring" in update.text and "9 weeks" in update.text
    assert SocialPost.query.filter_by(kind="player_injury").one().account.handle == "KenMyers"  # out for weeks: he posts too


def test_a_fans_temper_bends_the_predicted_score():
    import random

    from app.services.social_predictions import predict

    def mean_margin(temper):
        rng = random.Random(1)
        scores = [predict(rng, temper, 0.0) for _ in range(400)]
        return sum(ours - theirs for ours, theirs in scores) / len(scores)

    assert mean_margin("optimist") > mean_margin("nerd") > mean_margin("doomer")
    assert mean_margin("young") > 2  # absurd


def test_matchday_predictions_are_checked_against_the_result(app, small_world, pages, monkeypatch):
    from app.services import social_fans, social_predictions

    with small_world.begin() as c:  # Chelsea v Arsenal on 12 Aug 2063, a derby
        c.execute(world.WorldClubRelation.__table__.insert(), [
            {"club_uid": CHELSEA, "relation": "rival", "subject_uid": ARSENAL, "reason": 1, "level": 80, "permanent": 0,
             "names_stadium": 0, "as_of": UNTIL}])
    db.session.add(AppSetting(key=social_feed.UNTIL_KEY, value="2063-08-05"))
    db.session.commit()
    social_feed.make_posts(date(2063, 8, 6))
    matchday = SocialPost.query.filter_by(kind="matchday").one()
    assert matchday.text.startswith("🔥 DERBY DAY | Chelsea v Arsenal") and matchday.heat >= 60
    predictions = SocialPost.query.filter_by(kind="prediction").all()
    assert len(predictions) == 2 * social_predictions.PREDICTORS and all(p.reply_to_id == matchday.id for p in predictions)
    assert {p.account.club_uid for p in predictions} == {CHELSEA, ARSENAL}
    chelsea_fan = next(p for p in predictions if p.account.club_uid == CHELSEA)
    assert f"Chelsea {chelsea_fan.prediction['home']}-{chelsea_fan.prediction['away']} Arsenal" in chelsea_fan.text.title() or \
        f"CHELSEA {chelsea_fan.prediction['home']}-{chelsea_fan.prediction['away']} ARSENAL" in chelsea_fan.text
    with small_world.begin() as c:  # the result: exactly what that fan said
        c.execute(text("update fixtures set home_goals = :h, away_goals = :a where date = '2063-08-12'"),
                  {"h": chelsea_fan.prediction["home"], "a": chelsea_fan.prediction["away"]})
    social_feed.make_posts(date(2063, 8, 13))
    result = SocialPost.query.filter_by(kind="result").filter(SocialPost.event_key.like("result:2063-08-12:%")).first()
    assert result.heat >= 45  # a derby
    called = SocialPost.query.filter_by(kind="called_it", account_id=chelsea_fan.account_id).one()
    assert called.reply_to_id == result.id and "🔮" in called.text
    with small_world.connect() as connection:
        notebook, cast = social_fans.thread_notebook(connection, result)
    assert {"handle": chelsea_fan.account.handle, "verdict": "exact score"}.items() <= next(
        p for p in notebook["predictions"] if p["handle"] == chelsea_fan.account.handle).items()
    assert chelsea_fan.account in cast  # the predictor is in the thread to gloat
    assert "🔥 Hot" in pages.get("/feed/").data.decode() and "🔮 Prediction" in pages.get(f"/feed/posts/{matchday.id}").data.decode()


def test_a_fans_handle_names_the_club_not_a_fragment():
    from app.services.social_fans import _club_word

    assert [_club_word(n) for n in ("Chelsea", "Man City", "Real Madrid", "Aston Villa", "Boca", "Athletico Madrid")] == [
        "Chelsea", "ManCity", "RealMadrid", "AstonVilla", "Boca", "Athletico"]


def test_matchday_posts_come_even_when_no_new_days_have_passed(app, small_world):
    db.session.add(AppSetting(key=social_feed.UNTIL_KEY, value="2063-08-06"))  # the feed is up to date
    db.session.commit()
    assert social_feed.make_posts(date(2063, 8, 6)) > 0
    assert SocialPost.query.filter_by(kind="matchday").count() == 1  # Chelsea v Arsenal, 12 August
    assert social_feed.make_posts(date(2063, 8, 6)) == 0  # once only


def test_a_legend_sold_to_a_rival_sets_the_timeline_on_fire(app, small_world, monkeypatch):
    social_fans, _ = _fans_world(small_world, monkeypatch, second_try_fixed=True)  # Arsenal a rival of Chelsea
    SocialPost.query.delete()
    db.session.get(AppSetting, AppSetting.query.filter_by(key=social_feed.UNTIL_KEY).one().id).value = "2063-05-15"
    db.session.commit()
    with small_world.begin() as c:  # Ken Myers: an Arsenal Legend, and Chelsea's fans cannot stand him
        c.execute(world.WorldClubRelation.__table__.insert(), [
            {"club_uid": ARSENAL, "relation": "favourite", "subject_uid": MYERS, "reason": 1, "level": 100, "permanent": 1,
             "names_stadium": 0, "as_of": UNTIL},
            {"club_uid": CHELSEA, "relation": "disliked", "subject_uid": MYERS, "reason": 1, "level": 60, "permanent": 0,
             "names_stadium": 0, "as_of": UNTIL}])
    social_feed.make_posts(UNTIL)
    sale = SocialPost.query.filter_by(kind="sale").one()
    assert sale.account.club_uid == ARSENAL and sale.heat == 100 and ("Forever one of us" in sale.text or "always be one of us" in sale.text)
    signing = SocialPost.query.filter_by(kind="signing").one()
    assert signing.heat >= 90  # a disliked man arriving from the enemy
    with small_world.connect() as connection:
        notebook, _ = social_fans.thread_notebook(connection, signing)
    assert {"Legend at Arsenal", "Disliked at Chelsea"} <= set(notebook["people"][0]["standing_with_clubs"])


def test_what_happened_in_the_match_heats_the_result_and_reaches_the_fans(app, small_world, monkeypatch):
    from tests.test_world_pages import SPURS

    social_fans, _ = _fans_world(small_world, monkeypatch, second_try_fixed=True)
    SocialPost.query.delete()
    db.session.get(AppSetting, AppSetting.query.filter_by(key=social_feed.UNTIL_KEY).one().id).value = "2063-05-15"
    db.session.commit()
    figures = {name: 0 for name in world.MATCH_PLAYER_FIGURES}
    with small_world.begin() as c:
        fixture = c.execute(text("select id from fixtures where date = '2063-05-20'")).scalar()  # Tottenham 0-2 Chelsea
        c.execute(world.WorldPerson.__table__.insert(), [{"fm_uid": 91, "name": "Harry Spur", "as_of": UNTIL, "team_uid": SPURS}])
        c.execute(world.WorldGoal.__table__.insert(), [
            {"fixture_id": fixture, "seq": 0, "scorer_uid": MYERS, "side": 1, "minute": 87, "added_time": 0, "kind": 0},
            {"fixture_id": fixture, "seq": 1, "scorer_uid": MYERS, "side": 1, "minute": 90, "added_time": 2, "kind": 0}])
        c.execute(world.WorldMatchReport.__table__.insert(), [
            {"fixture_id": fixture, "stadium": "The Lane", "player_of_the_match_uid": MYERS, "minutes": 90, "as_of": UNTIL}])
        c.execute(world.WorldMatchTeam.__table__.insert(), [
            {"fixture_id": fixture, "side": 0, "team_uid": SPURS, "squad": 2, "passes": 500, "passes_completed": 450, "woodwork": 1,
             "red_cards": 1, "as_of": UNTIL},
            {"fixture_id": fixture, "side": 1, "team_uid": CHELSEA, "squad": 1, "passes": 300, "passes_completed": 250, "woodwork": 0, "red_cards": 0,
             "as_of": UNTIL}])
        c.execute(world.WorldMatchPlayer.__table__.insert(), [
            {**figures, "fixture_id": fixture, "side": 0, "seq": 0, "person_uid": 91, "played": 1, "rating": 5.9, "xg": 2.1,
             "shots": 6, "red_cards": 1, "sent_off_minute": 70, "as_of": UNTIL},
            {**figures, "fixture_id": fixture, "side": 1, "seq": 0, "person_uid": MYERS, "played": 1, "rating": 8.4, "xg": 0.9,
             "goals": 2, "shots": 2, "as_of": UNTIL}])
        c.execute(world.WorldMatchShot.__table__.insert(), [
            {"fixture_id": fixture, "side": 0, "seq": 0, "person_uid": 91, "minute": 33, "second": 10, "outcome": "woodwork",
             "across": 3.6, "height": 1.0, "as_of": UNTIL}])
    from app.services import world_queries as q

    with small_world.connect() as connection:
        summary = q.match_summary(connection, fixture)
    assert summary["player_of_the_match"] == "Ken Myers" and summary["late_winner"] == "Ken Myers 87'"
    assert summary["won_against_the_run_of_play"] == "Chelsea"
    assert summary["sides"][0]["red_cards"] == ["Harry Spur 70'"] and summary["sides"][0]["woodwork"] == ["Harry Spur 34'"]
    assert summary["sides"][1]["best_rated"] == "Ken Myers 8.4" and summary["sides"][0]["possession"] == 64
    social_feed.make_posts(UNTIL)
    result = SocialPost.query.filter(SocialPost.event_key.like("result:2063-05-20:%")).first()
    assert result.heat >= 50  # a late winner, a red card and a robbery
    with small_world.connect() as connection:
        notebook, _ = social_fans.thread_notebook(connection, result)
    assert notebook["the_match"]["late_winner"] == "Ken Myers 87'"


def test_the_fans_answer_this_weeks_news_and_talking_now_shows_the_last_answered(app, small_world, pages, monkeypatch):
    from datetime import timedelta

    social_fans, _ = _fans_world(small_world, monkeypatch, second_try_fixed=True)
    club = SocialAccount.query.filter_by(kind="club", club_uid=CHELSEA).one()
    newest = db.session.query(db.func.max(SocialPost.posted_on)).scalar()
    old = SocialPost(account_id=club.id, posted_on=newest - timedelta(days=8), kind="result", event_key="result:old",
                     text="FT | An old win", likes=10_000_000, reposts=0)
    new = SocialPost(account_id=club.id, posted_on=newest, kind="result", event_key="result:new", text="FT | This week's win",
                     likes=10_000_000, reposts=0)
    db.session.add_all([old, new])
    db.session.commit()
    candidates = social_fans.thread_candidates(100)
    assert candidates.index(new) < candidates.index(old)  # the same likes: this week's news first
    social_fans.write_thread(new)
    social_fans.write_thread(old)  # answered last
    page = pages.get("/feed/?show=talking").data.decode()
    assert page.index("FT | An old win") < page.index("FT | This week") and "Talking now" in page


def test_two_writers_making_one_clubs_fans_at_once_do_not_fail(app, small_world, monkeypatch):
    from app.services import social_fans

    saved = social_fans._saved

    def other_writer_first(account):
        # the slow writer and a job make Chelsea's fans at the same moment, with the same seed: the other one saves first
        monkeypatch.setattr(social_fans, "_saved", saved)
        with db.engines[world.BIND].connect() as other:
            social_fans.fans_of(other, CHELSEA)
        return saved(account)  # its handle is taken now

    monkeypatch.setattr(social_fans, "_saved", other_writer_first)
    with db.engines[world.BIND].connect() as connection:
        fans = social_fans.fans_of(connection, CHELSEA)
    db.session.commit()
    handles = [f.handle for f in SocialAccount.query.filter_by(kind="fan", club_uid=CHELSEA)]
    assert len(fans) == social_fans.FANS_PER_CLUB and sorted(f.handle for f in fans) == sorted(handles)
    assert len(handles) == len(set(handles)) == social_fans.FANS_PER_CLUB


def test_only_the_injuries_that_matter_are_posted_three_a_day_at_most(app, small_world):
    stars, squad = [500 + i for i in range(5)], [600 + i for i in range(4)]
    with small_world.begin() as c:
        c.execute(world.WorldPerson.__table__.insert(), [
            {"fm_uid": uid, "name": f"Star {uid}", "as_of": UNTIL, "team_uid": LIVERPOOL, "reputation_world": 8000 - uid}
            for uid in stars] + [
            {"fm_uid": uid, "name": f"Squad {uid}", "as_of": UNTIL, "team_uid": LIVERPOOL, "reputation_world": 1000} for uid in squad])
        base = {"seq": 0, "severity": "Moderate", "team_uid": LIVERPOOL, "current": 1, "as_of": UNTIL}
        c.execute(world.WorldInjury.__table__.insert(), [
            {**base, "person_uid": 500, "injury": "broken ribs", "injured_on": date(2063, 6, 10), "days": 25},  # a star, weeks out
            {**base, "person_uid": 501, "injury": "twisted ankle", "injured_on": date(2063, 6, 11), "days": 10},  # a star, days out
            {**base, "person_uid": 600, "injury": "blisters", "injured_on": date(2063, 6, 12), "days": 20}]  # a squad player
            + [{**base, "person_uid": uid, "injury": "torn knee ligaments", "injured_on": date(2063, 6, 14), "days": 60} for uid in squad[1:]]
            + [{**base, "person_uid": 502, "injury": "hamstring strain", "injured_on": date(2063, 6, 14), "days": 30}])
    with small_world.connect() as c:
        names = social_feed._clubs(c, {LIVERPOOL, CHELSEA})
        posts = [e for e in social_feed.injury_events(c, date(2063, 6, 1), date(2063, 6, 30), [LIVERPOOL, CHELSEA], names, (CHELSEA,))
                 if e.kind == "injury"]
    assert [e.links[0][2] for e in posts if e.day < date(2063, 6, 14)] == [500]  # not the short one, not the blisters
    assert "picked up broken ribs" in posts[0].text or "suffered broken ribs" in posts[0].text or "(broken ribs)" in posts[0].text
    june_14 = [e.links[0][2] for e in posts if e.day == date(2063, 6, 14)]
    assert len(june_14) == social_feed.INJURY_POSTS_A_DAY and june_14[0] == 502  # the star first, then the long ones
