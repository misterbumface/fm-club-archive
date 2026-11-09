"""Pictures for the outlets' articles (docs/media-plan.md).

An article shows, in this order: its own picture (an illustration or one the curator chose), a
picture of its subject from the archive (a stadium, a photograph, a portrait), the player's face,
the trophy, and only then the club's crest, as a small badge. On the Desk, the curator can have Draw
Things make an illustration for the lead story of each edition, in the team's colours: never after an
import by itself, as it takes minutes.

Each kind of story has several scenes, and each picture a light and a camera view of its own, from a new
seed every time: one fixed scene per kind gave nearly the same picture in every cycle.
"""

from __future__ import annotations

import io
import logging
import random
import re
import urllib.request

from app.extensions import db
from app.models.world_soccer_article import WorldSoccerArticle
from app.models.world_soccer_issue import WorldSoccerIssue
from app.services import world_portraits
from config import GENERATED_DIR, GENERATED_URL

log = logging.getLogger(__name__)

ILLUSTRATIONS = GENERATED_DIR / "illustrations"
ILLUSTRATION_URL = f"{GENERATED_URL}/illustrations"
WIDTH, HEIGHT = 1344, 768  # landscape, for a front page
SAVED_WIDTH = 1200

# One plain sentence of style, the way the newer models (Z-Image, FLUX, Qwen-Image) read a prompt: they draw what is named,
# so nothing is named that must not appear (no "no helmets"); "soccer" because "football" alone reads as the American game.
_STYLE = ("A press photograph from a soccer match, full frame: natural colour, a real stadium with its advertising boards, "
          "real players in sponsored kits as at a real match, sharp on the subject with the background softly out of focus. "
          "One single photograph of one moment, the whole image one continuous frame from edge to edge.")
_TROPHY = ("soccer players lifting a silver trophy under floodlights, ticker tape and confetti in the air, a packed stadium",
           "a captain kissing a silver trophy, team-mates jumping behind him, streamers falling",
           "an open-top bus parade through crowded city streets, a silver trophy held high, flares and scarves",
           "players in a dressing room spraying champagne, a trophy on the bench, steam and shouting")
_STRIKE = ("a striker's boot striking a soccer ball, close-up, spray of water from the wet grass",
           "a striker wheeling away in celebration, arms wide, the crowd behind on its feet",
           "a soccer ball hitting the back of the net, the net bulging, motion blur, a roaring crowd behind")
_OLD = ("an old soccer stadium at golden hour, empty seats, nostalgic film grain",
        "a faded black-and-white team photograph pinned to a wooden dressing-room wall",
        "an old pair of soccer boots and a folded shirt on a bench, warm window light")
