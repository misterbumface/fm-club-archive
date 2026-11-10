"""Pictures of people: the game's faces first, generated portraits second."""

from __future__ import annotations

import random
from pathlib import Path

from app.services import world_portraits as wp


def _facts(**changes) -> wp.PortraitFacts:
    base = dict(uid=2002829035, name="Marcelo Álvarez", age=21, ethnicity=1, skin_tone=None, hair_colour=5,
                hair_length=1, nationality="Argentine", position="D (C)", club="Chelsea")
    return wp.PortraitFacts(**{**base, **changes})


def test_the_prompt_uses_ethnicity_nationality_age_position_hair_and_club():
    prompt = wp.build_prompt(_facts())

    for words in ("Mediterranean or Hispanic appearance", "Argentine nationality", "21 years old", "a centre-back",
                  "black hair", "shirt of Chelsea"):
        assert words in prompt


def test_an_ethnicity_is_named_only_when_it_sets_him_apart_from_his_nation():
    romanian = wp.build_prompt(_facts(nationality="Romanian", ethnicity=1, usual_ethnicity=1))
    swede = wp.build_prompt(_facts(nationality="Swedish", ethnicity=2, usual_ethnicity=0))
    assert "appearance" not in romanian and "Romanian nationality" in romanian
    assert "North African or Middle Eastern appearance" in swede


def test_unknown_facts_are_left_out_of_the_prompt():
    prompt = wp.build_prompt(_facts(ethnicity=None, nationality=None, position=None, club=None, hair_colour=99, hair_length=0))

    assert "appearance" not in prompt and "nationality" not in prompt and "club colours" not in prompt
    assert any(head in prompt for head in wp._BALD_HEADS) and "shirt of his club" in prompt


def test_the_face_pack_config_maps_each_image_to_its_person(tmp_path):
    folder = tmp_path / wp.GENERATED_FOLDER
    folder.mkdir()
    for name in ("2002829035.png", "28124580.png", "notes.png"):
        (folder / name).write_bytes(b"png")

    wp.write_face_config(folder)

    config = (folder / "config.xml").read_text()
    assert '<record from="2002829035" to="graphics/pictures/person/r-2002829035/portrait" />' in config
    assert "notes" not in config
    assert wp.face_pack_index(tmp_path) == {2002829035: folder / "2002829035.png", 28124580: folder / "28124580.png"}


def test_the_game_face_comes_before_an_old_portrait(tmp_path):
    game, old = tmp_path / "game.png", tmp_path / "old.png"
    game.write_bytes(b"g")
    old.write_bytes(b"o")

    assert wp.picture_of(5, {5: game}, {5: old}) == game
    assert wp.picture_of(5, {}, {5: old}) == old
    assert wp.picture_of(6, {}, {}) is None


def test_the_users_own_face_wins_over_the_archives_as_in_the_game(tmp_path):
    """Chiriță had a face in a Sochaux club pack and one made here: the game showed the pack's, the app its own (2 Nov 2026)."""
    ours, pack = tmp_path / wp.GENERATED_FOLDER, tmp_path / "99_Country" / "00_Clubs" / "Ligue 1" / "FCSM"
    for folder in (ours, pack):
        folder.mkdir(parents=True)
    (ours / "2002593895.png").write_bytes(b"png")
    wp.write_face_config(ours)
    (pack / "Alexandru Chiriţă.png").write_bytes(b"png")
    (pack / "config.xml").write_text('<record><list id="maps"><record from="Alexandru Chiriţă" '
                                     'to="graphics/pictures/person/r-2002593895/portrait" /></list></record>', encoding="utf-8")
    assert wp.face_pack_index(tmp_path) == {2002593895: pack / "Alexandru Chiriţă.png"}


