"""Pictures for the stories (article_pictures): a competition's pool shared by its stories, each taking its own."""

import random
from datetime import date

import pytest

from app.extensions import db
from app.models.archive_image import ArchiveImageLink
from app.models.world_soccer_article import WorldSoccerArticle
from app.models.world_soccer_entity_link import WorldSoccerEntityLink
from app.models.world_soccer_issue import WorldSoccerIssue
from app.services import article_pictures, image_library, world_images
from tests.test_world_pages import app, small_world  # noqa: F401


def _pool(ref: str, urls: list[str], kind: str = "photograph") -> None:
    entity, uid = ref.split(":")
    for url in urls:
        image = image_library.register_archive_image(url, kind=kind, source="generated")
        image.links.append(ArchiveImageLink(entity_type=entity, entity_id=int(uid), relation="subject", world_ref=ref))
    db.session.commit()


def _story(headline: str, links: list[tuple[str, int]]) -> WorldSoccerArticle:
    issue = WorldSoccerIssue(publication_date=date(2063, 10, 27), knowledge_cutoff=date(2063, 10, 27), status="published", edition_name="x")
    article = WorldSoccerArticle(headline=headline, section="Europe", lead_key=None, prominence="standard", body="x", byline="Desk",
                                 article_type="report", sort_order=0)
    for kind, uid in links:
        article.entity_links.append(WorldSoccerEntityLink(entity_type=kind, entity_id=uid, world_ref=f"{kind}:{uid}", label=str(uid)))
    issue.articles.append(article)
    db.session.add(issue)
    db.session.commit()
    return article


@pytest.mark.usefixtures("app")
def test_stories_share_a_competition_pool_each_with_its_own_picture():
    _pool("competition:1000", ["/static/generated/world/photos/competition-1000-a.jpg", "/static/generated/world/photos/competition-1000-b.jpg"])
    first = _story("Night one", [("competition", 1000)])
    second = _story("Night two", [("competition", 1000)])
    assert article_pictures.pool_picture(first) != article_pictures.pool_picture(second)
    assert article_pictures.pool_picture(first) == article_pictures.pool_picture(first)  # a story keeps its picture
    assert article_pictures.pool_picture(_story("Nothing linked", [])) is None
    _pool("club:630", ["/static/generated/world/stadiums/club-630-x.jpg"], kind="stadium")
    both = _story("Chelsea in Europe", [("competition", 1000), ("club", 630)])
    assert article_pictures.pool_picture(both) == "/static/generated/world/stadiums/club-630-x.jpg"  # the club's own picture first
    world_images.hide_image(image_library.asset_for_url("/static/generated/world/stadiums/club-630-x.jpg").id)
    assert article_pictures.pool_picture(both).startswith("/static/generated/world/photos/competition-1000-")


def test_a_competition_prompt_has_its_mood_and_not_its_name():
    scene, prompt = article_pictures.competition_prompt("European Champions Cup", "continental", random.Random(1))
    assert scene in article_pictures.COMPETITION_SCENES and "European Champions Cup" not in prompt and "soccer" in prompt
    _, irish = article_pictures.competition_prompt("Irish Premier Division", "league", random.Random(1), "Ireland", 60)
    assert "In Ireland" in irish and "small ground with one main stand" in irish and "Irish Premier Division" not in irish
    _, top = article_pictures.competition_prompt("English Premier Division", "league", random.Random(1), "England", 190)
    assert "In England" in top and "huge modern stadium" in top
    _, euro = article_pictures.competition_prompt("UEFA Champions League", "continental", random.Random(1), None, 200)
    assert "In " not in euro.split("European night")[0] and "huge modern stadium" in euro
    scene, _ = article_pictures.competition_prompt("World Cup Asian Qualifying Section", "national_teams", random.Random(1))
    assert scene in article_pictures.NATIONAL_SCENES
    scene, prompt = article_pictures.competition_prompt("World Cup", "national_teams", random.Random(1))  # the finals: a tournament's scenes
    assert scene in article_pictures.TOURNAMENT_SCENES and "summer of football" in prompt and "World Cup" not in prompt
    assert article_pictures.is_finals("European Football Championship", "national_teams") and not article_pictures.is_finals("Under 20 World Cup", "national_teams")
    assert world_images.KINDS_FOR["competition"] == ("trophy", "photograph", "matchday")