_SCENES: dict[str, tuple[str, ...]] = {
    "title": _TROPHY,
    "title_clinched": _TROPHY,
    "great_final": ("a cup final at night, players celebrating at the final whistle, fireworks over the stadium roof",
                    "a goalkeeper diving full-length in a penalty shoot-out, a packed stadium holding its breath",
                    "two teams lined up before a final, a trophy on a plinth in front of them, smoke and flags"),
    "dynasty": ("a long row of silver trophies in a dim club museum, one spotlight, an empty stadium seen through the window",
                "a trophy cabinet crowded with silver cups, a child's face reflected in the glass",
                *_TROPHY[:2]),
    "giant_killing": ("underdog soccer players celebrating wildly in front of their small travelling crowd, rain, floodlights",
                      "fans of a small club invading a muddy pitch at the final whistle, hugging the players",
                      "a little ground with one small stand, its fans going wild behind a goal, a big club's players stunned"),
    "big_win": _STRIKE,
    "rise": ("a small soccer ground packed to the rafters on a cold evening, flares and scarves held high",
             "players and fans celebrating promotion together on the pitch, smoke flares, the setting sun",
             "a crowd of supporters singing in a terrace, scarves raised, breath steaming in the cold"),
    "fall": ("a lone soccer player kneeling on a wet pitch after the final whistle, empty seats behind, grey light",
             "fans with their heads in their hands in a half-empty stand, rain falling",
             "players walking off slowly, heads down, past an empty dugout, the floodlights going off"),
    "transfer": ("a new soccer shirt hanging in a dressing room locker, camera flashes reflected on the tiles",
                 "a new signing holding up his new shirt in front of the stadium, photographers below",
                 "a contract and a pen on a polished boardroom table, the pitch seen through the window",
                 "a player walking out onto an empty pitch for the first time, looking up at the stands"),
    "spending": ("a row of new soccer shirts hanging in an empty dressing room, bright lights, polished floor",
                 "a busy press room with cameras and microphones waiting for a new signing",
                 "a club chairman's empty leather chair before a window over the stadium, dusk"),
    "rising_star": ("a young soccer player silhouetted against the floodlights, walking out of the tunnel",
                    "a teenage player dribbling past a defender, the ball at his feet, a blurred crowd",
                    "a young player applauding the fans after a match, his face lit by the floodlights"),
    "scouting": ("a scout's notebook and binoculars on a cold stand above a youth soccer match at dusk, soccer goals with nets",
                 "a lone scout in a long coat watching a youth match from a muddy touchline",
                 "a laptop with match data and a cup of coffee in a scout's car, a floodlit pitch seen through the windscreen"),
    "academy": ("teenage soccer players training on a misty academy pitch at dawn, cones and balls on the grass",
                "a coach talking to a circle of young players on a training pitch, balls at their feet",
                "young players running sprints between cones in the rain, an academy building behind"),
    "award": ("a golden ball award on a pedestal under a spotlight, a dark auditorium behind",
              "a player in a dark suit holding a golden award on a stage, camera flashes",
              "a row of glittering awards on a table at a gala dinner, soft candle light"),
    "milestone": _STRIKE,
    "club_record": _STRIKE,
    "career_end": ("an old pair of soccer boots hanging on a peg in an empty dressing room, warm evening light",
                   "a veteran player waving goodbye to a standing crowd, a guard of honour of both teams",
                   "a player alone in the centre circle of an empty stadium at dusk, looking around"),
    "legend": _OLD,
    "legend_profile": _OLD,
    "where_now": ("a coach in a tracksuit watching young players on a training pitch at sunset",
                  "a former player in a suit in a stadium's executive box, looking down at the pitch",
                  "a coach with a whistle and a clipboard on a muddy training pitch, players behind"),
    "appointment": ("an empty dugout and technical area before a match, the stadium filling up, evening light",
                    "a new manager in a suit holding up a scarf in an empty stadium, photographers below",
                    "a manager on the touchline pointing and shouting instructions, players blurred behind"),
    "anniversary": ("a faded old soccer programme and a scarf on a wooden table, soft window light", *_OLD[:2]),
    "all_time": ("a wall of framed old soccer photographs in a dark corridor, one lit by a spotlight", *_OLD[1:]),
}
_DEFAULT_SCENES = ("a soccer stadium under floodlights on a big match night, a packed crowd",
                   "two players challenging for a header, the ball between them, a packed stand behind",
                   "a crowd of supporters with scarves and flags outside a stadium before kick-off")
_LIGHTS = ("dramatic floodlights at night", "low golden evening sun", "grey drizzle and wet reflections",
           "a crisp cold winter afternoon", "bright midday sun and hard shadows", "mist and soft diffused light",
           "a stormy sky over the stadium", "warm spring light")
_VIEWS = ("35mm lens, close to the action", "telephoto lens from the stands", "wide-angle lens, low angle",
          "85mm lens, shallow focus", "high angle from the roof of the stand", "photographed from pitch level")


def drawing_online() -> bool:
    """True when Draw Things answers on its API port."""
    from app.services import picture_makers

    try:
        with urllib.request.urlopen(f"{picture_makers.draw_things_url()}/sdapi/v1/options", timeout=3):
            return True
    except OSError:
        return False


def _club_of(article: WorldSoccerArticle) -> int | None:
    """The story's club: the first club it is about (the winner, the buying club, the player's club)."""
    for ref in (article.lead or {}).get("refs") or []:
        if ref.startswith("club:"):
            return int(ref.split(":")[1])
    return None


