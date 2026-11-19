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
_STYLE = ("Sports agency press photograph of soccer, natural colour, sharp focus on the subject, shallow depth of field, real faces and "
          "hands, photojournalism.")  # 19 Nov 2026: short; the long style and busy scenes gave slop
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
SCENE_STEPS = 16  # a busy scene (crowds, flags, a stadium) needs more than a portrait's 8 steps (19 Nov 2026: 8 gave slop)
_SUMMER_LIGHTS = ("dramatic floodlights on a warm summer night", "low golden evening sun", "bright midday sun and hard shadows",
                  "a hazy summer afternoon", "a warm dusk, the sky still blue")  # a tournament is played in summer
_VIEWS = ("35mm lens, close to the action", "telephoto lens from the stands", "wide-angle lens, low angle",
          "85mm lens, shallow focus", "high angle from the roof of the stand", "photographed from pitch level")


def drawing_online() -> bool:
    """True when Draw Things answers on its API port."""
    from app.services import draw_things_grpc, picture_makers

    if picture_makers.draw_things_grpc():
        return draw_things_grpc.online(picture_makers.draw_things_url())
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


def _rival_of(article: WorldSoccerArticle) -> int | None:
    """The story's second club (the beaten side, the selling club), when it names one."""
    clubs = [int(ref.split(":")[1]) for ref in (article.lead or {}).get("refs") or [] if ref.startswith("club:")]
    return clubs[1] if len(clubs) > 1 else None


def _sides(article: WorldSoccerArticle) -> str:
    """The sentence that names who is in the picture: the story's club in its kit, its opponent in theirs, the country.
    Without it a scene of players celebrating had shirts of every colour (19 Nov 2026)."""
    club, rival = _club_of(article), _rival_of(article)
    team = _team_name(club) if club else None
    if not team:
        return ""
    other = _team_name(rival) if rival else None
    country = _country_of(club)
    words = f" The team is {team}: every {team} player, scarf and flag in {team}'s home kit and colours."
    if other:
        words += f" Their opponents are {other}, in {other}'s away kit."
    if country:
        words += f" In {country}."
    return words


def _country_of(club_uid: int) -> str | None:
    from app.models import world as world_models
    from app.services import world_queries as q

    with db.engines[world_models.BIND].connect() as connection:
        row = q._one(connection, "select n.name from clubs c join nations n on n.fm_uid = c.nation_uid where c.fm_uid = :c", c=club_uid)
    return row["name"] if row else None


# For the writer's own scene, which may be a high street or a burger van as well as a match: the look, not the subject.
_WRITER_STYLE = ("A documentary press photograph: natural colour, real people and real places, sharp on the subject with the "
                 "background softly out of focus. One single photograph of one moment, the whole image one continuous frame "
                 "from edge to edge.")


def illustration_prompt(article: WorldSoccerArticle, rng: random.Random | None = None) -> str:
    """The writer's scene when the article has one (it knows the piece's place and moment), else a scene of its kind."""
    rng = rng or random.Random(article.id)
    own = (article.lead or {}).get("picture")
    sides = _sides(article)
    if own:
        own = re.sub(r"\b[Ff]ootball", "soccer", own)  # "football" alone draws the American game
        return f"{own.rstrip('.')}. {rng.choice(_VIEWS).capitalize()}.{sides} {_WRITER_STYLE}"
    kind = (article.lead or {}).get("kind", "")
    scene = rng.choice(_SCENES.get(kind, _DEFAULT_SCENES))
    return f"{scene[0].upper()}{scene[1:]}. {rng.choice(_LIGHTS).capitalize()}, {rng.choice(_VIEWS)}.{sides} {_STYLE}"


