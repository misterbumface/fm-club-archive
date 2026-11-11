"""Pictures of people in the world archive: the game's faces first, generated portraits second.

A person's picture is, in this order:
1. the face in the user's FM face pack (``graphics/Faces/**/config.xml`` maps FM uids to images);
2. a portrait generated earlier by the old archive (``app/static/generated/portraits/``);
3. a portrait generated here with Draw Things (its HTTP API, ``/sdapi/v1/txt2img``), saved in
   ``app/static/generated/faces/<uid>.png`` and copied into a separate face-pack folder
   (``Archive generated``) with its own ``config.xml``, so the game shows the same face.
The user's own faces are never changed; deleting the ``Archive generated`` folder undoes step 3
in the game.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.services import fm_save
from config import ARCHIVE, ARCHIVE_DIR, GENERATED_DIR, generated_path

log = logging.getLogger(__name__)

FACES_DIR = ARCHIVE.faces_dir  # the game's face pack (the user's own faces, and this archive's folder in it)
GENERATED_FOLDER = ARCHIVE.face_folder
STADIUMS_DIR = ARCHIVE.stadiums_dir
_STADIUM_MAP = re.compile(r'<record from="([^"]+)" to="graphics/pictures/stadium/(\d+)/stadium"')
ARCHIVE_FACES = GENERATED_DIR / "faces"
OLD_PORTRAITS = GENERATED_DIR / "portraits"
DRAW_THINGS_URL = os.environ.get("DRAW_THINGS_URL", "http://127.0.0.1:7860")
# Each request names its model, so neither depends on the one chosen in the Draw Things app: a text-to-image model
# for portraits, an editing model (it keeps the face of a reference picture) for action photos.
PORTRAIT_MODEL = os.environ.get("DRAW_THINGS_MODEL", "z_image_turbo_1.0_i8x.ckpt")
# FLUX.1 Kontext [dev] at 5 bits: about twice as fast as Qwen Image Edit on a 24 GB Mac (3.8 against 7.6 minutes at 28 steps),
# with a good likeness; 20 steps take about a third less time again.
EDIT_MODEL = os.environ.get("DRAW_THINGS_EDIT_MODEL", "flux_1_kontext_dev_q5p.ckpt")
ILLUSTRATION_DEFAULT = os.environ.get("DRAW_THINGS_ILLUSTRATION_MODEL") or None  # None: the one selected in Draw Things
EDIT_STEPS = int(os.environ.get("DRAW_THINGS_EDIT_STEPS", "20"))
# Club crests and makers' logos appear on kit even when the prompt forbids them; models that use a negative prompt avoid them.
# Scars, cuts and wounds come out far too strong, so no face gets them (InvokeAI's models take no negative prompt).
NEGATIVE_PROMPT = "logo, crest, club badge, emblem, sponsor, brand mark, text, lettering, watermark, scar, cut, wound, blood"
ARCHIVE_SIZE, GAME_SIZE = 512, 250
FACE_PACK_BACKUPS = ARCHIVE_DIR / "backups" / "face pack"  # the face pack's own pictures, as they were before a new one
_FACE_MAP = re.compile(r'<record from="([^"]+)" to="graphics/pictures/person/(?:r-)?(\d+)/portrait"')

_ETHNICITY_WORDS = {
    0: "Northern European",
    1: "Mediterranean or Hispanic",
    2: "North African or Middle Eastern",
    3: "Black African or Afro-Caribbean",
    4: "South Asian",
    5: "South-East Asian",
    6: "Pacific Islander",
    7: "Indigenous Latin American",
    8: "Aboriginal Australian",
    9: "mixed-race",
    10: "East Asian",
}
_HAIR_COLOURS = fm_save.HAIR_COLOUR_WORDS
_POSITION_WORDS = {
    "GK": "goalkeeper", "SW": "sweeper", "D (R)": "right-back", "D (L)": "left-back", "D (C)": "centre-back",
    "WB (R)": "right wing-back", "WB (L)": "left wing-back", "DM": "defensive midfielder", "M (R)": "right midfielder",
    "M (L)": "left midfielder", "M (C)": "central midfielder", "AM (R)": "right winger", "AM (L)": "left winger",
    "AM (C)": "attacking midfielder", "ST (C)": "striker",
}


class PortraitError(RuntimeError):
    """Draw Things could not make a portrait."""


@dataclass(frozen=True)
class PortraitFacts:
    uid: int
    name: str
    age: int | None
    ethnicity: int | None
    skin_tone: int | None
    hair_colour: int | None
    hair_length: int | None
    nationality: str | None  # the adjective, "Argentine"
    position: str | None  # "D (C)"
    club: str | None
    height_cm: int | None = None
    weight_kg: int | None = None
    attributes: dict[str, int] | None = None  # the game's 1-20 attributes; the mental ones give his expression
    usual_ethnicity: int | None = None  # the most common ethnicity among his nation's people
    job: str | None = None  # a staff member's job ("Manager", "Physio"): staff get a staff photograph, not a player portrait
    job_team: str | None = None  # the team of that job


# -- finding pictures ------------------------------------------------------------------------------
def face_pack_index(faces_dir: Path = FACES_DIR) -> dict[int, Path]:
    """FM uid -> image, from every config.xml of the face pack (the user's faces and ours). The user's own faces win over
    this archive's folder, as in the game: Chiriță's face in a Sochaux club pack showed in the game while the archive
    showed its own, read first by the disk's order, and made him new ones the game never used (2 Nov 2026)."""
    found: dict[int, Path] = {}
    if not faces_dir.is_dir():
        return found
    for config in sorted(faces_dir.rglob("config.xml"), key=lambda c: (c.parent.name == GENERATED_FOLDER, str(c))):
        for name, uid in _FACE_MAP.findall(config.read_text(encoding="utf-8", errors="replace")):
            for extension in (".png", ".jpg"):
                image = config.parent / f"{name}{extension}"
                if image.is_file():
                    found.setdefault(int(uid), image)
                    break
    return found


def stadium_pack_index(stadiums_dir: Path = STADIUMS_DIR) -> dict[int, Path]:
    """Stadium FM uid -> picture, from every config.xml of the game's stadium pack, read where it is."""
    found: dict[int, Path] = {}
    if not stadiums_dir.is_dir():
        return found
    for config in stadiums_dir.rglob("config.xml"):
        for name, uid in _STADIUM_MAP.findall(config.read_text(encoding="utf-8", errors="replace")):
            for extension in (".jpg", ".jpeg", ".png"):
                image = config.parent / f"{name}{extension}"
                if image.is_file():
                    found.setdefault(int(uid), image)
                    break
    return found


def old_portraits(archive_db: Path = ARCHIVE_DIR / "fm_archive.sqlite3") -> dict[int, Path]:
    """The archive's own portraits (old ones and uploads), by FM uid, through the image links'
    world references (docs/images-plan.md); the newest wins."""
    if not archive_db.exists():
        return {}
    connection = sqlite3.connect(f"file:{archive_db}?mode=ro", uri=True)
    found: dict[int, Path] = {}
    try:
        rows = connection.execute(
            "select l.world_ref, a.url from archive_image_links l join archive_images a on a.id = l.image_id "
            "where a.kind = 'portrait' and a.is_active = 1 and l.world_ref like 'person:%' order by a.priority, a.id"
        ).fetchall()
    except sqlite3.OperationalError:  # a database from before the image links had world references
        rows = []
    finally:
        connection.close()
    for ref, url in rows:
        uid = ref.split(":", 1)[1]
        path = generated_path(url)
        if uid.isdigit() and path is not None and path.is_file():
            found[int(uid)] = path
    return found


def picture_of(uid: int, faces: dict[int, Path], old: dict[int, Path]) -> Path | None:
    """The user's own face first; for a picture made here, the archive's full photograph rather than the
    game's cut-out of it (both kept by save_portrait)."""
    generated = ARCHIVE_FACES / f"{uid}.png"
    face = faces.get(uid)
    ours = face is not None and (face.parent.name == GENERATED_FOLDER or _overwritten(face))
    if ours and generated.is_file():
        face = generated
    for candidate in (face, old.get(uid), generated):
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def _overwritten(face: Path) -> bool:
    """A face pack picture the archive wrote over (its original is in the backups)."""
    try:
        return face_backup_path(face).exists()
    except ValueError:  # not under the face pack
        return False


# -- the prompt ------------------------------------------------------------------------------------
# A look for each player, so the portraits do not all share one "average footballer" face. The
# choices are random but seeded by the player's uid: his look stays his own, and "New picture"
# draws a new one. Hair follows the save's hair length; its texture fits his background.
# Mild features only: FLUX.3 pushes a strong one ("hollow cheeks", "sharply angled eyebrows", "large ears") into a
# gaunt, demonic face (10 Nov 2026).
_FACES = ("an oval face", "a round face", "a square face", "a slightly long face", "a heart-shaped face", "a rectangular face")
_JAWS = ("a square jaw", "a soft rounded jaw", "a cleft chin", "a defined jawline")
_CHEEKS = ("high cheekbones", "full cheeks", "wide cheekbones")
_NOSES = ("a straight nose", "a slightly broad nose", "a nose with a slight bump", "a small nose", "a slightly crooked nose",
          "a long straight nose", "a short nose")
_EYES = ("deep-set eyes", "almond-shaped eyes", "slightly wide-set eyes", "warm eyes", "slightly downturned eyes")
_BROWS = ("thick straight eyebrows", "thin eyebrows", "full eyebrows", "light eyebrows")
_LIPS = ("thin lips", "full lips", "a wide mouth", "a small mouth")
_EARS = ("ears that stick out a little", "small ears", "")
_MARKS = ("", "", "", "a few freckles across the nose", "a small mole on one cheek",
          "a light sunburn across the nose", "a dimple in one cheek")
_EXPRESSIONS = ("a calm neutral expression", "a faint confident smile", "a serious determined look", "a relaxed half smile",
                "a focused look", "a friendly smile", "a slightly shy expression")
# His character from the game's mental attributes: (attribute, high or low, expressions). A trait far
# from the middle of the 1-20 scale is likelier to show; chance still plays a part.
_CHARACTER = (
    ("Aggression", True, ("a hard, serious look", "a tough, no-nonsense expression", "a slight frown")),
    ("Flair", True, ("a cheeky grin", "a playful smirk with one eyebrow raised", "a mischievous half smile", "laughing eyes and a crooked smile")),
    ("Leadership", True, ("a commanding look with the chin slightly raised", "a proud, assured expression", "the calm authority of a captain")),
    ("Composure", True, ("a cool, unruffled half smile", "a relaxed, composed expression", "calm, heavy-lidded confidence")),
    ("Determination", True, ("a steely, determined stare", "a set jaw and focused eyes", "a hungry, driven look")),
    ("Teamwork", True, ("a warm, open smile", "a friendly smile that reaches the eyes", "an easy-going grin")),
    ("Work Rate", True, ("an earnest, eager expression", "a no-nonsense look", "a tired but satisfied smile")),
    ("Bravery", True, ("a fearless, direct look", "a direct, confident look", "a steady, unflinching look")),
    ("Composure", False, ("a slightly nervous expression", "a guarded, uncertain look", "a tense half smile")),
    ("Aggression", False, ("a gentle, soft-eyed expression", "a mild, polite smile")),
)
_YOUNG_EXPRESSIONS = ("a boyish grin", "a shy smile", "a wide-eyed, eager expression", "a slightly awkward smile")
_VETERAN_EXPRESSIONS = ("a wry, knowing smile", "a world-weary look", "the relaxed smile of a veteran")
FACE_FEATURES = 3  # of face, jaw, cheeks, nose, eyes, brows, lips, ears and marks, at random for each player
# Photo words: "sharp eyes, realistic skin texture" and "not a generic model face" gave over-processed skin and exaggerated faces.
_PHOTO = ("natural proportions, natural skin, shallow depth of field, candid editorial sports photography, "
          "no text, no logos, no badges")


def expression(facts: PortraitFacts, rng: random.Random) -> str:
    """His expression: from his character (the mental attributes), his age, and chance."""
    choices: list[tuple[float, tuple[str, ...]]] = [(1.0, _EXPRESSIONS)]
    for name, high, faces in _CHARACTER:
        value = (facts.attributes or {}).get(name)
        if value is not None:
            strength = (value - 12) if high else (8 - value)
            if strength > 0:
                choices.append((float(strength) ** 1.5, faces))
    age = facts.age or 25
    if age < 20:
        choices.append((3.0, _YOUNG_EXPRESSIONS))
    elif age >= 32:
        choices.append((2.5, _VETERAN_EXPRESSIONS))
    faces = rng.choices([faces for _, faces in choices], [weight for weight, _ in choices])[0]
    return rng.choice(faces)  # one expression: a second ("smiling with teeth showing") fought a "steely stare"
_EYE_COLOURS = {"light": ("blue", "grey", "green", "hazel", "light brown", "brown"), "dark": ("dark brown", "brown", "very dark brown", "hazel")}
_HAIR_TEXTURES = {  # by the save's ethnicity code: texture weights
    0: {"straight": 6, "wavy": 3, "curly": 1}, 1: {"straight": 4, "wavy": 4, "curly": 2}, 2: {"wavy": 4, "curly": 4, "straight": 2},
    3: {"coily": 8, "curly": 2}, 4: {"straight": 5, "wavy": 4, "curly": 1}, 5: {"straight": 7, "wavy": 3}, 6: {"wavy": 4, "curly": 5, "coily": 1},
    7: {"straight": 8, "wavy": 2}, 8: {"curly": 5, "wavy": 4, "coily": 1}, 9: {"curly": 4, "coily": 3, "wavy": 3}, 10: {"straight": 9, "wavy": 1},
}
_HAIRSTYLES = {  # (length, texture) -> styles; "*" for any texture
    ("short", "*"): ("a buzz cut", "a crew cut", "a skin fade with a short textured top", "a short side part", "a French crop with a short fringe",
                     "a short quiff", "short hair slicked back", "a high fade with a hard part line", "a short spiky cut"),
    ("short", "curly"): ("short tight curls on top with faded sides", "a curly crop"),
    ("short", "coily"): ("a high-top fade", "short twists with faded sides", "a close crop with waves", "a short afro fade",
                         "short dyed blond curls", "a close-cropped cut with a sharp line-up"),
    ("medium", "*"): ("messy textured hair", "a curtain fringe parted in the middle", "hair swept back", "a side-swept fringe",
                      "a modern mullet", "hair pushed up into a quiff"),
    ("medium", "wavy"): ("loose waves swept back", "wavy hair falling over the forehead"),
    ("medium", "curly"): ("a curly mop on top", "loose curls with short sides", "curly hair with bleached tips"),
    ("medium", "coily"): ("a medium afro", "twists", "short dreadlocks", "a sponge-twist top with a fade", "an afro with dyed tips"),
    ("long", "*"): ("shoulder-length hair tucked behind the ears", "long hair tied in a man bun", "long hair in a ponytail",
                    "long hair held back by a thin headband", "a half-up top knot"),
    ("long", "wavy"): ("long wavy hair", "long wavy hair with a centre parting"),
    ("long", "curly"): ("long curly hair", "long curls tied back"),
    ("long", "coily"): ("long dreadlocks tied back", "cornrows", "box braids pulled back", "a large afro", "long locs with bleached ends"),
}
_HAIR_COLOURS_BY_LOOK = {"light": ("dark brown", "brown", "light brown", "dirty blond", "blond", "black", "auburn", "ginger"),
                         "dark": ("black", "black", "very dark brown", "dark brown")}
_FACIAL_HAIR = ("", "", "", "light stubble", "heavy stubble", "a short neat beard", "a full thick beard", "a goatee", "a thin moustache",
                "a chin-strap beard", "a moustache and stubble", "a patchy thin beard", "designer stubble")
_BALD_HEADS = ("a clean-shaven head", "a closely shaved head", "a shaved head with light stubble")
_YOUNG_FACIAL_HAIR = ("", "", "", "", "faint peach fuzz on the upper lip", "a few wispy chin hairs")
_OLDER_STYLES = ("a short neat side parting", "short hair combed back", "a short tidy cut", "a classic short back and sides",
                 "a receding hairline with short hair", "thinning hair cut short", "hair swept back from a high forehead")
_OLDER_BALD = ("a bald crown with short hair at the sides", "a balding head with a horseshoe of short hair")


def _light(facts: PortraitFacts) -> bool:
    if facts.skin_tone:
        return facts.skin_tone <= 8
    return facts.ethnicity in (0, 1, 9, None)


def _weighted(rng: random.Random, weights: dict[str, int]) -> str:
    return rng.choices(list(weights), list(weights.values()))[0]


def appearance(facts: PortraitFacts, rng: random.Random) -> list[str]:
    """A face, hair, facial hair, build and expression for one player: varied, believable for his age and background."""
    # A few features, not all nine: given every one, FLUX draws each strongly and the face becomes a caricature (10 Nov 2026).
    features = [rng.choice(_FACES), rng.choice(_JAWS), rng.choice(_CHEEKS), rng.choice(_NOSES), rng.choice(_EYES),
                rng.choice(_BROWS), rng.choice(_LIPS), rng.choice(_EARS), rng.choice(_MARKS)]
    look = rng.sample([part for part in features if part], FACE_FEATURES)
    look.append(f"{rng.choice(_EYE_COLOURS['light' if _light(facts) else 'dark'])} eyes")
    age = facts.age or 25
    length = fm_save.HAIR_LENGTHS.get(facts.hair_length) or rng.choice(
        ("short", "short", "medium", "long") if age < 40 else ("short", "short", "short", "medium", "bald") if age < 55 else ("short", "short", "bald"))
    if length == "bald":
        look.append(rng.choice(_BALD_HEADS + (_OLDER_BALD if age >= 45 else ())))
    else:
        texture = _weighted(rng, _HAIR_TEXTURES.get(facts.ethnicity, {"straight": 4, "wavy": 3, "curly": 2, "coily": 1}))
        styles = (_OLDER_STYLES if age >= 40 else _HAIRSTYLES[(length, "*")] + _HAIRSTYLES.get((length, texture), ()))
        fair = _light(facts) and facts.ethnicity in (0, None)  # blond and ginger only for a Northern European look
        colour = _HAIR_COLOURS.get(facts.hair_colour) or rng.choice(_HAIR_COLOURS_BY_LOOK["light" if fair else "dark"])
        if age >= 62:
            colour = rng.choice(("white", "silver-grey", "grey"))
        elif age >= 50:
            colour = rng.choice(("grey", "salt-and-pepper", f"greying {colour}"))
        look.append(f"{texture} {colour} hair, {rng.choice(styles)}")
    look.append(rng.choice(_YOUNG_FACIAL_HAIR if age < 19 else _FACIAL_HAIR))
    if age >= 31:
        signs = ("fine lines around the eyes", "a few lines on the forehead") + (() if length == "bald" else ("a slightly receding hairline",))
        look.append(rng.choice(signs))
    if 34 <= age < 50 and length != "bald":
        look.append(rng.choice(("greying temples", "a few grey hairs", "salt-and-pepper stubble")))
    if age >= 50:
        look.append(rng.choice(("deep lines on the forehead and around the eyes", "a lined, lived-in face", "loose skin under the jaw",
                                "deep laugh lines")))
    if facts.height_cm and facts.weight_kg:
        bmi = facts.weight_kg / (facts.height_cm / 100) ** 2
        look.append("a stocky build with a thick neck" if bmi >= 25 else "a slim build with a slender neck" if bmi <= 21
                    else "an athletic build")
        if facts.height_cm >= 192:
            look.append("very tall with a long neck")
    look.append(expression(facts, rng))
    return [part for part in look if part]


# Staff are photographed as football staff are: the job decides the clothes and the place. (role words, outfits, places)
_STAFF_LOOKS = (
    (("Manager", "Assistant Manager"), "football manager",
     ("wearing a dark tailored suit, a white shirt and a plain dark tie", "wearing a navy suit with an open-collared white shirt",
      "wearing a charcoal suit, a light blue shirt and a plain tie", "wearing a dark suit and a long dark overcoat"),
     ("standing on the touchline of a football stadium, the stands softly blurred behind", "at a press conference, a plain backdrop behind",
      "in the tunnel of a football stadium, softly lit")),
    (("Director of Football", "Technical Director", "Loan Manager"), "football club director",
     ("wearing a dark suit without a tie", "wearing a tailored blazer over an open-collared shirt", "wearing a grey suit and a plain tie"),
     ("in a modern club office with large windows, softly blurred", "in a boardroom overlooking a stadium, softly blurred")),
    (("Coach", "Goalkeeping Coach", "Fitness Coach", "Set Piece Coach", "Head of Youth Development"), "football coach",
     ("wearing a plain training tracksuit top in the colours of {club}, without any logo or crest",
      "wearing a plain quarter-zip training top in the colours of {club}, a whistle around the neck, no logos",
      "wearing a plain padded coaching jacket in the colours of {club}, no logos"),
     ("on a training pitch, cones and a goal softly blurred behind", "at a football academy training ground, softly blurred")),
    (("Physio", "Head Physio", "Chief Doctor", "Sports Scientist", "Head of Sports Science"), "football club medical and sports science staff member",
     ("wearing a plain staff polo shirt in the colours of {club}, no logos", "wearing a plain zip-up staff top in the colours of {club}, no logos"),
     ("in a modern club medical room, softly blurred", "in a club gym with equipment softly blurred behind")),
    (("Scout", "Chief Scout", "Recruitment Analyst", "Performance Analyst", "Head Performance Analyst"), "football scout and analyst",
     ("wearing a smart casual jacket over a jumper", "wearing a dark padded jacket over a shirt", "wearing a quarter-zip jumper over a shirt"),
     ("in the stands of a football stadium, softly blurred", "in an analysis room with screens softly blurred behind")),
)


# Players are photographed as they are around a club, not all against one studio wall: the varied place, kit
# and light make each picture its own (the game's copy is cut out of it, so the face pack stays transparent).
_PLAYER_OUTFITS = ("wearing the home shirt of {club}, the team's match shirt in the club's colours, without any logo, crest or sponsor",
                   "wearing the short-sleeved home match shirt of {club} in the club's colours, no logos, crest or sponsor",
                   "wearing the match shirt of {club} in the club's colours, the collar visible, no logos, crest or sponsor")
_PLAYER_PLACES = ("on a training pitch, the grass and cones softly blurred behind", "in front of a plain media-day backdrop",
                  "in the tunnel of a football stadium", "pitch-side in an empty stadium, the stands softly blurred",
                  "at the training ground, the club building softly blurred behind", "in a club gym, softly blurred")
_LIGHTS = ("soft overcast daylight", "bright natural sunlight", "warm late-afternoon light", "even indoor light",
           "stadium floodlights in the evening", "cool morning light")


def build_staff_prompt(facts: PortraitFacts, rng: random.Random | None = None) -> str:
    """A press photograph of a staff member: dressed and placed for the job, chest-up, the face clear for the game's small frame."""
    rng = rng or random.Random(facts.uid)
    jobs, role, outfits, places = next((look for look in _STAFF_LOOKS if facts.job in look[0]), _STAFF_LOOKS[0])
    who = [f"one fictional male {role}"]
    if facts.ethnicity in _ETHNICITY_WORDS and not (facts.nationality and facts.ethnicity == facts.usual_ethnicity):
        who.append(f"{_ETHNICITY_WORDS[facts.ethnicity]} appearance")
    if facts.nationality:
        who.append(f"{facts.nationality} nationality")
    if facts.age:
        who.append(f"{facts.age} years old")
    looks = []
    if facts.skin_tone:
        looks.append("very light skin" if facts.skin_tone <= 4 else "light skin" if facts.skin_tone <= 8
                     else "medium skin" if facts.skin_tone <= 12 else "dark skin" if facts.skin_tone <= 16 else "very dark skin")
    club = facts.job_team or facts.club or "his club"
    return ", ".join([
        "Photorealistic professional sports press photograph of a football club staff member",
        *who, *looks, *appearance(facts, rng),
        rng.choice(outfits).format(club=club),
        rng.choice(places),
        rng.choice(_LIGHTS),
        "chest-up framing, the head in the upper middle of the frame and fully visible with a margin above the hair, body slightly "
        "turned with the face towards the camera, natural relaxed pose, " + _PHOTO,
    ])


def build_prompt(facts: PortraitFacts, rng: random.Random | None = None) -> str:
    if facts.job:
        return build_staff_prompt(facts, rng)
    rng = rng or random.Random(facts.uid)
    who = ["one fictional male footballer"]
    # The game's ethnicities are broad face groups: 1 covers Romanians, Italians, Greeks, Spaniards and
    # Argentines alike. Named for his nation's usual group, it gives a Romanian a Spanish face, so it is
    # named only when it sets him apart from his nation (a Swede of North African descent).
    if facts.ethnicity in _ETHNICITY_WORDS and not (facts.nationality and facts.ethnicity == facts.usual_ethnicity):
        who.append(f"{_ETHNICITY_WORDS[facts.ethnicity]} appearance")
    if facts.nationality:
        who.append(f"{facts.nationality} nationality")
    if facts.age:
        who.append(f"a {facts.age}-year-old teenager with a youthful face" if facts.age < 19 else f"{facts.age} years old")
    if facts.position in _POSITION_WORDS:
        who.append(f"a {_POSITION_WORDS[facts.position]}")
    looks = []
    if facts.skin_tone:
        looks.append("very light skin" if facts.skin_tone <= 4 else "light skin" if facts.skin_tone <= 8
                     else "medium skin" if facts.skin_tone <= 12 else "dark skin" if facts.skin_tone <= 16 else "very dark skin")
    club = facts.club or "his club"
    return ", ".join(
        [
            "Photorealistic professional sports photograph of a footballer",
            *who,
            *looks,
            *appearance(facts, rng),
            rng.choice(_PLAYER_OUTFITS).format(club=club),
            rng.choice(_PLAYER_PLACES),
            rng.choice(_LIGHTS),
            "chest-up framing, the head in the upper middle of the frame and fully visible with a margin above the hair, body "
            "slightly turned with the face towards the camera, natural relaxed pose, " + _PHOTO,
        ]
    )


def portrait_facts(world: sqlite3.Connection, uid: int, game_date: date) -> PortraitFacts:
    person = world.execute(
        "select p.name, p.birth_date, p.ethnicity, p.skin_tone, p.hair_colour, p.hair_length, n.adjective, p.nation_uid "
        "from people p left join nations n on n.fm_uid = p.nation_uid where p.fm_uid = ?",
        (uid,),
    ).fetchone()
    if person is None:
        raise PortraitError(f"No person with FM uid {uid} in the world database.")
    name, born, ethnicity, skin, hair_colour, hair_length, nationality, nation_uid = person
    usual = world.execute(
        "select ethnicity from people where nation_uid = ? and ethnicity is not null group by ethnicity order by count(*) desc limit 1",
        (nation_uid,),
    ).fetchone() if nation_uid else None
    if nationality is None and ethnicity is None:
        # Nothing of his background in the save (a retired player kept only for history): the nation
        # he played for (his goals and match reports), else his first club's country, as the game
        # makes its young players from the youth club's country.
        side = "case {0}.side when 0 then f.home_uid else f.away_uid end"
        found = world.execute(
            "select n.adjective, count(*) from (select " + side.format("g") + " team from goals g join fixtures f on f.id = g.fixture_id "
            "where g.scorer_uid = ? union all select " + side.format("m") + " from match_players m join fixtures f on f.id = m.fixture_id "
            "where m.person_uid = ?) x join teams t on t.fm_uid = x.team and t.club_uid is null join nations n on n.fm_uid = t.nation_uid "
            "group by n.fm_uid order by count(*) desc limit 1",
            (uid, uid),
        ).fetchone() or world.execute(
            "select n.adjective from player_seasons s join teams t on t.fm_uid = s.team_uid join clubs c on c.fm_uid = t.club_uid "
            "join nations n on n.fm_uid = c.nation_uid where s.person_uid = ? and s.kind != ? order by s.season_start limit 1",
            (uid, fm_save.INTERNATIONAL),
        ).fetchone()
        nationality = found[0] if found else None
    age = None
    if born:
        birth = date.fromisoformat(born)
        age = game_date.year - birth.year - ((game_date.month, game_date.day) < (birth.month, birth.day))
    # His main position: the game's highest position rating, else his best elevens' most common one.
    position = world.execute(
        "select key from player_snapshots ps join saves s on s.id = ps.save_id, json_each(ps.positions) "
        "where ps.person_uid = ? and ps.positions is not null order by s.game_date desc, value desc limit 1",
        (uid,),
    ).fetchone() or world.execute(
        "select position from best_elevens where person_uid = ? and position is not null group by position order by count(*) desc limit 1",
        (uid,),
    ).fetchone()
    # The club of his latest season: where he played the most club games that season (a loan
    # club only when he played more there); national-team games do not count.
    club = world.execute(
        "select t.name from player_seasons s join teams t on t.fm_uid = s.team_uid where s.person_uid = ? and s.kind != ? "
        "and t.club_uid is not null group by s.season_start, s.team_uid "
        "order by s.season_start desc, sum(s.starts + s.substitute_appearances) desc limit 1",
        (uid, fm_save.INTERNATIONAL),
    ).fetchone()
    body = world.execute("select ps.height_cm, ps.weight_kg, ps.attributes from player_snapshots ps join saves s on s.id = ps.save_id "
                         "where ps.person_uid = ? order by s.game_date desc limit 1",  # the newest save, not the last imported
                         (uid,)).fetchone()
    job = world.execute("select p.staff_job, t.name from people p left join teams t on t.fm_uid = p.staff_team_uid where p.fm_uid = ?",
                        (uid,)).fetchone()
    attributes = json.loads(body[2]) if body and body[2] else None
    return PortraitFacts(uid, name, age, ethnicity, skin, hair_colour, hair_length, nationality,
                         position[0] if position else None, club[0] if club else None,
                         body[0] if body else None, body[1] if body else None, attributes, usual[0] if usual else None,
                         job[0] if job and job[0] else None, job[1] if job and job[0] else None)


def _draw_portrait(prompt: str, seed: int) -> bytes:
    from app.services import picture_makers  # Draw Things with the model chosen on the Settings page, or InvokeAI

    return picture_makers.draw_portrait(prompt, seed)


# -- generating ------------------------------------------------------------------------------------
def _draw_things_url() -> str:
    from app.services import picture_makers

    return picture_makers.draw_things_url()


def _grpc() -> bool:
    from app.services import picture_makers

    return picture_makers.draw_things_grpc()


def draw(prompt: str, seed: int, url: str | None = None, timeout: int = 600, width: int = 1024, height: int = 1024,
         model: str | None = PORTRAIT_MODEL, negative: str | None = NEGATIVE_PROMPT) -> bytes:
    """One image from Draw Things (1024 x 1024 unless given), as PNG bytes. With ``model`` None the model selected in Draw
    Things draws it; its other settings (steps, guidance, sampler) stay as the user set them there."""
    url = url or _draw_things_url()
    if _grpc():
        from app.services import draw_things_grpc, picture_makers

        try:
            return draw_things_grpc.generate(url, prompt, model or picture_makers.draw_things_portrait_model(), seed, width, height,
                                             negative=negative or "", timeout=timeout)
        except draw_things_grpc.DrawThingsError as exc:
            raise PortraitError(str(exc)) from exc
    request = {"prompt": prompt, "seed": seed % 2_147_483_647, "width": width, "height": height}
    if model:
        request["model"] = model
    if negative:
        request["negative_prompt"] = negative
    return _request_image(f"{url}/sdapi/v1/txt2img", json.dumps(request).encode(), url, timeout)


def draw_edit(prompt: str, reference: bytes, seed: int, url: str | None = None, timeout: int = 1800, model: str = EDIT_MODEL,
              width: int = 1024, height: int = 1024) -> bytes:
    """A new picture from a reference picture and an instruction, with the editing model (the face is kept)."""
    url = url or _draw_things_url()
    if _grpc():
        from app.services import draw_things_grpc

        try:
            return draw_things_grpc.edit(url, prompt, model, reference, seed, width, height, negative=NEGATIVE_PROMPT, timeout=timeout)
        except draw_things_grpc.DrawThingsError as exc:
            raise PortraitError(str(exc)) from exc
    body = json.dumps({"prompt": prompt, "negative_prompt": NEGATIVE_PROMPT, "init_images": [base64.b64encode(reference).decode()],
                       "model": model, "steps": EDIT_STEPS, "strength": 1.0, "seed": seed % 2_147_483_647,
                       "width": width, "height": height}).encode()
    return _request_image(f"{url}/sdapi/v1/img2img", body, url, timeout)


def _request_image(endpoint: str, body: bytes, url: str, timeout: int) -> bytes:
    request = urllib.request.Request(endpoint, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            images = json.load(response).get("images") or []
    except OSError as exc:
        raise PortraitError(f"Draw Things did not answer at {url}: {exc}") from exc
    if not images:
        raise PortraitError("Draw Things returned no image.")
    return base64.b64decode(images[0])


def cut_out(png: bytes) -> bytes:
    """The player without the studio background (transparent), with macOS Vision's subject lifting.

    The game shows face-pack images as cut-outs. Without macOS Vision (another system), the
    image comes back unchanged.
    """
    try:
        import Quartz
        import Vision
        from Foundation import NSData
    except ImportError:
        log.warning("macOS Vision is not available; the portrait keeps its background.")
        return png
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(NSData.dataWithBytes_length_(png, len(png)), None)
    request = Vision.VNGenerateForegroundInstanceMaskRequest.alloc().init()
    ok, error = handler.performRequests_error_([request], None)
    if not ok or not request.results():
        raise PortraitError(f"No person found to cut out of the portrait: {error}")
    found = request.results()[0]
    buffer, error = found.generateMaskedImageOfInstances_fromRequestHandler_croppedToInstancesExtent_error_(
        found.allInstances(), handler, False, None
    )
    if buffer is None:
        raise PortraitError(f"The cut-out failed: {error}")
    context = Quartz.CIContext.contextWithOptions_(None)
    data = context.PNGRepresentationOfImage_format_colorSpace_options_(
        Quartz.CIImage.imageWithCVPixelBuffer_(buffer), Quartz.kCIFormatRGBA8, Quartz.CGColorSpaceCreateDeviceRGB(), None
    )
    return bytes(data)


def face_box(png: bytes) -> tuple[int, int, int, int] | None:
    """The largest face in the picture as (left, top, width, height) in pixels, with macOS Vision; None without one."""
    try:
        import Vision
        from Foundation import NSData
    except ImportError:
        return None
    from PIL import Image

    width, height = Image.open(io.BytesIO(png)).size
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(NSData.dataWithBytes_length_(png, len(png)), None)
    request = Vision.VNDetectFaceRectanglesRequest.alloc().init()
    ok, _ = handler.performRequests_error_([request], None)
    if not ok or not request.results():
        return None
    box = max((r.boundingBox() for r in request.results()), key=lambda b: b.size.width * b.size.height)
    w, h = box.size.width * width, box.size.height * height
    return round(box.origin.x * width), round((1 - box.origin.y) * height - h), round(w), round(h)


# How a portrait is cropped, as face packs are: a square of head and shoulders, the face centred across, a small margin
# above the hair. Measured in face heights (Vision's face box runs from the brows to the chin).
FRAME_FACES, FRAME_ABOVE = 2.5, 0.75


def framed(png: bytes) -> tuple[int, int, int, int] | None:
    """The square to crop a portrait to so he fills it (left, top, right, bottom), or None to keep the whole picture."""
    from PIL import Image

    face = face_box(png)
    if face is None:
        return None
    width, height = Image.open(io.BytesIO(png)).size
    left, top, w, h = face
    side = min(round(h * FRAME_FACES), width, height)
    if side >= 0.92 * min(width, height):  # he fills the picture already
        return None
    x = min(max(round(left + w / 2 - side / 2), 0), width - side)
    y = min(max(round(top - h * FRAME_ABOVE), 0), height - side)
    return x, y, x + side, y + side


def save_portrait(uid: int, png: bytes, faces_dir: Path = FACES_DIR) -> Path:
    """Keep the photograph, with its background, as the archive's 512 px picture, and the person cut out of
    it (a transparent background, as face packs are) as the game's 250 px face."""
    from PIL import Image

    crop = framed(png)
    if crop:  # he stands small in the picture: crop it round him, so he fills the game's frame
        buffer = io.BytesIO()
        Image.open(io.BytesIO(png)).convert("RGB").crop(crop).save(buffer, format="PNG")
        png = buffer.getvalue()
    photo = Image.open(io.BytesIO(png)).convert("RGB")
    ARCHIVE_FACES.mkdir(parents=True, exist_ok=True)
    archive_copy = ARCHIVE_FACES / f"{uid}.png"
    photo.resize((ARCHIVE_SIZE, ARCHIVE_SIZE), Image.LANCZOS).save(archive_copy, optimize=True)
    if faces_dir.is_dir():
        cut = Image.open(io.BytesIO(cut_out(png))).convert("RGBA").resize((GAME_SIZE, GAME_SIZE), Image.LANCZOS)
        game_folder = faces_dir / GENERATED_FOLDER
        theirs = face_pack_index(faces_dir).get(uid)
        if theirs is not None and theirs.parent.name != GENERATED_FOLDER:
            # his face is in the user's own face pack: the game shows that one, so the new picture goes over it (Chiriță's
            # new picture never reached the game, 2 Nov 2026), its original kept once in the archive's backups
            back_up_face(theirs, faces_dir)
            if theirs.suffix.lower() in (".jpg", ".jpeg"):
                photo.resize((GAME_SIZE, GAME_SIZE), Image.LANCZOS).save(theirs, format="JPEG", quality=92)
            else:
                cut.save(theirs, format="PNG", optimize=True)
            ours = game_folder / f"{uid}.png"
            if ours.is_file():  # one picture of him for the game: no second claim from our folder
                ours.unlink()
                write_face_config(game_folder)
        else:
            game_folder.mkdir(exist_ok=True)
            cut.save(game_folder / f"{uid}.png", optimize=True)
            write_face_config(game_folder)
    return archive_copy


def own_picture(data: bytes) -> bytes:
    """A picture the user made elsewhere (PNG, JPEG, WebP, any size), as a square PNG for ``save_portrait``: a
    transparent one is put on grey first, and a tall or wide one is cropped square round the face (the top centre
    without one), so it is not squashed into the game's square frame."""
    from PIL import Image, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise PortraitError("That file is not a picture (use PNG, JPEG or WebP).") from exc
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        grey = Image.new("RGBA", image.size, (128, 128, 128, 255))
        image = Image.alpha_composite(grey, image)
    image = image.convert("RGB")
    width, height = image.size
    if width != height:
        side = min(width, height)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        face = face_box(buffer.getvalue())
        centre_x, centre_y = (face[0] + face[2] / 2, face[1] + face[3] / 2) if face else (width / 2, side / 2)
        left = min(max(round(centre_x - side / 2), 0), width - side)
        top = min(max(round(centre_y - side / 2), 0), height - side)
        image = image.crop((left, top, left + side, top + side))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def use_own_picture(uid: int, data: bytes, faces_dir: Path = FACES_DIR) -> Path:
    """The user's own picture as his picture: kept in the archive and cut out (transparent) into the game's face pack."""
    return save_portrait(uid, own_picture(data), faces_dir)


def face_backup_path(face: Path, faces_dir: Path | None = None) -> Path:
    return FACE_PACK_BACKUPS / face.relative_to(faces_dir or FACES_DIR)


def back_up_face(face: Path, faces_dir: Path | None = None) -> Path:
    """The face pack's own picture, copied once before the archive writes over it; a later picture keeps the first backup,
    the pack's original."""
    backup = face_backup_path(face, faces_dir)
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(face, backup)
    return backup


def write_face_config(game_folder: Path) -> None:
    """A config.xml that maps every image in our folder to its person (file name = FM uid).

    The game finds faces in any folder under ``graphics`` that has a config.xml; ``r-`` before
    the uid is the standard form in face packs.
    """
    records = "\n".join(
        f'    <record from="{image.stem}" to="graphics/pictures/person/r-{image.stem}/portrait" />'
        for image in sorted(game_folder.glob("*.png"))
        if image.stem.isdigit()
    )
    game_folder.joinpath("config.xml").write_text(
        "<?xml version='1.0' encoding='utf-8'?>\n<record>\n"
        '  <boolean id="preload" value="false" />\n  <boolean id="amap" value="false" />\n'
        f'  <list id="maps">\n{records}\n  </list>\n</record>\n',
        encoding="utf-8",
    )


def generate_one(world_db: Path, uid: int, replace: bool = False) -> Path:
    """Make one person's portrait (the player page's "Generate picture" button).

    Without ``replace``, a person who already has a picture (a game face, an old portrait or a
    generated one) keeps it.
    """
    if not replace:
        existing = picture_of(uid, face_pack_index(), old_portraits())
        if existing is not None:
            return existing
    world = sqlite3.connect(f"file:{world_db}?mode=ro", uri=True)
    game_date = date.fromisoformat(world.execute("select max(game_date) from saves").fetchone()[0])
    facts = portrait_facts(world, uid, game_date)
    if replace:  # "New picture": a new look and a new image, not the same face again
        seed = random.SystemRandom().randrange(2**31)
        return save_portrait(uid, _draw_portrait(build_prompt(facts, random.Random(seed)), seed))
    return save_portrait(uid, _draw_portrait(build_prompt(facts), uid))


# -- action photos ---------------------------------------------------------------------------------
# A new photograph of a player in a match, made from his current picture by the editing model, so it is his face.
# A photo is a moment (of his position, of one of his goals, a celebration, or before and after a game), a camera
# position and the conditions: 17 to 41 moments for a player (by his position, and whether he has scored), 12 cameras
# and 12 conditions, so two photos of one player rarely look alike. They used to be knee slides and kneeling keepers
# from one telephoto view.
_ACTIONS = {  # by the first letters of his main position
    "GK": ("diving full stretch to his left, fingertips pushing the ball around the post",
           "flying horizontally to his right to palm away a curling shot, the ball an inch from his glove",
           "arching backwards to tip a dipping shot over the crossbar",
           "punching the ball clear above a crowd of jumping players at a corner",
           "spreading himself wide to block a striker's shot in a one-on-one",
           "diving low to his left to save a penalty",
           "catching a high cross cleanly at full stretch, one knee raised",
           "throwing himself across the goal to claw the ball off the line",
           "roaring with clenched fists after a save, teammates rushing to him",
           "shouting instructions to his defenders, arms spread wide",
           "rolling the ball out to a defender to start an attack",
           "launching a long goal kick, his body twisted with the effort",
           "standing on his line, crouched and ready, as a striker lines up a free kick"),
    "D": ("sliding in to win the ball with a perfectly timed tackle", "rising above a striker to head the ball clear",
          "throwing himself in front of a shot to block it", "striding out of defence with the ball at his feet",
          "heading the ball into the net from a corner, defenders scattered", "clearing the ball off the goal line with an overhead kick",
          "jockeying a winger near the touchline, low and balanced", "wrestling with a striker for position at a free kick",
          "hooking the ball away from an attacker's feet", "pointing and organising the defensive line",
          "playing a long diagonal pass out of defence", "arguing with the referee, palms open"),
    "WB": ("sprinting down the wing with the ball, the touchline at his feet", "whipping in a cross from the byline",
           "racing back to make a last-ditch sliding tackle", "cutting inside past a full-back", "overlapping a teammate at full speed",
           "taking a long throw-in, the ball behind his head", "volleying a cross first time from the edge of the box"),
    "DM": ("winning the ball back with a crunching tackle in midfield", "spraying a long pass across the pitch",
           "shielding the ball from two opponents", "intercepting a pass, stretching out a leg",
           "heading the ball away in a crowded midfield", "turning sharply away from a pressing opponent",
           "striking a shot from thirty yards, the ball leaving his boot"),
    "M": ("driving forward with the ball at his feet, an opponent chasing", "playing a pass with the outside of his boot",
          "controlling a high ball on his chest", "hitting a volley from the edge of the area", "taking a corner kick, arm raised",
          "spinning away from his marker in midfield", "curling a free kick over the wall", "leaping for a header in midfield"),
    "AM": ("dribbling past a defender at speed, the defender off balance", "curling a shot towards the top corner",
           "threading a pass through the defence", "taking a free kick, the wall jumping in front of him",
           "flicking the ball over a defender's head", "skipping past a sliding tackle", "chipping the goalkeeper from the edge of the box",
           "drilling a low shot past the goalkeeper"),
    "ST": ("striking the ball hard towards goal", "rising above two defenders to head the ball towards goal",
           "turning away from his marker with the ball", "sprinting through on goal, the defence left behind",
           "volleying the ball into the net, both feet off the ground", "attempting a bicycle kick in a crowded penalty area",
           "rounding the goalkeeper with the ball", "sliding in at the far post to turn the ball into the net",
           "taking a penalty, the goalkeeper diving the wrong way", "holding off a defender with his back to goal",
           "shooting low past the goalkeeper's outstretched hand"),
}
_SCORING = ("striking the ball into the top corner, the goalkeeper diving in vain", "heading the ball past the goalkeeper into the net",
            "sliding the ball under the goalkeeper", "volleying the ball into the roof of the net",
            "turning in a cross from close range, the net bulging", "scoring a penalty, sending the goalkeeper the wrong way",
            "curling a shot around the defender and in off the post", "chipping the ball over the goalkeeper into the net")
GOAL_CELEBRATIONS = ("running towards the fans with his arms spread wide, shouting with joy", "leaping high in the air and punching the sky",
                     "cupping his ear to the crowd after scoring", "buried under a pile of celebrating teammates",
                     "jumping into the arms of a teammate", "running away with one finger raised, mouth open in a roar",
                     "standing still with his arms folded, a calm smile, as teammates run to him",
                     "kissing his shirt in front of the fans", "screaming with joy towards the camera, veins in his neck",
                     "sliding on his knees in celebration", "high-fiving the fans in the front row behind the goal",
                     "pointing at the teammate who made the goal")
_AROUND = ("walking out of the tunnel before the match, focused, the noise of the crowd around him",
           "lined up with his teammates before kick-off, hand on his heart",
           "warming up before the match, jogging along the touchline", "applauding the travelling fans at the final whistle",
           "sitting on the pitch exhausted after the final whistle, head tilted back", "talking to his manager on the touchline",
           "pulling on his shirt in the dressing room before the match", "walking off the pitch at half-time, deep in thought",
           "wiping sweat from his face during a break in play", "standing in the rain waiting for a corner, breath steaming")
# Every camera stands well back: the whole man in the frame with pitch around him, never a close-up (25 Oct 2026: the
# first photos were "quite in your face, literally")
_CAMERAS = ("shot from pitch level with a long lens from thirty metres, slightly low angle",
            "wide shot from the touchline, the pitch and the crowd in view, several players in the frame",
            "side-on shot from the touchline at a distance, panning with him so the background streaks",
            "shot from behind the goal, through the netting, the whole penalty area in view",
            "over-the-shoulder shot from behind an opponent, both men full-length",
            "three-quarter view from a photographer's spot beside the corner flag, the far stand behind",
            "full-length shot frozen at the peak of the movement, turf and water spraying, space around him",
            "shot from the photographers' pit, the stand rising behind him, the goal in the frame",
            "shot from a low camera at the side of the goal, at grass height, the six-yard box in view",
            "shot from high in the stand, the pitch markings and the shadows of the players on the grass")
_DISTANCE = "his whole body in the frame with pitch around him, no larger than a third of the picture's height, no close-up of his face"
_SCENES = ("a night match under floodlights in a packed stadium", "an afternoon match in bright sunshine, the stands full",
           "a match in heavy rain, the pitch wet and shining", "an evening match, the crowd a blur of colour behind him",
           "golden hour, low sun casting long shadows across the pitch", "a cup tie in an old, tightly packed ground",
           "a European night, flares glowing in the away end", "a muddy pitch on a grey afternoon")
_WINTER_SCENES = ("a winter match in falling snow, the pitch lines cleared", "a foggy night, the floodlights haloed in mist",
                  "a cold, clear winter afternoon, breath visible")  # November to March
_SUMMER_SCENES = ("a summer evening, the sky still bright above the stadium",)  # May to September
WINTER, SUMMER = {11, 12, 1, 2, 3}, {5, 6, 7, 8, 9}


@dataclass(frozen=True)
class ActionPhoto:
    prompt: str
    caption: str


def _when(day) -> str:
    day = date.fromisoformat(str(day)[:10])
    return f"{day.day} {day:%B %Y}"


def action_photo_plan(world: sqlite3.Connection, uid: int, facts: PortraitFacts, rng: random.Random) -> ActionPhoto:
    """What the action photo shows. A player who has scored: a fifth of the time the goal itself, a fifth its
    celebration, both named in the caption; otherwise (and always for a keeper or a player without goals) mostly an
    action of his position in one of his recent matches, named when the save has it, and now and then a moment before
    or after a game. Seen from one of the cameras, in one of the conditions."""
    goals = world.execute(
        "select f.date, c.name, case g.side when 0 then ta.name else th.name end opponent from goals g join fixtures f on f.id = g.fixture_id "
        "left join competitions c on c.fm_uid = f.competition_uid left join teams th on th.fm_uid = f.home_uid "
        "left join teams ta on ta.fm_uid = f.away_uid where g.scorer_uid = ? and g.kind in (0, 2) "
        "and c.scope in ('league', 'cup', 'continental') order by f.date desc limit 40", (uid,)).fetchall()
    try:
        matches = world.execute(
            "select f.date, c.name, case m.side when 0 then ta.name else th.name end opponent from match_players m "
            "join fixtures f on f.id = m.fixture_id left join competitions c on c.fm_uid = f.competition_uid "
            "left join teams th on th.fm_uid = f.home_uid left join teams ta on ta.fm_uid = f.away_uid "
                "where m.person_uid = ? and m.played = 1 and c.scope in ('league', 'cup', 'continental') order by f.date desc limit 20",
            (uid,)).fetchall()
    except sqlite3.OperationalError:  # a world without match reports
        matches = []
    kit = f"wearing a plain football kit in the colours of {facts.club}, without any logo, crest, badge or sponsor" if facts.club \
        else "wearing a plain football kit without logos"
    key = next((k for k in ("GK", "WB", "DM", "AM", "ST", "D", "M") if (facts.position or "").startswith(k)), "M")
    roll = rng.random()
    opponent, day = None, None
    if goals and key != "GK" and roll < 0.4:
        day, competition, opponent = rng.choice(goals)
        scoring = roll < 0.2
        action = rng.choice(_SCORING if scoring else GOAL_CELEBRATIONS)
        caption = (f"{'Scoring' if scoring else 'Celebrating his goal'} against {opponent}" + (f", {competition}" if competition else "")
                   + f", {_when(day)}")
    elif roll > 0.85:
        action = rng.choice(_AROUND)
        caption = f"Matchday with {facts.club}" if facts.club else "Matchday"
    else:
        action = rng.choice(_ACTIONS[key])
        if matches:
            day, competition, opponent = rng.choice(matches[:8])
            caption = f"In action against {opponent}" + (f", {competition}" if competition else "") + f", {_when(day)}"
        else:
            caption = f"In action for {facts.club}" if facts.club else "In action"
    rival = f", the opponents in the colours of {opponent}" if opponent else ""
    month = date.fromisoformat(str(day)[:10]).month if day else None
    scenes = _SCENES + (_WINTER_SCENES if month in WINTER or month is None else ()) + (_SUMMER_SCENES if month in SUMMER or month is None else ())
    prompt = ", ".join([
        "Use the man in the picture as the reference for his face, hair, skin and build, and keep him recognisably the same person",
        f"show him as a footballer in {rng.choice(scenes)}, {action}{rival}", kit,
        f"{rng.choice(_CAMERAS)}, {_DISTANCE}, editorial sports photography, photorealistic, natural motion, sharp focus on him, "
        "no text, no logos",
    ])
    return ActionPhoto(prompt, caption)


def reference_picture(uid: int, room: float | None = None, size: tuple[int, int] = (1024, 1024)) -> bytes:
    """His current picture on a neutral grey background (a cut-out has none), at ``size``, as PNG bytes. With ``room``
    (the age-up, the action photos) he is cut out of his picture and shrunk to that share of the frame's height, standing
    at the bottom centre: a face pack's tight crop would otherwise give the new picture a huge head on a squat body."""
    from PIL import Image

    path = picture_of(uid, face_pack_index(), old_portraits())
    if path is None:
        raise PortraitError("This player has no picture yet: make his portrait first.")
    picture = Image.open(path).convert("RGBA")
    if room:
        if picture.getextrema()[3][0] == 255:  # a photograph with its background: cut him out of it
            picture = Image.open(io.BytesIO(cut_out(path.read_bytes()))).convert("RGBA")
        # Measure the man, not the picture: his head and shoulders (the top of the cut-out, as tall as it is wide) are
        # scaled to ``room`` of the frame's height, so a tight face crop and a full-length photo start the same.
        box = picture.getchannel("A").point(lambda a: 255 if a > 32 else 0).getbbox() or (0, 0, *picture.size)
        left, top, right, bottom = box
        bottom = min(bottom, top + round((right - left) * 1.1))
        upper = picture.crop((left, top, right, bottom))
        scale = min(size[1] * room / upper.height, size[0] * 0.9 / upper.width)
        upper = upper.resize((max(1, round(upper.width * scale)), max(1, round(upper.height * scale))), Image.LANCZOS)
        canvas = Image.new("RGBA", size, (128, 128, 128, 255))
        canvas.alpha_composite(upper, ((size[0] - upper.width) // 2, size[1] - upper.height))
    else:
        canvas = Image.new("RGBA", picture.size, (128, 128, 128, 255))
        canvas.alpha_composite(picture)
    buffer = io.BytesIO()
    canvas.convert("RGB").resize(size, Image.LANCZOS).save(buffer, format="PNG")
    return buffer.getvalue()


def make_action_photo(world_db: Path, uid: int) -> tuple[bytes, str]:
    """A new action photograph of a player from his current picture, and its caption."""
    world = sqlite3.connect(f"file:{world_db}?mode=ro", uri=True)
    game_date = date.fromisoformat(world.execute("select max(game_date) from saves").fetchone()[0])
    facts = portrait_facts(world, uid, game_date)
    seed = random.SystemRandom().randrange(2**31)
    plan = action_photo_plan(world, uid, facts, random.Random(seed))
    from app.services import picture_makers  # the chosen maker: Draw Things' editing model, or WaveSpeed's

    return picture_makers.edit(plan.prompt, reference_picture(uid, room=ACTION_ROOM, size=ACTION_SIZE), seed, *ACTION_SIZE), plan.caption



# The age-up's background: the picture is often a face-pack cut-out, put on grey for the edit, so the grey is replaced
# with the training ground.
# Action photos: a 3:2 landscape frame (a square one squeezed the whole body under the portrait's big head), and his
# portrait small in the reference so the model draws him at a natural size.
ACTION_SIZE = (1216, 832)
ACTION_ROOM = 0.45
AGE_UP_ROOM = 0.6  # his head and shoulders as a share of the frame height in the edit's reference picture
_AGE_UP_PLACES = ("on a training pitch, the grass and cones softly blurred behind",
                  "at the training ground, the club building softly blurred behind",
                  "at the training ground, the training pitches and goals softly blurred behind")


def age_up_prompt(facts: PortraitFacts, rng: random.Random | None = None) -> str:
    """The edit that brings his picture up to date: his age now and the shirt of his club now, the same man and framing."""
    if facts.job:
        team = facts.job_team or facts.club
        clothes = f"dress him as a {facts.job.lower()} of {team}: a plain staff top in the colours of {team}, no logos" if team \
            else "keep his clothes"
    else:
        clothes = (f"change his top to the home match shirt of {facts.club}, the team's shirt in the club's colours, without any logo, "
                   "crest or sponsor") if facts.club else "keep his shirt"
    rng = rng or random.Random(facts.uid)
    decade = {2: "twenties", 3: "thirties", 4: "forties", 5: "fifties"}.get((facts.age or 0) // 10)
    age = (f"make him look {facts.age} years old, ageing his face naturally if he looks younger in the picture "
           f"(fuller face, stronger jaw, the skin of a man of {facts.age}"
           + (f"; a mature man in his {decade} with lines around the eyes" if facts.age >= 28 and decade else "") + ")") \
        if facts.age else "keep his age"
    return ", ".join([
        "Keep this exact man: the same face, identity, skin tone, eye colour, hair colour and hairline, recognisably the same person",
        age, clothes,
        f"replace the background: photograph him {rng.choice(_AGE_UP_PLACES)}, in {rng.choice(_LIGHTS)}",
        "keep the camera distance of the picture: head and shoulders with the upper chest, the head about a quarter of the frame's "
        "height, a clear margin above the hair, the face towards the camera, photorealistic sports portrait, natural skin, shallow depth of field, no text, no logos",
    ])


def age_up(world_db: Path, uid: int) -> tuple[Path, str]:
    """His picture brought up to date (the player page's "Age up / change shirt" button): a new portrait from the current
    one with the chosen maker's editing model, kept as his picture. Returns the picture and what it shows."""
    world = sqlite3.connect(f"file:{world_db}?mode=ro", uri=True)
    game_date = date.fromisoformat(world.execute("select max(game_date) from saves").fetchone()[0])
    facts = portrait_facts(world, uid, game_date)
    seed = random.SystemRandom().randrange(2**31)
    from app.services import picture_makers  # the chosen maker's editing model

    picture = save_portrait(uid, picture_makers.edit(age_up_prompt(facts, random.Random(seed)), reference_picture(uid, room=AGE_UP_ROOM), seed))
    what = ", ".join(x for x in (f"aged {facts.age}" if facts.age else "", f"in the shirt of {facts.job_team or facts.club}"
                                 if (facts.job_team or facts.club) else "") if x)
    return picture, what or "brought up to date"


def squad_without_pictures(world: sqlite3.Connection, team_uid: int, season: int | None = None) -> list[int]:
    """The players of a team's season (its latest season when none is given) who have no picture."""
    if season is None:
        row = world.execute("select max(season_start) from player_seasons where team_uid = ?", (team_uid,)).fetchone()
        season = row[0]
    if season is None:
        return []
    faces, old = face_pack_index(), old_portraits()
    squad = world.execute(
        "select person_uid from player_seasons where team_uid = ? and season_start = ? group by person_uid "
        "order by sum(starts + substitute_appearances) desc",
        (team_uid, season),
    )
    return [uid for (uid,) in squad if picture_of(uid, faces, old) is None]


LOCAL_MAKERS = {"draw_things": "Draw Things", "invoke": "InvokeAI"}  # the makers a league's batch may run on: no cost per picture


def league_without_pictures(world: sqlite3.Connection, league_uid: int, faces: dict[int, Path] | None = None,
                            old: dict[int, Path] | None = None) -> list[tuple[int, int]]:
    """The active first-team players of a league's clubs who have no picture, as (person, team), most games first."""
    return leagues_without_pictures(world, faces, old, league_uid).get(league_uid, {}).get("missing", [])


def leagues_without_pictures(world: sqlite3.Connection, faces: dict[int, Path] | None = None, old: dict[int, Path] | None = None,
                             league_uid: int | None = None) -> dict[int, dict]:
    """For each league (or the one given): {"players": n, "missing": [(person, team), ...]}. The players are those with
    league games in the league's newest season that is under way (at least half as many players with games as its
    fullest season), still on that club's books (not a player who left, not
    staff), most games first. Not simply the newest: a season just begun in the save has rows but (nearly) no games, and Argentina's 2064 showed "every one has a picture" with nobody in it (11 Nov 2026)."""
    faces = face_pack_index() if faces is None else faces
    old = old_portraits() if old is None else old
    one = "and league_uid = ?" if league_uid is not None else ""
    rows = world.execute(
        "with counts as (select league_uid, season_start, count(*) n from player_seasons where kind = 1 and league_uid is not null "
        f"{one} and starts + substitute_appearances > 0 group by league_uid, season_start), "
        "seasons as (select c.league_uid, max(c.season_start) season from counts c "
        "where c.n * 2 >= (select max(n) from counts m where m.league_uid = c.league_uid) group by c.league_uid) "
        "select ps.league_uid, ps.person_uid, ps.team_uid, sum(ps.starts + ps.substitute_appearances) games from player_seasons ps "
        "join seasons s on s.league_uid = ps.league_uid and s.season = ps.season_start "
        "join people p on p.fm_uid = ps.person_uid and p.team_uid = ps.team_uid where ps.kind = 1 and p.staff_job is null "
        "group by ps.league_uid, ps.person_uid, ps.team_uid having games > 0 order by ps.league_uid, games desc",
        (league_uid,) if league_uid is not None else ())
    found: dict[int, dict] = {}
    for league, uid, team, _ in rows:
        entry = found.setdefault(league, {"players": 0, "missing": []})
        entry["players"] += 1
        if picture_of(uid, faces, old) is None:
            entry["missing"].append((uid, team))
    return found


def generate_team(
    world_db: Path, team_uid: int, season: int | None = None, progress: Callable[[str], None] = log.info
) -> int:
    """Make a portrait for every player of a team's season who has none (the club-season page's button)."""
    world = sqlite3.connect(f"file:{world_db}?mode=ro", uri=True)
    game_date = date.fromisoformat(world.execute("select max(game_date) from saves").fetchone()[0])
    made = 0
    for uid in squad_without_pictures(world, team_uid, season):
        facts = portrait_facts(world, uid, game_date)
        save_portrait(uid, _draw_portrait(build_prompt(facts), uid))
        made += 1
        progress(f"{made}: {facts.name}")
    return made