# For the writer's own scene, which may be a high street or a burger van as well as a match: the look, not the subject.
_WRITER_STYLE = ("A documentary press photograph: natural colour, real people and real places, sharp on the subject with the "
                 "background softly out of focus. One single photograph of one moment, the whole image one continuous frame "
                 "from edge to edge.")


def illustration_prompt(article: WorldSoccerArticle, rng: random.Random | None = None) -> str:
    """The writer's scene when the article has one (it knows the piece's place and moment), else a scene of its kind."""
    rng = rng or random.Random(article.id)
    own = (article.lead or {}).get("picture")
    if own:
        club = _club_of(article)
        team = _team_name(club) if club else None
        kit = f" Any players, scarves and flags are in {team}'s home colours." if team else ""
        own = re.sub(r"\b[Ff]ootball", "soccer", own)  # "football" alone draws the American game
        return f"{own.rstrip('.')}. {rng.choice(_VIEWS).capitalize()}.{kit} {_WRITER_STYLE}"
    kind = (article.lead or {}).get("kind", "")
    scene = rng.choice(_SCENES.get(kind, _DEFAULT_SCENES))
    club = _club_of(article)
    team = _team_name(club) if club else None
    kit = f" The team is {team}: the players, scarves and flags in {team}'s home colours." if team else ""
    return f"{scene[0].upper()}{scene[1:]}. {rng.choice(_LIGHTS).capitalize()}, {rng.choice(_VIEWS)}.{kit} {_STYLE}"


def _draw(prompt: str, seed: int) -> bytes:
    from app.services import picture_makers

    return picture_makers.draw(prompt, seed, WIDTH, HEIGHT, model=picture_makers.draw_things_illustration_model())


def _team_name(club_uid: int) -> str | None:
    from app.models import world as world_models
    from app.services import world_queries as q

    with db.engines[world_models.BIND].connect() as connection:
        return q.teams(connection, [club_uid]).get(club_uid, {}).get("name")


def illustrate(article: WorldSoccerArticle) -> str:
    """Generate and save an illustration for the article; returns its address."""
    from PIL import Image

    seed = random.randrange(2_000_000_000)  # a new picture every time
    png = _draw(illustration_prompt(article, random.Random(seed)), seed)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image = image.resize((SAVED_WIDTH, round(image.height * SAVED_WIDTH / image.width)), Image.LANCZOS)
    ILLUSTRATIONS.mkdir(parents=True, exist_ok=True)
    name = f"article-{article.id}-{abs(hash(article.headline)) % 10**8}.jpg"
    image.save(ILLUSTRATIONS / name, quality=86, optimize=True)
    for old in ILLUSTRATIONS.glob(f"article-{article.id}-*.jpg"):
        if old.name != name:
            old.unlink(missing_ok=True)
    article.image_url = f"{ILLUSTRATION_URL}/{name}"
    article.image_caption = None
    db.session.commit()
    return article.image_url


def illustrate_front_pages(articles: list[WorldSoccerArticle], progress=log.info) -> int:
    """An illustration for each given article (the editions' leads), when Draw Things is running."""
    from app.services import picture_makers

    ready, why = picture_makers.online()
    if not articles or not ready:
        progress(f"{why or 'Nothing to draw'}: no illustrations this time")
        return 0
    made = 0
    for number, article in enumerate(articles, 1):
        try:
            illustrate(article)
            made += 1
            progress(f"Illustrated {number} of {len(articles)}")
        except picture_makers.PictureError as exc:
            progress(f"No illustration for {article.headline}: {exc}")
    return made


def lead_article(issue: WorldSoccerIssue) -> WorldSoccerArticle | None:
    return min(issue.articles, key=lambda a: (a.sort_order, a.id), default=None)


def illustrate_editions(issue_ids: list[int], again: bool = False, progress=log.info) -> int:
    """Illustrations for the lead stories of the given editions: those without one, or all of them ``again``."""
    fronts = [lead_article(issue) for issue in (db.session.get(WorldSoccerIssue, i) for i in issue_ids) if issue]
    fronts = [a for a in fronts if a and (again or not is_illustration(a.image_url))]
    if not fronts:
        progress("Every edition's lead story already has a picture")
        return 0
    return illustrate_front_pages(fronts, progress)


