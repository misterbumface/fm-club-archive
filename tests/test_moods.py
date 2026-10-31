# ruff: noqa: F811  (pytest fixtures imported from test_world_pages)
"""Morale, happiness and the dressing room: the player and club pages, the dressing-room talk the writers and fans get,
and the stories (an unsettled player, team-mates taking sides, a concern that ends)."""

from datetime import date

from app.models import world
from app.services import moods
from app.services import story_leads_moods as dressing
from app.services.story_leads import _World
from tests.test_tactics import SIGNING, STAR, USUAL, _people
from tests.test_world_pages import AS_OF, CHELSEA, LEAGUE, app, pages, small_world  # noqa: F401

SAVE_DAY = date(2063, 9, 15)
LEFT_OUT, UNFAIR, MOVE_ON, NEW_DEAL = (32, 596), (19, 808), (10, 666), (8, 634)


def _concern(person: int, seq: int, code: tuple[int, int], started: date, ended: date | None = None, stage: int = 1,
             about: int | None = None, competition: int | None = None) -> dict:
    return {"person_uid": person, "seq": seq, "kind": code[0], "code": code[1], "stage": stage, "ref": None, "ref2": None,
            "about_person_uid": about, "competition_uid": competition, "nation_uid": None, "started": started, "ended": ended,
            "as_of": SAVE_DAY}


def _moods(engine) -> None:
    """The star left out of the league squad and telling his team-mates; three team-mates (two of his circle) think he is
    treated unfairly; the signing wants to move on; 101's wish for a new deal ended after two months."""
    _people(engine)
    backers = (102, 103, 104)
    with engine.begin() as c:
        c.execute(world.WorldPersonMood.__table__.insert(), [
            {"person_uid": uid, "morale": 8 if uid == STAR else 17, "happiness": -40 if uid == STAR else 70, "as_of": SAVE_DAY}
            for uid in (*USUAL, STAR, SIGNING)])
        c.execute(world.WorldPersonConcern.__table__.insert(), [
            _concern(STAR, 0, LEFT_OUT, date(2063, 9, 9), stage=2, competition=LEAGUE),
            *[_concern(uid, 0, UNFAIR, date(2063, 9, 9 + i), about=STAR) for i, uid in enumerate(backers)],
            _concern(SIGNING, 0, MOVE_ON, date(2063, 9, 12)),
            _concern(101, 0, NEW_DEAL, date(2063, 7, 1), date(2063, 9, 5)),
            _concern(105, 0, (19, 819), date(2063, 9, 10))])  # not named: left out
        c.execute(world.WorldPersonFeeling.__table__.insert(), [
            {"person_uid": STAR, "other_uid": 102, "likes": 1, "reason": 5, "level": 70, "as_of": SAVE_DAY},
            {"person_uid": STAR, "other_uid": 103, "likes": 1, "reason": 5, "level": 20, "as_of": SAVE_DAY},
            {"person_uid": STAR, "other_uid": 106, "likes": 0, "reason": 0, "level": 60, "as_of": SAVE_DAY}])
        c.execute(world.WorldSocialGroup.__table__.insert(), [
            {"team_uid": CHELSEA, "person_uid": uid, "group_number": group, "seq": seq, "as_of": SAVE_DAY}
            for group, members in ((1, (STAR, 102, 103)), (2, (104, 105, SIGNING))) for seq, uid in enumerate(members)])


def test_a_players_mood_tells_his_concerns_backers_and_favoured_personnel(small_world):
    _moods(small_world)
    with small_world.connect() as c:
        mood = moods.person_mood(c, STAR)
        assert (mood["morale_word"], mood["happiness_word"]) == ("Fair", "Not happy")
        # every level has a word, so no story can say "seven out of twenty"
        assert [moods.morale_word(v) for v in (15, 10, 7, 4, 1)] == ["Very Good", "Fair", "Poor", "Very Poor", "Abysmal"]
        assert not any(ch.isdigit() for v in range(0, 21) for ch in moods.morale_word(v))
        (concern,) = mood["open"]
        assert concern["text"] == "Left out of English Premier Division squad" and concern["told_team_mates"]
        assert [b["uid"] for b in mood["backers"]] == [102, 103, 104]
        assert [f["uid"] for f in mood["favoured"]] == [102]  # 103 is liked below the game's favoured level
        assert [f["uid"] for f in mood["dislikes"]] == [106]
        assert moods.person_mood(c, 101)["recent"][0]["text"] == "Wants a new deal to reflect his ability"
        assert moods.person_mood(c, 2000099999) is None  # no mood in the save