def test_a_team_season_lists_its_players_without_pictures(monkeypatch):
    import sqlite3

    world = sqlite3.connect(":memory:")
    world.execute("create table player_seasons (person_uid, team_uid, season_start, starts, substitute_appearances)")
    world.executemany("insert into player_seasons values (?, ?, ?, ?, ?)", [
        (1, 630, 2062, 30, 2), (2, 630, 2062, 5, 0), (3, 630, 2062, 20, 0), (4, 630, 2061, 9, 0), (5, 650, 2062, 9, 0),
    ])
    monkeypatch.setattr(wp, "face_pack_index", lambda: {3: object()})
    monkeypatch.setattr(wp, "old_portraits", lambda: {})
    monkeypatch.setattr(wp, "picture_of", lambda uid, faces, old: faces.get(uid))

    assert wp.squad_without_pictures(world, 630) == [1, 2]  # latest season, most games first; 3 has a face
    assert wp.squad_without_pictures(world, 630, 2061) == [4]


def test_a_league_lists_its_first_team_players_without_pictures(monkeypatch):
    """La Liga's missing faces for InvokeAI (2 Nov 2026): league games in its newest season, still at that club, no staff,
    most games first."""
    import sqlite3

    world = sqlite3.connect(":memory:")
    world.execute("create table player_seasons (person_uid, team_uid, season_start, kind, league_uid, starts, substitute_appearances)")
    world.execute("create table people (fm_uid, team_uid, staff_job)")
    world.executemany("insert into player_seasons values (?, ?, ?, ?, ?, ?, ?)", [
        (1, 630, 2063, 1, 11, 20, 0), (2, 650, 2063, 1, 11, 25, 1), (3, 630, 2063, 1, 11, 5, 0),  # 3 has a face
        (4, 630, 2063, 1, 11, 9, 0),  # 4 has left for another club
        (5, 630, 2063, 2, 11, 3, 0),  # 5 played only in a cup
        (6, 630, 2062, 1, 11, 30, 0),  # 6 played last season only
        (7, 630, 2063, 1, 11, 0, 0)])  # 7 never played
    world.executemany("insert into people values (?, ?, ?)", [(1, 630, None), (2, 650, None), (3, 630, None), (4, 700, None),
                                                               (5, 630, None), (6, 630, None), (7, 630, None)])
    monkeypatch.setattr(wp, "picture_of", lambda uid, faces, old: faces.get(uid))
    assert wp.league_without_pictures(world, 11, faces={3: Path("face.png")}, old={}) == [(2, 650), (1, 630)]
    assert wp.league_without_pictures(world, 99, faces={}, old={}) == []


def test_each_player_has_his_own_look_and_keeps_it():
    prompts = {wp.build_prompt(_facts(uid=uid)) for uid in range(2002829000, 2002829040)}
    assert len(prompts) == 40  # forty players, forty different looks
    assert wp.build_prompt(_facts()) == wp.build_prompt(_facts())  # the same player: the same look
    assert wp.build_prompt(_facts(), random.Random(7)) != wp.build_prompt(_facts(), random.Random(8))  # "New picture"


def test_a_face_gets_a_few_features_not_all_of_them():
    """FLUX drew all nine features strongly and the faces became caricatures (10 Nov 2026)."""
    features = set(wp._FACES + wp._JAWS + wp._CHEEKS + wp._NOSES + wp._EYES + wp._BROWS + wp._LIPS + wp._EARS + wp._MARKS) - {""}
    for seed in range(40):
        look = wp.appearance(_facts(), random.Random(seed))
        assert sum(part in features for part in look) == wp.FACE_FEATURES
    prompt = wp.build_prompt(_facts())
    assert "not a generic model face" not in prompt and "realistic skin texture" not in prompt


def test_the_look_fits_age_hair_length_and_build():
    for seed in range(60):
        teen = ", ".join(wp.appearance(_facts(age=17), random.Random(seed)))
        assert not any(beard in teen for beard in ("full thick beard", "short neat beard", "goatee", "chin-strap"))
        bald = wp.appearance(_facts(hair_length=0), random.Random(seed))
        assert not any(" hair, " in part for part in bald)
    stocky = wp.appearance(_facts(height_cm=180, weight_kg=88), random.Random(1))
    assert "a stocky build with a thick neck" in stocky