# -- a competition's own pictures: a pool its stories share ----------------------------------------------------------------
# Without one, every story about a competition showed its logo. A pool of a few pictures in its mood, drawn once and kept in
# the image library, gives each story one of them: chosen by the story's number, so two stories differ and a story keeps its.
COMPETITION_SCENES = (
    "a packed soccer stadium at night as the teams walk out, the anthem moment, a giant banner unfurled in the stands",
    "a wall of fans behind a goal with flags, flares and scarves, singing, floodlights above",
    "a goal celebration in front of the away end, players piled on the scorer, the crowd a blur",
    "two captains and the referee at the coin toss on the centre circle, the ball on its plinth, a full stadium",
    "a stadium exterior at dusk, crowds streaming towards the lit gates, scarves and programmes",
    "a goalkeeper flying across goal to tip a shot over, the net and the crowd behind",
    "a crowded penalty box as a corner comes in, players rising for the header",
    "a silver trophy on a plinth before kick-off, the teams lined up behind it",
    "a long exposure of a stadium bowl at night, a sea of phone lights in the stands",
    "a winger sprinting down the touchline past the dugouts, the bench on its feet",
)
NATIONAL_SCENES = (
    "two national teams lined up for the anthems, a sea of flags of many countries in the stands",
    "fans painted in their country's colours in a packed stadium, flags and drums",
    "a national team huddle before kick-off, a huge stadium bowl behind, the pitch lit bright",
    "a goal celebration in an international, players running to a corner flag, flags waving in the stands",
    "a fan zone on a city square, a giant screen, thousands watching an international at night",
)
# A major tournament's finals (the World Cup, a continental championship): the whole world in one country for a month
TOURNAMENT_SCENES = (
    "an opening ceremony in a vast stadium, dancers in bright costumes on the pitch, flags of every nation around the bowl",
    "a sea of fans in the colours of two countries filling a huge stadium at night, flags, drums, faces painted",
    "a national team walking out through a tunnel of flags for a knockout game, the stadium a wall of light and noise",
    "a fan zone on a city square at dusk, a giant screen, thousands of fans of many countries watching, flags waving",
    "a goal celebration in a tournament knockout game, the scorer sliding on his knees to a corner full of his country's flags",
    "a captain lifting a golden trophy on a podium, confetti falling, his team and the stadium lit up behind him",
    "two sets of fans sharing a stand, scarves of both countries, a close game under floodlights",
    "penalty shoot-out, the goalkeeper on his line, the taker walking up, both benches on their feet, a full stadium holding its breath",
    "fans of a small nation celebrating in a packed stadium, flags and tears, their team applauding them after the game",
    "a stadium exterior before a tournament game, crowds of many countries in their shirts streaming in, banners on the walls",
)
# A club's generic pictures: any week's story about the club can carry one (the club's colours, never its crest or name)
CLUB_SCENES = (
    "the outside of a football stadium on a matchday morning, turnstiles, fans in {colours} arriving, a programme seller",
    "fans in {colours} scarves and shirts packed outside the ground before kick-off, flags, a pub on the corner",
    "a players' tunnel before kick-off, the teams lined up in {colours} and the opponents' kit, the pitch bright beyond",
    "a training ground on a weekday morning, players in {colours} training bibs, cones, the manager watching with his arms folded",
    "a dressing room before a game, shirts in {colours} hung above each seat, boots lined up, the floor still clean",
    "a football pitch under floodlights at night from the stand, the crowd in {colours} below, breath in the cold air",
    "the away end of a stadium in full voice, fans in {colours} standing, scarves held up, a flare's smoke",
    "a trophy room with silver cups on shelves, a shirt in {colours} framed on the wall, soft light",
    "the groundsman's pitch at dawn, dew on the grass, the mower lines, the empty stand in {colours} seats",
    "a club shop queue on a Saturday, children in {colours} shirts, a father with a scarf, the stadium behind",
    "a team bus arriving at the stadium, fans in {colours} lining the road with phones up, police on horses",
    "an old man and a boy in {colours} scarves in their seats before kick-off, the pitch below, the ground filling",
)
POOL_KINDS = ("photograph", "matchday", "stadium")
TOURNAMENT_POOL = 12  # a tournament in play gets a bigger pool than a competition
CLUB_POOL = 8