def test_prompts_are_plain_sentences_without_negations_or_names(monkeypatch):
    from app.services import world_portraits

    scene, prompt = article_pictures.competition_prompt("European Champions Cup", "continental", random.Random(3))
    assert prompt.startswith(scene[0].upper() + scene[1:] + ".") and "European Champions Cup" not in prompt
    assert "A European night between two big clubs." in prompt and "no helmets" not in prompt and "Not American" not in prompt
    sent = {}

    def fake_request(endpoint, body, url, timeout):
        import json

        sent.update(json.loads(body))
        return b"png"
    monkeypatch.setattr(world_portraits, "_request_image", fake_request)
    assert world_portraits.draw("x", seed=5, model=None) == b"png" and "model" not in sent and sent["negative_prompt"]
    world_portraits.draw("x", seed=5)
    assert sent["model"] == world_portraits.PORTRAIT_MODEL


@pytest.mark.usefixtures("app")
def test_the_gallery_deletes_pictures_and_their_files(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "GENERATED_DIR", tmp_path)
    pool_file = tmp_path / "world" / "photos" / "competition-7-a.jpg"
    pool_file.parent.mkdir(parents=True)
    pool_file.write_bytes(b"jpg")
    _pool("competition:7", ["/static/generated/world/photos/competition-7-a.jpg"])
    (image,) = world_images.library()
    assert world_images.delete_image(image.id).id == image.id and not pool_file.exists() and world_images.library() == []
    assert world_images.delete_image(999) is None
    drawn = tmp_path / "illustrations" / "article-1-x.jpg"
    drawn.parent.mkdir(parents=True)
    drawn.write_bytes(b"jpg")
    story = _story("Drawn", [])
    story.image_url = "/static/generated/illustrations/article-1-x.jpg"
    db.session.commit()
    assert [a.id for a in article_pictures.illustrated_articles()] == [story.id]
    article_pictures.remove_illustration(story)
    assert story.image_url is None and not drawn.exists() and article_pictures.illustrated_articles() == []


@pytest.mark.usefixtures("app")
def test_a_story_about_a_player_takes_one_of_his_action_photos_before_his_face(app):
    from app.public import picture

    _pool("person:7", ["/static/generated/world/photos/person-7-a.jpg", "/static/generated/world/photos/person-7-b.jpg"])
    first, second = _story("Júnior strikes", [("person", 7)]), _story("Júnior again", [("person", 7)])
    with app.test_request_context():
        assert picture(first).startswith("/static/generated/world/photos/person-7-") and picture(first) != picture(second)
        assert picture(first) == picture(first)
        for image in world_images.pictures("person:7", ("photograph",)):
            world_images.hide_image(image.id)
        assert not (picture(first) or "").startswith("/static/generated/world/photos/person-7-")  # none left: the face or the pools


@pytest.mark.usefixtures("app")
def test_the_pools_to_draw_include_a_tournament_in_play(monkeypatch):
    from app.public import football

    assert article_pictures.tournaments_in_play() == []  # no world yet: none in play, and no error
    monkeypatch.setattr(football, "major_tournament", lambda c: {"uid": 1301385})
    assert article_pictures.tournaments_in_play() == [1301385]


@pytest.mark.usefixtures("app")
def test_a_club_prompt_carries_the_colours_and_never_the_crest(monkeypatch):
    scene, prompt = article_pictures.club_prompt("Chelsea", random.Random(2))
    assert scene in [s.format(colours="the colours of Chelsea") for s in article_pictures.CLUB_SCENES]
    assert "the colours of Chelsea" in prompt and "free of crests and lettering" in prompt and "soccer" in prompt and "no " not in prompt
    # the followed clubs come first in the clubs to draw for
    from app.services import story_leads
    monkeypatch.setattr(story_leads, "followed_clubs", lambda: (630,))
    assert article_pictures.clubs_in_the_news()[:1] == [630]


def test_a_national_teams_pool_is_drawn_with_international_scenes_in_its_country(monkeypatch, small_world):
    """The national team stories (England, 24 Nov 2063) link the nation's uid as a club and had no pool: a club query found
    no club and the drawer would have drawn 'club 765' in club colours."""
    import io

    from PIL import Image

    from app.models import world

    prompts = []

    def fake_draw(prompt, seed):
        prompts.append(prompt)
        out = io.BytesIO()
        Image.new("RGB", (64, 40), (20, 60, 120)).save(out, format="PNG")
        return out.getvalue()

    monkeypatch.setattr(article_pictures, "_draw", fake_draw)
    added = []
    monkeypatch.setattr(world_images, "add_generated_image", lambda ref, kind, data, caption, extension: added.append((ref, kind, caption)))
    monkeypatch.setattr(world_images, "pictures", lambda ref, kinds=None: [])
    assert article_pictures.draw_club_pictures(765, 1) == 1  # England is a nation in the small world, not a club
    assert added[0][0] == "club:765" and added[0][2].startswith("England: ")
    assert "In England" in prompts[0] and "international" in prompts[0].lower() and "the colours of" not in prompts[0]
