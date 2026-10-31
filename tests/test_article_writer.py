

def test_the_writer_gives_the_picture_and_it_holds_no_names_or_numbers():
    """27 Oct 2026: the writer describes the picture of its own piece; a name or a number in it is dropped (models draw a
    name as a stranger and text as nonsense), and the fixed scenes of the kind serve instead."""
    from app.services import article_writer as aw

    notebook = {"story": {"facts": {"the_game": {"home": "Sutton United", "away": "Derby County", "ground": "Paul Doswell Stadium",
                                                 "goals": ["Jamie Chadwick 48'"]}, "manager": "Tom Edwards"}}}
    good = "Rain on a small town ground at dusk, an old man in a flat cap eating a bacon roll by a low corrugated stand."
    assert aw._article({"headline": "H", "body": "B", "picture": good})["picture"] == good
    assert "picture" not in aw._article({"headline": "H", "body": "B", "picture": "short"})
    assert aw.picture_is_clean(good, notebook)
    assert not aw.picture_is_clean("Fans of Sutton in the rain.", notebook)  # a club
    assert not aw.picture_is_clean("A man watches Edwards on the touchline.", notebook)  # a surname
    assert not aw.picture_is_clean("A scoreboard reads 0-4.", notebook)  # a number
    assert aw.picture_is_clean("A county ground in drizzle, a united crowd of scarves.", notebook)  # common words are fine


def test_the_picture_prompt_takes_the_writers_scene(app):
    from types import SimpleNamespace

    from app.services import article_pictures

    article = SimpleNamespace(id=1, lead={"kind": "travel_log", "refs": [], "picture": "Rain on a low stand at dusk."}, headline="H")
    prompt = article_pictures.illustration_prompt(article)
    assert prompt.startswith("Rain on a low stand at dusk. ") and "documentary press photograph" in prompt
    article.lead["picture"] = "A small football ground at dusk."
    assert article_pictures.illustration_prompt(article).startswith("A small soccer ground at dusk. ")
    article.lead.pop("picture")
    assert "documentary" not in article_pictures.illustration_prompt(article)  # the scenes of its kind