MOODS = {"continental": "A European night between two big clubs.", "national_teams": "An international match.",
         "cup": "A cup tie.", "league": "A league match.", "tournament": "A major international tournament, a summer of football."}


def is_finals(name: str | None, scope: str | None) -> bool:
    """A senior international tournament's finals (the World Cup, a continental championship), not its qualifiers or a
    youth edition."""
    from app.services.tournament_leads import MAJOR_TOURNAMENTS

    name = name or ""
    return (scope == "national_teams" and "Qualif" not in name and not name.startswith(("Under", "U1", "U2")) and "Youth" not in name
            and any(name == m or name.startswith(m) for m in MAJOR_TOURNAMENTS))


def _ground_words(reputation: int | None) -> str:
    """The size of the ground from the competition's reputation (0-200): without it every picture looks like the Premier
    League (the Irish and Croatian pools did, 25 Oct 2026)."""
    if reputation is None:
        return ""
    if reputation >= 170:
        return "a huge modern stadium, tens of thousands in the stands"
    if reputation >= 130:
        return "a big stadium, the stands full"
    if reputation >= 90:
        return "a mid-sized ground of a few stands, most seats taken"
    return "a small ground with one main stand, a few thousand fans, houses or trees behind the far end"


def competition_prompt(name: str, scope: str | None, rng: random.Random, nation: str | None = None, reputation: int | None = None) -> tuple[str, str]:
    """A scene for a competition's pool picture and its prompt (the competition's name stays out: a name invites lettering
    and invented logos; its kind sets the mood). The country and the size of the ground go in, so an Irish league looks
    Irish and small. A major tournament's finals have scenes of their own."""
    finals = is_finals(name, scope)
    scene = rng.choice(TOURNAMENT_SCENES if finals else NATIONAL_SCENES if scope == "national_teams" else COMPETITION_SCENES)
    mood = MOODS["tournament"] if finals else MOODS.get(scope or "", "")
    if nation and not finals and scope != "continental":
        mood = f"{mood} In {nation}: the look of football there, its grounds, fans, light and weather.".strip()
    ground = _ground_words(reputation) if not finals else ""
    if ground:
        mood = f"{mood} {ground.capitalize()}.".strip()
    return scene, f"{scene[0].upper()}{scene[1:]}. {rng.choice(_LIGHTS).capitalize()}, {rng.choice(_VIEWS)}. {mood} {_STYLE}".replace("  ", " ")


def club_prompt(name: str, rng: random.Random) -> tuple[str, str]:
    """A scene for a club's pool picture and its prompt: the club's colours from its name, never its crest or lettering."""
    scene = rng.choice(CLUB_SCENES).format(colours=f"the colours of {name}" if name else "the home colours")
    return scene, (f"{scene[0].upper()}{scene[1:]}. {rng.choice(_LIGHTS).capitalize()}, {rng.choice(_VIEWS)}. A football club's week. "
                   f"Plain kits, scarves and flags in those colours, every surface free of crests and lettering. {_STYLE}").replace("  ", " ")