def _draw(prompt: str, seed: int) -> bytes:
    from app.services import picture_makers

    return picture_makers.draw(prompt, seed, WIDTH, HEIGHT, model=picture_makers.draw_things_illustration_model(), steps=SCENE_STEPS)


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
# Each scene names its teams and its place: {team} the picture's team, {rival} its opponent, {country} where it is. A scene
# that names nobody gave players of every colour celebrating together and fans of no club (19 Nov 2026): the models fill
# a gap with anything. The teams come from the competition's own clubs (pool_teams); a team's own kit is named, which the
# models know for the real clubs.
COMPETITION_SCENES = (
    "the players of {team} and {rival} walking out of the tunnel side by side before kick-off, {team} in their home kit and "
    "{rival} in their away kit, the two captains in front",
    "{team} fans behind a goal on their feet singing, {team} shirts and scarves held up above their heads, one fan in the front row shouting",
    "a {team} player in the {team} home kit celebrating a goal, running towards the camera with his arms out, two {team} "
    "team-mates in the same kit chasing him, the crowd blurred behind",
    "the {team} goalkeeper diving full stretch to tip a shot from a {rival} striker round the post, the ball at his fingertips",
    "a {team} striker in the {team} home kit rising above a {rival} defender in the {rival} away kit to head the ball in the "
    "penalty box, both off the ground. The {rival} goalkeeper is standing in goal, facing them",
    "a {team} winger in the {team} home kit sprinting with the ball at his feet past a {rival} defender near the touchline, the dugouts behind",
    "the {team} manager in a dark jacket shouting instructions from the touchline, one arm pointing, the {team} bench behind him",
    "{team} fans in {team} shirts and scarves walking along a street in {country} towards a lit stadium at dusk, seen from behind",
    "a {team} player and a {rival} player, each in his own team's kit, challenging for the ball in midfield, the ball in the "
    "air between them, the crowd blurred behind",
    "a {team} player in the {team} home kit sitting alone on the grass after the final whistle, head bowed, the stand emptying behind him",
)
NATIONAL_SCENES = (
    "the {team} national team in their home kit standing in a line on the pitch for the anthem, hands on hearts, a full stadium behind",
    "{team} fans in {team} national team shirts with painted faces singing in the stand, {team} flags and scarves held up",
    "the {team} national team in their home kit in a huddle on the pitch before kick-off, arms round each other's shoulders, the stadium blurred behind",
    "a {team} player in the {team} national team's home kit celebrating a goal, sliding on his knees towards the corner flag, "
    "{team} fans on their feet behind",
    "{team} fans in {team} shirts seen from behind watching a giant screen on a city square in {country} at dusk, arms raised at a goal",
)
# A major tournament's finals (the World Cup, a continental championship): the whole world in one country for a month
TOURNAMENT_SCENES = (
    "a {team} player in the {team} national team's home kit celebrating a goal, sliding on his knees on the grass towards "
    "the corner flag, mouth open, arms wide; behind him a packed stand of {team} fans on their feet, blurred",
    "the {team} national team in their home kit standing in a line on the pitch for the anthem before a tournament game, "
    "hands on hearts, a huge stadium full to the roof behind them",
    "{team} fans in {team} national team shirts with painted faces singing in the stand of a big stadium, {team} scarves "
    "held up, one fan in the front row shouting with joy",
    "the {team} goalkeeper in a bright kit diving full stretch to save a shot from a {rival} striker, the ball at his "
    "fingertips, the net and a blurred crowd behind",
    "the {team} captain with the armband shouting encouragement to his {team} team-mates in a huddle before kick-off, all "
    "in the {team} home kit, a huge stadium behind",
    "the {team} national team coach in a suit on the touchline, arms folded, watching intently, the {team} bench behind him",
    "{team} fans in {team} shirts walking towards a big modern stadium before a tournament game, seen from behind, the stadium rising ahead",
    "{team} fans in {team} shirts seen from behind watching a giant screen on a city square at dusk, arms raised at a goal",
    "a {team} player in the {team} national team's kit sitting alone on the grass after the final whistle, head bowed, "
    "the stadium emptying behind him",
    "a {team} striker in the {team} home kit striking the ball on the volley, the ball leaving his boot, {rival} defenders "
    "in the {rival} away kit closing in",
    "a {team} striker in the {team} home kit rising above a {rival} defender in the {rival} away kit to head the ball in "
    "the penalty box, both off the ground. The {rival} goalkeeper is standing in goal, facing them",
)
# A club's generic pictures: any week's story about the club can carry one
CLUB_SCENES = (
    "the outside of the {team} stadium in {country} on a matchday morning, turnstiles, {team} fans in {team} shirts arriving, a programme seller",
    "{team} fans in {team} scarves and shirts packed outside the ground before kick-off, {team} flags, a pub on the corner",
    "a players' tunnel before kick-off, {team} lined up in their home kit and {rival} in their away kit, the pitch bright beyond",
    "the {team} training ground on a weekday morning, {team} players in training bibs over their {team} training kit, cones, "
    "the manager watching with his arms folded",
    "the {team} dressing room before a game, {team} home shirts hung above each seat, boots lined up, the floor still clean",
    "a football pitch under floodlights at night seen from the stand, {team} fans in {team} shirts below, breath in the cold air",
    "the {team} away end of a stadium in full voice, {team} fans in {team} shirts standing, scarves held up, a flare's smoke",
    "the {team} trophy room with silver cups on shelves, a {team} home shirt framed on the wall, soft light",
    "the {team} pitch at dawn, dew on the grass, the mower lines, the empty stand behind",
    "the {team} club shop queue on a Saturday, children in {team} shirts, a father with a {team} scarf, the stadium behind",
    "the {team} team bus arriving at the stadium, {team} fans in {team} shirts lining the road with phones up, police on horses",
    "an old man and a boy in {team} scarves in their seats before kick-off, the pitch below, the ground filling",
    "a {team} striker in the {team} home kit rising above a {rival} defender in the {rival} away kit to head the ball in the "
    "penalty box, both off the ground. The {rival} goalkeeper is standing in goal, facing them",
    "a {team} player in the {team} home kit celebrating a goal in front of the {team} fans, two {team} team-mates in the same "
    "kit running to join him",
)
_ANYONE = {"team": "the home team", "rival": "the away team", "country": "their country"}  # no teams known: still one side each