def test_the_expression_follows_his_character():
    hothead = _facts(attributes={"Aggression": 20, "Flair": 6, "Leadership": 8, "Composure": 9, "Determination": 10, "Teamwork": 8,
                                 "Work Rate": 10, "Bravery": 11})
    angry = sum(any(face in wp.expression(hothead, random.Random(seed)) for face in wp._CHARACTER[0][2]) for seed in range(200))
    calm = sum(any(face in wp.expression(_facts(attributes={"Aggression": 2, "Composure": 20}), random.Random(seed))
                   for face in wp._CHARACTER[0][2]) for seed in range(200))
    assert angry > 100 and calm == 0  # a hothead often glares; a calm player never does


def test_staff_get_a_staff_photograph_dressed_for_the_job():
    base = dict(uid=5, name="Carl Dimario", age=76, ethnicity=1, skin_tone=None, hair_colour=None, hair_length=None,
                nationality="Romanian", position=None, club=None, usual_ethnicity=1)
    manager = wp.build_prompt(wp.PortraitFacts(**base, job="Manager", job_team="Chelsea"))
    assert "press photograph" in manager and "football manager" in manager and ("suit" in manager)
    assert "footballer" not in manager
    assert any(word in manager for word in ("white hair", "silver-grey hair", "grey hair", "bald"))  # 76 years old
    coach = wp.build_prompt(wp.PortraitFacts(**base | {"age": 45}, job="Coach", job_team="Chelsea U21"))
    assert "football coach" in coach and "Chelsea U21" in coach and "suit" not in coach
    player = wp.build_prompt(wp.PortraitFacts(**base | {"age": 24}))
    assert "photograph of a footballer" in player and "suit" not in player


def test_the_archive_keeps_the_photograph_and_the_game_gets_the_cut_out(tmp_path, monkeypatch):

    from PIL import Image

    monkeypatch.setattr(wp, "ARCHIVE_FACES", tmp_path / "archive")
    cut = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    monkeypatch.setattr(wp, "cut_out", lambda png: _png(cut))
    faces = tmp_path / "faces"
    (faces / "My own faces").mkdir(parents=True)

    archive_copy = wp.save_portrait(2002329, _png(Image.new("RGB", (64, 64), (30, 90, 160))), faces_dir=faces)

    assert Image.open(archive_copy).mode == "RGB"  # the photograph, background and all
    game_copy = faces / wp.GENERATED_FOLDER / "2002329.png"
    assert Image.open(game_copy).mode == "RGBA" and Image.open(game_copy).getpixel((0, 0))[3] == 0  # transparent
    assert wp.picture_of(2002329, {2002329: game_copy}, {}) == archive_copy
    own = faces / "My own faces" / "2002329.png"
    own.write_bytes(_png(cut))
    assert wp.picture_of(2002329, {2002329: own}, {}) == own  # the user's own face comes first