def draw_club_pictures(club_uid: int, count: int, progress=log.info) -> int:
    """Draw ``count`` pictures for a club's pool and keep them in the image library; those it has already count."""
    from PIL import Image

    from app.models import world as world_models
    from app.services import world_images
    from app.services import world_queries as q

    with db.engines[world_models.BIND].connect() as connection:
        club = q._one(connection, "select name from clubs where fm_uid = :c", c=club_uid)
        nation = None if club else q._one(connection, "select name from nations where fm_uid = :c", c=club_uid)
    name = club["name"] if club else nation["name"] if nation else f"club {club_uid}"
    have = len(world_images.pictures(f"club:{club_uid}", POOL_KINDS))
    made = 0
    for number in range(have, count):
        seed = random.randrange(2_000_000_000)
        if nation:  # a national team (its stories link the nation's uid as a club): international scenes in its country
            scene, prompt = competition_prompt("", "national_teams", random.Random(seed), nation=name)
        else:
            scene, prompt = club_prompt(name, random.Random(seed))
        png = _draw(prompt, seed)
        image = Image.open(io.BytesIO(png)).convert("RGB")
        image = image.resize((SAVED_WIDTH, round(image.height * SAVED_WIDTH / image.width)), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=86, optimize=True)
        world_images.add_generated_image(f"club:{club_uid}", "photograph", out.getvalue(), caption=f"{name}: {scene}", extension="jpg")
        made += 1
        progress(f"{name}: picture {number + 1} of {count}")
    return made


NATIONS_IN_THE_NEWS = 6


def clubs_in_the_news(limit: int = 20) -> list[int]:
    """The clubs the published stories are about, the followed clubs first, then the most written about, then the national
    teams most written about (their stories link the nation; the pool is kept under the nation's uid as a club)."""
    from sqlalchemy import func

    from app.models.world_soccer_entity_link import WorldSoccerEntityLink
    from app.services.story_leads import followed_clubs

    def most(entity_type: str, count: int) -> list[int]:
        rows = (db.session.query(WorldSoccerEntityLink.entity_id, func.count(WorldSoccerEntityLink.id).label("n"))
                .join(WorldSoccerArticle).join(WorldSoccerIssue).filter(WorldSoccerIssue.status == "published",
                                                                         WorldSoccerEntityLink.entity_type == entity_type)
                .group_by(WorldSoccerEntityLink.entity_id).order_by(func.count(WorldSoccerEntityLink.id).desc()).limit(count).all())
        return [row[0] for row in rows]

    return list(dict.fromkeys(list(followed_clubs()) + most("club", limit) + most("nation", NATIONS_IN_THE_NEWS)))


def draw_club_pools(count: int, progress=log.info) -> int:
    """Pools for the followed clubs and the clubs in the news, when the picture maker can draw."""
    from app.services import picture_makers

    ready, why = picture_makers.online()
    if not ready:
        progress(f"{why}: no pictures this time")
        return 0
    made = 0
    for uid in clubs_in_the_news():
        try:
            made += draw_club_pictures(uid, count, progress)
        except picture_makers.PictureError as exc:
            progress(f"No picture for club {uid}: {exc}")
    return made


def test_picture() -> tuple[str, str]:
    """One picture from a scene chosen at random, to judge a model and its settings: its address and its prompt."""
    from PIL import Image

    seed = random.randrange(2_000_000_000)
    rng = random.Random(seed)
    scene, prompt = competition_prompt("", rng.choice(list(MOODS)), rng)
    png = _draw(prompt, seed)
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image = image.resize((SAVED_WIDTH, round(image.height * SAVED_WIDTH / image.width)), Image.LANCZOS)
    ILLUSTRATIONS.mkdir(parents=True, exist_ok=True)
    for old in ILLUSTRATIONS.glob("test-*.jpg"):
        old.unlink(missing_ok=True)
    name = f"test-{seed}.jpg"
    image.save(ILLUSTRATIONS / name, quality=86, optimize=True)
    return f"{ILLUSTRATION_URL}/{name}", prompt


def competitions_in_the_news(limit: int = 20) -> list[int]:
    """The competitions the published stories are about, the most written about first."""
    from sqlalchemy import func

    from app.models.world_soccer_entity_link import WorldSoccerEntityLink

    rows = (db.session.query(WorldSoccerEntityLink.entity_id, func.count(WorldSoccerEntityLink.id).label("n"))
            .join(WorldSoccerArticle).join(WorldSoccerIssue).filter(WorldSoccerIssue.status == "published",
                                                                     WorldSoccerEntityLink.entity_type == "competition")
            .group_by(WorldSoccerEntityLink.entity_id).order_by(func.count(WorldSoccerEntityLink.id).desc()).limit(limit).all())
    return [row[0] for row in rows]