def test_the_dressing_room_groups_the_squad_and_shows_who_takes_sides(small_world):
    _moods(small_world)
    with small_world.connect() as c:
        room = moods.dressing_room(c, CHELSEA)
    assert [[m["uid"] for m in group] for group in room["groups"]] == [[STAR, 102, 103], [104, 105, SIGNING]]
    (side,) = room["sides"]
    assert side["uid"] == STAR and side["own_words"] == "left out of English Premier Division squad"
    assert [(b["uid"], b["same_group"]) for b in side["backers"]] == [(102, True), (103, True), (104, False)]


def test_the_pages_show_morale_concerns_and_the_dressing_room(small_world, pages):
    _moods(small_world)
    club = pages.get(f"/clubs/{CHELSEA}").data.decode()
    assert "Dressing room" in club and "backs Big Star" in club and ">Fair<" in club
    person = pages.get(f"/people/{STAR}").data.decode()
    assert "Morale and happiness" in person and "3 team-mates take his side" in person
    assert "Left out of English Premier Division squad" in person


def test_the_talk_is_only_what_reached_the_team_mates(small_world):
    _moods(small_world)
    with small_world.connect() as c:
        talk = moods.public_talk(c, STAR, SAVE_DAY)
        assert talk[0].startswith("has told his team-mates: left out of English Premier Division squad")
        assert talk[1] == "team-mates who think he has been treated unfairly: Chelsea 102, Chelsea 103, Chelsea 104"
        assert moods.public_talk(c, SIGNING, SAVE_DAY) == []  # he keeps his wish to move on to himself
        assert moods.public_talk(c, STAR, date(2063, 6, 1)) == []  # before any of it
        assert moods.public_talk(c, 102, SAVE_DAY) == ["thinks Big Star has been treated unfairly, since 2063-09-09"]


def test_the_stories_of_the_dressing_room(small_world):
    _moods(small_world)
    with small_world.connect() as c:
        w = _World(c, date(2063, 9, 1), SAVE_DAY, (CHELSEA,))
        found = {finder.__name__: finder(w) for finder in dressing.FINDERS}
    unsettled = {lead.facts["player"]: lead.facts for lead in found["unsettled"]}
    assert unsettled["New Man"]["concern"] == "Feels it is time to move on for a new challenge"
    assert unsettled["Big Star"]["told_his_team_mates"] == "yes" and unsettled["Big Star"]["morale"] == "Fair"
    assert unsettled["Big Star"]["team_mates_who_think_he_is_treated_unfairly"] == "Chelsea 102, Chelsea 103, Chelsea 104"
    (side,) = found["taking_sides"]
    assert side.headline == "3 Chelsea players take Big Star's side"
    assert side.facts["from_his_own_circle_in_the_dressing_room"] == "Chelsea 102, Chelsea 103"
    assert side.facts["from_other_circles"] == "Chelsea 104"
    (settled,) = found["settled"]
    assert settled.facts["concern"] == "Wants a new deal to reflect his ability" and settled.facts["lasted_days"] == 66


def test_a_transfer_story_knows_the_concerns_before_the_move(small_world):
    from app.services import story_leads_now as now

    _moods(small_world)
    with small_world.connect() as c:
        w = _World(c, date(2063, 9, 1), SAVE_DAY, (CHELSEA,))
        assert now._concerns_before(w, SIGNING, date(2063, 9, 14)) == ["Feels it is time to move on for a new challenge (from 2063-09-12)"]
        assert now._concerns_before(w, 101, date(2063, 9, 14)) == ["Wants a new deal to reflect his ability (from 2063-07-01 to 2063-09-05)"]
        assert now._concerns_before(w, 102, date(2063, 9, 14)) == []  # backing a team-mate does not explain a move


def test_a_preview_hears_who_is_not_fit_sharp_or_fresh(small_world):
    _moods(small_world)
    with small_world.begin() as c:
        table = world.WorldPersonMood.__table__
        c.execute(table.update().where(table.c.person_uid == STAR).values(sharpness=6200, fatigue=-151, condition=9050))
        c.execute(table.update().where(table.c.person_uid == 102).values(sharpness=9800, fatigue=420, condition=7800))
        c.execute(table.update().where(table.c.person_uid == 103).values(sharpness=9000, fatigue=-300, condition=9900))
    names = {STAR: "Big Star", 102: "Chelsea 102", 103: "Chelsea 103"}
    with small_world.connect() as c:
        notes = moods.fitness_notes(c, names, date(2063, 9, 20))
        assert sorted(notes[:-1]) == ["Big Star: short of match sharpness (62%)", "Chelsea 102: condition 78%, jaded"]
        assert notes[-1] == "(as of 2063-09-15)"
        assert moods.fitness_notes(c, names, date(2063, 12, 1)) == []  # too far from the save to tell
    assert [moods.fatigue_word(v) for v in (-500, -151, 0, 250, 500)] == ["fresh", "fresh", "some fatigue", "tired", "jaded"]