def test_a_new_picture_of_a_face_pack_player_goes_over_the_packs_picture_with_a_backup(tmp_path, monkeypatch):
    """Chiriță's new picture never reached the game, which showed his face from a Sochaux club pack (2 Nov 2026): the new
    picture now goes over the pack's, whose original is kept once; no second claim stays in our folder."""
    from PIL import Image

    monkeypatch.setattr(wp, "ARCHIVE_FACES", tmp_path / "archive")
    monkeypatch.setattr(wp, "FACE_PACK_BACKUPS", tmp_path / "backups")
    monkeypatch.setattr(wp, "FACES_DIR", tmp_path / "faces")
    monkeypatch.setattr(wp, "cut_out", lambda png: _png(Image.new("RGBA", (64, 64), (0, 0, 0, 0))))
    faces, uid = tmp_path / "faces", 2002593895
    pack, ours = faces / "99_Country" / "FCSM", faces / wp.GENERATED_FOLDER
    pack.mkdir(parents=True)
    ours.mkdir()
    original = _png(Image.new("RGBA", (32, 32), (200, 0, 0, 255)))
    (pack / "Alexandru Chiriţă.png").write_bytes(original)
    (pack / "config.xml").write_text(f'<record><list id="maps"><record from="Alexandru Chiriţă" to="graphics/pictures/person/r-{uid}/portrait" />'
                                     '</list></record>', encoding="utf-8")
    (ours / f"{uid}.png").write_bytes(original)  # an older picture of ours, which the game ignored
    wp.write_face_config(ours)

    archive_copy = wp.save_portrait(uid, _png(Image.new("RGB", (64, 64), (30, 90, 160))), faces_dir=faces)

    face = pack / "Alexandru Chiriţă.png"
    assert Image.open(face).size == (wp.GAME_SIZE, wp.GAME_SIZE)  # the new picture, in the pack's place
    assert (tmp_path / "backups" / "99_Country" / "FCSM" / "Alexandru Chiriţă.png").read_bytes() == original
    assert not (ours / f"{uid}.png").exists() and str(uid) not in (ours / "config.xml").read_text()
    assert wp.picture_of(uid, wp.face_pack_index(faces), {}) == archive_copy  # the app shows the new photograph
    wp.save_portrait(uid, _png(Image.new("RGB", (64, 64), (90, 30, 160))), faces_dir=faces)
    assert (tmp_path / "backups" / "99_Country" / "FCSM" / "Alexandru Chiriţă.png").read_bytes() == original  # the first backup stays


def _png(image) -> bytes:
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _photo_world():
    import sqlite3

    world = sqlite3.connect(":memory:")
    world.executescript("""
        create table competitions (fm_uid integer, name text, scope text);
        create table teams (fm_uid integer, name text);
        create table fixtures (id integer, date text, competition_uid integer, home_uid integer, away_uid integer);
        create table goals (fixture_id integer, scorer_uid integer, side integer, kind integer);
        create table match_players (fixture_id integer, side integer, person_uid integer, played integer);
        insert into competitions values (11, 'English Premier Division', 'league'), (99, 'Nations League', 'national_teams');
        insert into teams values (630, 'Chelsea'), (676, 'Liverpool'), (650, 'Arsenal'), (771, 'Scotland');
        insert into fixtures values (1, '2063-04-02', 11, 630, 676), (2, '2063-04-09', 11, 650, 630), (3, '2063-03-25', 99, 765, 771);
        insert into goals values (1, 5, 0, 0), (3, 5, 0, 0);
        insert into match_players values (1, 0, 5, 1), (2, 1, 5, 1), (2, 1, 6, 1);""")
    return world


def _player(uid: int, position: str):
    return wp.PortraitFacts(uid=uid, name="Elias Jimenez", age=33, ethnicity=1, skin_tone=None, hair_colour=None, hair_length=None,
                            nationality="English", position=position, club="Chelsea")


def test_an_action_photo_shows_a_goal_a_celebration_an_action_or_a_matchday_from_many_cameras():
    world = _photo_world()
    plans = [wp.action_photo_plan(world, 5, _player(5, "ST (C)"), random.Random(seed)) for seed in range(80)]
    captions = {p.caption.split(" against ")[0] for p in plans}
    assert {"Scoring", "Celebrating his goal", "In action", "Matchday with Chelsea"} <= captions
    assert "Scoring against Liverpool, English Premier Division, 2 April 2063" in {p.caption for p in plans}
    assert "In action against Arsenal, English Premier Division, 9 April 2063" in {p.caption for p in plans}  # one of his matches
    assert len({p.prompt for p in plans}) >= 75  # hardly two alike
    assert len({c for p in plans for c in wp._CAMERAS if c in p.prompt}) >= 8  # many points of view
    assert all("colours of Chelsea" in p.prompt and "reference for his face" in p.prompt for p in plans)
    assert not any("Scotland" in p.caption for p in plans)  # not his country's games: he wears his club's colours
    in_april = [p for p in plans if "April" in p.caption]
    assert in_april and not any("snow" in p.prompt or "summer evening" in p.prompt for p in in_april)  # the weather of the month
    acting = [p for p in plans if p.caption.startswith("In action against Arsenal")]
    assert acting and all("opponents in the colours of Arsenal" in p.prompt for p in acting)