def fill(scene: str, teams: dict | None) -> str:
    """A scene with its teams and country in it (the generic words when they are not known: "a the home team player"
    becomes "a home team player")."""
    filled = scene.format(**{**_ANYONE, **{k: v for k, v in (teams or {}).items() if v}})
    return re.sub(r"\b([Aa]|[Tt]he) the ", r"\1 ", filled)


def _template_of(filled: str, scenes: tuple[str, ...]) -> str | None:
    """The scene a filled scene came from (pool pictures keep the filled scene in their caption)."""
    for scene in scenes:
        pattern = re.escape(scene).replace(r"\{team\}", ".+?").replace(r"\{rival\}", ".+?").replace(r"\{country\}", ".+?")
        if filled == scene or re.fullmatch(pattern, filled):
            return scene
    return None


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


def _fresh(scenes: tuple[str, ...], used: set[str], rng: random.Random) -> str:
    """A scene the pool does not have yet; when it has them all, any. Each picture chose by itself before, so a pool of 12
    from 10 scenes had the same one three or four times (League One's anthem four times, 19 Nov 2026). ``used`` holds the
    filled scenes of the pool's pictures."""
    taken = {_template_of(scene, scenes) for scene in used}
    unused = [scene for scene in scenes if scene not in taken]
    return rng.choice(unused or list(scenes))


def _scenes_in(ref: str) -> set[str]:
    """The scenes a pool's pictures were drawn from: their captions are "Name: scene"."""
    from app.services import world_images

    return {image.caption.split(": ", 1)[1] for image in world_images.pictures(ref, POOL_KINDS) if image.caption and ": " in image.caption}


def competition_prompt(name: str, scope: str | None, rng: random.Random, nation: str | None = None, reputation: int | None = None,
                       used: set[str] = frozenset(), teams: dict | None = None) -> tuple[str, str]:
    """A scene for a competition's pool picture and its prompt. The competition's name stays out (a name invites lettering
    and invented logos; its kind sets the mood); its teams go in (``teams``: team, rival, country, from pool_teams), so
    every player and fan belongs to a named side. The country and the size of the ground go in, so an Irish league looks
    Irish and small. A major tournament's finals have scenes of their own. ``used``: the pool's scenes, left out while
    others remain."""
    finals = is_finals(name, scope)
    scene = fill(_fresh(TOURNAMENT_SCENES if finals else NATIONAL_SCENES if scope == "national_teams" else COMPETITION_SCENES, used, rng), teams)
    mood = MOODS["tournament"] if finals else MOODS.get(scope or "", "")
    if nation and not finals and scope != "continental":
        mood = f"{mood} In {nation}: the look of football there, its grounds, fans, light and weather.".strip()
    ground = _ground_words(reputation) if not finals else ""
    if ground:
        mood = f"{mood} {ground.capitalize()}.".strip()
    light = rng.choice(_SUMMER_LIGHTS if finals else _LIGHTS)
    return scene, f"{scene[0].upper()}{scene[1:]}. {light.capitalize()}, {rng.choice(_VIEWS)}. {mood} {_STYLE}".replace("  ", " ")