def tournaments_in_play() -> list[int]:
    """The major international tournaments in play at the game date (a week before the first game to three days after)."""
    from app.models import world as world_models
    from app.public import football

    with db.engines[world_models.BIND].connect() as connection:
        found = football.major_tournament(connection)
    return [found["uid"]] if found else []


def draw_competition_pictures(competition_uid: int, count: int, progress=log.info) -> int:
    """Draw ``count`` pictures for a competition's pool and keep them in the image library; those it has already count."""
    from PIL import Image

    from app.models import world as world_models
    from app.services import world_images
    from app.services import world_queries as q

    with db.engines[world_models.BIND].connect() as connection:
        comp = q.competition(connection, competition_uid) or {}
        nation = q._one(connection, "select name from nations where fm_uid = :n", n=comp.get("nation_uid")) if comp.get("nation_uid") else None
    name = comp.get("name") or f"competition {competition_uid}"
    nation_name = nation["name"] if nation else None
    have = len(world_images.pictures(f"competition:{competition_uid}", POOL_KINDS))
    made = 0
    for number in range(have, count):
        seed = random.randrange(2_000_000_000)
        scene, prompt = competition_prompt(name, comp.get("scope"), random.Random(seed), nation_name, comp.get("reputation"))
        png = _draw(prompt, seed)
        image = Image.open(io.BytesIO(png)).convert("RGB")
        image = image.resize((SAVED_WIDTH, round(image.height * SAVED_WIDTH / image.width)), Image.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=86, optimize=True)
        world_images.add_generated_image(f"competition:{competition_uid}", "photograph", out.getvalue(), caption=f"{name}: {scene}", extension="jpg")
        made += 1
        progress(f"{name}: picture {number + 1} of {count}")
    return made


def draw_competition_pools(count: int, progress=log.info) -> int:
    """Pools for the competitions in the news, when the picture maker can draw."""
    from app.services import picture_makers

    ready, why = picture_makers.online()
    if not ready:
        progress(f"{why}: no pictures this time")
        return 0
    made = 0
    wanted = {uid: count for uid in competitions_in_the_news()}
    for uid in tournaments_in_play():  # a tournament in play gets its bigger pool, in the news or not
        wanted[uid] = max(wanted.get(uid, 0), TOURNAMENT_POOL)
    for uid, n in wanted.items():
        try:
            made += draw_competition_pictures(uid, n, progress)
        except picture_makers.PictureError as exc:
            progress(f"No picture for competition {uid}: {exc}")
    return made


def pool_picture(article: WorldSoccerArticle) -> str | None:
    """A picture from the pool of the story's first club with one, else of its competition: the one its number points at."""
    from app.services import world_images

    links = [(link.entity_type, link.entity_id) for link in article.entity_links if link.entity_type in ("club", "competition")]
    for entity in ("club", "competition"):
        for kind, uid in links:
            if kind == entity:
                pool = [image for image in world_images.pictures(f"{entity}:{uid}", POOL_KINDS) if image.is_active]
                if pool:
                    return pool[article.id % len(pool)].url
    return None


def remove_illustration(article: WorldSoccerArticle) -> None:
    """Take a story's drawn picture away (the story falls back to the pools, faces and crests) and delete its file."""
    from config import generated_path

    path = generated_path(article.image_url) if is_illustration(article.image_url) else None
    article.image_url = None
    article.image_caption = None
    db.session.commit()
    if path is not None:
        path.unlink(missing_ok=True)


def illustrated_articles(limit: int = 400) -> list[WorldSoccerArticle]:
    """The stories with a drawn picture, newest first."""
    return (WorldSoccerArticle.query.join(WorldSoccerIssue).filter(WorldSoccerArticle.image_url.like(f"{ILLUSTRATION_URL}/%"))
            .order_by(WorldSoccerIssue.publication_date.desc(), WorldSoccerArticle.id.desc()).limit(limit).all())


def is_illustration(url: str | None) -> bool:
    return bool(url) and url.startswith(ILLUSTRATION_URL)