def test_a_goalkeeper_makes_saves_and_never_celebrates_goals():
    world = _photo_world()
    plans = [wp.action_photo_plan(world, 6, _player(6, "GK"), random.Random(seed)) for seed in range(40)]
    assert not any(p.caption.startswith(("Scoring", "Celebrating")) for p in plans)
    saves = [p for p in plans if any(a in p.prompt for a in wp._ACTIONS["GK"])]
    assert len(saves) > len(plans) / 2 and len({a for p in saves for a in wp._ACTIONS["GK"] if a in p.prompt}) >= 8


def test_age_up_keeps_the_face_and_changes_age_and_shirt():
    prompt = wp.age_up_prompt(_facts(age=23, club="Chelsea"))
    assert prompt.startswith("Keep this exact man") and "look 23 years old" in prompt and "home match shirt of Chelsea" in prompt
    assert "a quarter of the frame" in prompt and "training" in prompt and "neutral background" not in prompt
    staff = wp.age_up_prompt(_facts(age=50, club=None, job="Manager", job_team="Reading"))
    assert "manager of Reading" in staff and "in his fifties" in staff and "match shirt" not in staff
    assert "keep his shirt" in wp.age_up_prompt(_facts(age=None, club=None)) and "keep his age" in wp.age_up_prompt(_facts(age=None, club=None))


def test_an_own_picture_is_made_square_and_loses_its_transparency(monkeypatch):
    import io

    from PIL import Image

    monkeypatch.setattr(wp, "face_box", lambda png: None)
    wide = io.BytesIO()
    Image.new("RGBA", (1600, 900), (0, 0, 0, 0)).save(wide, format="PNG")
    square = Image.open(io.BytesIO(wp.own_picture(wide.getvalue())))
    assert square.size == (900, 900) and square.mode == "RGB" and square.getpixel((10, 10)) == (128, 128, 128)
    tall = io.BytesIO()
    Image.new("RGB", (600, 1000), "white").save(tall, format="JPEG")
    assert Image.open(io.BytesIO(wp.own_picture(tall.getvalue()))).size == (600, 600)
    try:
        wp.own_picture(b"not a picture")
    except wp.PortraitError:
        pass
    else:
        raise AssertionError("a non-picture must be refused")


def test_a_league_whose_new_season_has_just_begun_counts_the_last_one(monkeypatch):
    """Argentina's 2064 season had begun in the save with rows and one game: "every one has a picture" with nobody in it."""
    import sqlite3

    world = sqlite3.connect(":memory:")
    world.execute("create table player_seasons (person_uid, team_uid, season_start, kind, league_uid, starts, substitute_appearances)")
    world.execute("create table people (fm_uid, team_uid, staff_job)")
    world.executemany("insert into player_seasons values (?, ?, ?, ?, ?, ?, ?)",
                      [(uid, 1, 2063, 1, 7, 20, 0) for uid in range(1, 11)] + [(1, 1, 2064, 1, 7, 1, 0), (2, 1, 2064, 1, 7, 0, 0)])
    world.executemany("insert into people values (?, ?, ?)", [(uid, 1, None) for uid in range(1, 11)])
    monkeypatch.setattr(wp, "picture_of", lambda uid, faces, old: faces.get(uid))
    found = wp.leagues_without_pictures(world, faces={3: Path("face.png")}, old={})
    assert found[7]["players"] == 10 and len(found[7]["missing"]) == 9
    assert wp.league_without_pictures(world, 7, faces={}, old={})[0] == (1, 1)