def club_prompt(name: str, rng: random.Random, used: set[str] = frozenset(), teams: dict | None = None) -> tuple[str, str]:
    """A scene for a club's pool picture and its prompt: the club by name in its own kit, and its opponent when the scene has one."""
    scene = fill(_fresh(CLUB_SCENES, used, rng), {"team": name, **(teams or {})})
    return scene, (f"{scene[0].upper()}{scene[1:]}. {rng.choice(_LIGHTS).capitalize()}, {rng.choice(_VIEWS)}. "
                   f"A football club's week. {_STYLE}").replace("  ", " ")


def pool_teams(connection, rng: random.Random, competition_uid: int | None = None, team_uid: int | None = None) -> dict:
    """The teams for one pool picture: a competition's club (the bigger ones more often) and another of its clubs as the
    rival; or the given team and one of its recent opponents. The country is the team's nation."""
    from app.services import world_queries as q

    def nation_of(row) -> str | None:
        """A national team's nation, or a club team's club's nation (a club's team rows have none of their own)."""
        found = q._one(connection, "select n.name from nations n where n.fm_uid = coalesce(:n, (select nation_uid from clubs "
                       "where fm_uid = :c))", n=row.get("nation_uid"), c=row.get("club_uid"))
        return found["name"] if found else None

    if competition_uid is not None:
        clubs = q.league_clubs(connection, competition_uid)
        if not clubs:
            return {}
        team = rng.choices(clubs, weights=[max(1, (c.get("reputation") or 0)) for c in clubs])[0]
        others = [c for c in clubs if c["fm_uid"] != team["fm_uid"]]
        rival = rng.choice(others) if others else None
    else:
        team = q._one(connection, "select fm_uid, name, nation_uid, club_uid from teams where fm_uid = :t", t=team_uid)
        if not team:
            return {}
        team = dict(team)
        rivals = q._rows(connection, "select distinct t.name from fixtures f join teams t on t.fm_uid = "
                         "(case when f.home_uid = :t then f.away_uid else f.home_uid end) where (f.home_uid = :t or f.away_uid = :t) "
                         "order by f.date desc limit 20", t=team_uid)
        rival = rng.choice(rivals) if rivals else None
    return {"team": team["name"], "rival": rival["name"] if rival else None, "country": nation_of(team)}


def first_team(connection, club_uid: int, nation: bool = False) -> int | None:
    """A club's first team (or a nation's senior team): the one with the most reputation and no age limit."""
    from app.services import world_queries as q

    column = "nation_uid" if nation else "club_uid"
    extra = " and club_uid is null" if nation else ""
    row = q._one(connection, f"select fm_uid from teams where {column} = :c{extra} and age_limit is null "
                 "order by reputation desc limit 1", c=club_uid)
    return row["fm_uid"] if row else None


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
        rng = random.Random(seed)
        with db.engines[world_models.BIND].connect() as connection:
            team_uid = first_team(connection, club_uid, nation=bool(nation))
            teams = pool_teams(connection, rng, team_uid=team_uid) if team_uid else {}
        teams = {**teams, "team": name}  # the club's or nation's own name, not its team's ("Chelsea", not "Chelsea FC Reserves")
        if nation:  # a national team (its stories link the nation's uid as a club): international scenes in its country
            scene, prompt = competition_prompt("", "national_teams", rng, nation=name, used=_scenes_in(f"club:{club_uid}"),
                                               teams={**teams, "country": name})
        else:
            scene, prompt = club_prompt(name, rng, used=_scenes_in(f"club:{club_uid}"), teams=teams)
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
        rng = random.Random(seed)
        with db.engines[world_models.BIND].connect() as connection:
            teams = pool_teams(connection, rng, competition_uid=competition_uid)
        scene, prompt = competition_prompt(name, comp.get("scope"), rng, nation_name, comp.get("reputation"),
                                           used=_scenes_in(f"competition:{competition_uid}"), teams=teams)
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


def illustrated_articles(limit: int | None = None) -> list[WorldSoccerArticle]:
    """The stories with a drawn picture, newest first; no cap by default, as the gallery's library."""
    found = (WorldSoccerArticle.query.join(WorldSoccerIssue).filter(WorldSoccerArticle.image_url.like(f"{ILLUSTRATION_URL}/%"))
             .order_by(WorldSoccerIssue.publication_date.desc(), WorldSoccerArticle.id.desc()))
    return (found.limit(limit) if limit else found).all()


def is_illustration(url: str | None) -> bool:
    return bool(url) and url.startswith(ILLUSTRATION_URL)

