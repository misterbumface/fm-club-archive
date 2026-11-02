"""Write season stories, competition stories and match reports from the save data
(docs/stories-plan.md), as ``bio_writer`` does for players.

Each writer builds its notebook (``story_notebooks``), asks the AI model chosen in Settings
for the text, checks that every year and score in it is in the notebook, and saves it. One
wrong draft gets one more try with its faults listed; a second wrong draft is refused.
"""

from __future__ import annotations

import json
import re
from datetime import date

import requests
from flask import current_app

from app.extensions import db
from app.models import world as world_models
from app.models.match_text import CURATOR, GAME, GENERATED as MATCH_GENERATED, REPORT, MatchText
from app.models.world_story import GENERATED, ClubSeasonStory, ClubText, EditionStory, StoryVersion, _now
from app.services import story_notebooks, world_queries as q
from app.services.writing_voices import chosen as chosen_voice, voice_prompt
from app.services.ai_settings import ai_request_config, ai_timeout, chat_content, check_ollama_answer, get_deepseek_model, resolve_ai_api_key

TIMEOUT_SECONDS = 180
MAX_TOKENS = 5000

_CRAFT = (
    "Scores in the notebook are written from the side named there; keep the order right. Do not call a goal 'the "
    "winner' unless the score makes it one. Follow the notebook's status: for something still in progress, write from "
    "the present moment and never state a final position, a trophy or a legacy that is not decided. Do not mention "
    "notebooks, saves, databases or data. Write as football people talk: 'the 2062/63 Champions League' or 'this "
    "season's FA Cup', never 'edition', and do not count how many times a competition has been played. A club's or "
    "player's stature is background only: never compare the two sides' standing or reputation, except as the "
    "notebook's expectation says it, in words. Avoid stock phrases such as 'footballing journey', 'etched his name', "
    "'testament to', 'rollercoaster' and 'the rest is history'."
)
# A fictional world told with life: the hard facts stay true to the save (so a text never contradicts the
# archive's pages), and the colour around them is free.
ARTICLE_RULES = (
    "The hard facts must come from the notebook and nothing may contradict it: every name, date, number, club, score, "
    "result, scorer, competition, fee and move. Do not invent goals, assists, injuries, transfers, line-ups or tactics, "
    "and do not name stadiums, managers, chairmen, cities or other people and places that are not in the notebook. Name a "
    "future opponent, draw or fixture only as the notebook gives it (next_round), never as a guess. A player's position "
    "only as the notebook gives it: a man whose position is not given is a player, not a midfielder, a striker or a "
    "keeper, and the squad's needs are never guessed from him. Around "
    "those facts, embellish like a real newspaper: the atmosphere, the crowd, the emotions, the drama and the stakes, and "
    "short quotes in character from the players or clubs in the story. The colour must add no new facts: no numbers, "
    "dates, results or events in it. A person's 'character' (a short fuse, a big-game player, a model professional) is what "
    "the club and the scouts know of him: let it shape what he says and does in the story and how others speak of him; "
    "never list it, never name a trait, never give it a number. A person's how_he_plays (the game's player traits) is "
    "background, like his character: let it colour a description lightly, at most one hint of it for a player in a "
    "piece and only where it fits the story (a winger 'always looking to come inside', a forward 'who likes the ball to "
    "feet'); never copy a trait's words, never list or stack them, never make it the point of a sentence, and never tell "
    "it as how one goal or one move happened (a left-footer still scores with his right). The people in his life (people_in_his_life) and his frame "
    "of mind (frame_of_mind) are facts of the same kind: a friend, a manager he is close to, a man he dislikes, an open "
    "grievance may colour a quote or a line; no other relationship exists, and no number of them is given. What he said "
    "at press conferences (in_the_press) is given as the game's headlines and tones: use their spirit in your own words. "
    "A player is injured, suspended or missing only when the notebook says so; a man absent from an expected line-up is "
    "simply not expected to start, and the reason is not known. A player is the leading scorer only when the notebook "
    "names him so. "
    "A continental game is not a league game: the clubs' own leagues (home_in_their_own_league, away_in_their_own_league) "
    "may be told as background, their form there, but the game counts in none of them, changes no league table and widens "
    "no gap, and the piece leads with this game and its own competition. "
    "Promotion, play-off and relegation places, the drop zone or safety, exist only as the notebook's league_rules give "
    "them; then use the plain words a paper uses. With no league_rules, or when they say it is not known, name no such "
    "place. "
    "When the notebook gives how_the_goals_went_in, a goal's kind of shot (a header, a volley, a drive, a shot with the "
    "foot), its height and whether it went through the middle or near a post come from it alone, in a paper's words ('headed "
    "in from close range' only when it is a header); never call a header a shot or a shot a header, never give a distance "
    "in yards or metres, and never invent the build-up (a cross, a free kick, a corner) it does not give. "
    "Morale, happiness, fitness and character are told in words, the way a club or a newspaper would put them; never as a "
    "score or a scale (no 'seven out of twenty'), which belongs to the game's screens, not to the world. "
) + _CRAFT
FACT_RULES = ARTICLE_RULES + (
    " Older texts in the notebook (old_story, older_texts, match_reports, preview) are colour: use their atmosphere, but "
    "where they disagree with the notebook's structured fields, the notebook wins and the old claim is left out silently."
)
_RULES = FACT_RULES + (
    " Write with life, colour, specificity and rhythm, in British English. Return a JSON object "
    "{\"text\": \"...\"}; the text is plain: no Markdown, bullets, sources note or labels."
)

SEASON_PROMPT = (
    "You are the senior football features writer for a private Football Manager history archive. Write a feature about "
    "one club's season from the supplied notebook: a newspaper headline of at most 12 words on the first line, then "
    "600-1000 words according to the depth of evidence, with 3-5 short, "
    "season-specific section headings, each on its own line with a blank line before the prose. Choose one central "
    "lens the evidence supports (a title race, a final, a changing squad, a breakthrough player, a near miss) rather "
    "than marching through every competition. Open on the most revealing result, player or contrast, not on the first "
    "fixture. Weave three to six players through the events, with their ages, nationalities and numbers where they "
    "matter. The page already shows the full results and squad, so never list them all. When the notebook has the "
    "club's background (owners, finances, history), let it shape the angle where it explains the season. " + _RULES
)

EDITION_PROMPT = (
    "You are the football correspondent of a private Football Manager history archive. Write the story of one edition "
    "of a competition from the supplied notebook: a newspaper headline of at most 12 words on the first line, then "
    "500-900 words according to the depth of evidence, with 3-5 short, "
    "specific section headings, each on its own line with a blank line before the prose. Give the winner, the decisive "
    "rounds or the title race, the final if there was one, surprising results and the leading scorers their proper "
    "place, and connect them to the previous winner and the winner's record where the notebook gives it. The page "
    "already shows every round and the table, so never list them all. " + _RULES
)

MATCH_PROMPT = (
    "You are the match reporter of a private Football Manager history archive. Write a report of one match from the "
    "supplied notebook: a short headline on the first line, then 250-450 words in four to six paragraphs and no other "
    "headings. Tell the match through its goals in order (minute, scorer, team), extra time and penalties if there were "
    "any, and what was at stake: the round, the edition, each team's form and league position before the day, the head "
    "to head, and what the winner meets next when the notebook gives next_round (with the aggregate of a second leg). "
    "Say who the scorers are (age, nationality, position) only where the notebook gives it. Let the game's match report "
    "shape the story: who had the ball and the better chances, the Player of the Match and the stand-out players, red "
    "cards and injuries and when they came, chances missed and shots that hit the woodwork. Pick the few numbers that "
    "tell the story; never list them all. Do not state records, tallies or runs the notebook does not give. " + _RULES
)
# A match the save knows only by its goals: no invented play, but the result's meaning in the competition.
MATCH_SUMMARY_PROMPT = (
    "You are the match reporter of a private Football Manager history archive. The save knows this match only by its "
    "result and goals, so write a short summary of what the result means, not an account of the play: a short headline "
    "on the first line, then 150-300 words in two to four paragraphs and no other headings. Give the result and the "
    "goals (minute, scorer, team; who the scorers are only where the notebook gives it), then read the result through "
    "the competition: each side's road so far (road_so_far, with its totals before this match in record_before_this_match; never count them yourself) "
    "and its group or league-phase place before the day, who was "
    "expected to win and whether this was as expected, an upset, a giant-killing or a rout (expectation), each side's "
    "form and league position before the day, and what the winner meets next when the notebook gives next_round (with "
    "the aggregate of a second leg). Describe no incident of the play beyond the goals: no cards, saves, misses, "
    "woodwork, possession, headers, how a goal was made, or the Player of the Match. Do not state records, tallies or "
    "runs the notebook does not give. " + _RULES
)

_YEAR = re.compile(r"\b(1[89]\d\d|2[01]\d\d)\b")
_SEASON = re.compile(r"\b(\d{4})/(\d{2})\b")
_GROUND = re.compile(r"\b(?:[A-Z][\w'.-]+ ){0,3}(?:Stadium|Stadion|Arena|Ground|Park|Road|Lane|Bridge)\b|\b[A-Z]\w+stadion\b")
# Things only the game's match report knows: without one, a text that tells them invents them.
_PLAY = re.compile(r"\b(red card|sent off|sending-off|second yellow|second bookable|woodwork|hit the (?:post|bar|crossbar)"
                   r"|Player of the Match|possession|clear-cut|penalty appeal|missed penalty|saved a penalty|header|headed|tackle"
                   r"|\d+ yards|corner|free-kick|free kick|cross|crossed|goalkeeper|keeper|save|saved|captain|shot|volley)\b", re.IGNORECASE)
_SCORE = re.compile(r"(?<![\d/–-])(\d{1,2})\s?[-–]\s?(\d{1,2})(?![\d/–-])")
# A level score told as a win or a defeat ("beaten 1-1 at home"): the word and the score at most four words apart.
_WIN_OR_LOSS = (r"beat|beaten|beating|lost|lose|loses|losing|loss|defeat|defeated|defeats|won|win|wins|winning|victory|"
                r"victories|downed|thrashed|thrashing|edged|humbled|stunned")
_LEVEL_AS_RESULT = re.compile(rf"\b(?:{_WIN_OR_LOSS})\b(?:[^\w.;:!?]+\w+){{0,4}}?[^\w.;:!?]+(\d{{1,2}})\s?[-–]\s?(\d{{1,2}})(?![\d/–-])"
                              rf"|(?<![\d/–-])(\d{{1,2}})\s?[-–]\s?(\d{{1,2}})\s+(?:\w+\s+)?(?:{_WIN_OR_LOSS})\b", re.IGNORECASE)
_DRAW_WORDS = re.compile(r"\b(?:draw|draws|drew|drawn|drawing|level|penalt\w*|shoot-?out|aggregate|on away goals)\b", re.IGNORECASE)


class StoryError(RuntimeError):
    """The text could not be written; the message says why, for the page."""


COPY_RUN = 160  # characters: a passage this long taken word for word from a background text is a copy
_COLOUR_KEYS = ("old_story", "older_texts", "preview", "match_reports", "club_background", "clubs")


def _colour_texts(value) -> list[str]:
    """The background texts in a notebook (older stories, previews, reports), without the "…" of a cut."""
    if isinstance(value, str):
        return [value.removesuffix(" …")]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _colour_texts(item)]
    if isinstance(value, list):
        return [text for item in value for text in _colour_texts(item)]
    return []


def copied_text(text: str, notebook: dict) -> bool:
    """Whether the text repeats a long passage of a background text word for word."""
    for key in _COLOUR_KEYS:
        for colour in _colour_texts(notebook.get(key)):
            for start in range(0, max(len(colour) - COPY_RUN, 0) + 1, COPY_RUN // 2):
                if len(colour) >= COPY_RUN and colour[start:start + COPY_RUN] in text:
                    return True
    return False


def _known_ground(name: str, facts: str) -> bool:
    """Whether the notebook names the ground, allowing for a capital word before it ("At Anfield Stadium")."""
    words = name.split()
    return any(" ".join(words[start:]) in facts for start in range(max(len(words) - 1, 1)))


# Stock phrases that make a text read like a brochure: a draft that uses one is written again.
STOCK_PHRASES = ("a testament to", "testament to", "defies belief", "powerhouse", "global phenomenon", "footballing journey",
                 "etched", "rollercoaster", "the rest is history", "cemented", "cementing", "unprecedented", "storied", "iconic",
                 "rich history", "unparalleled", "dominant force", "the annals", "stands as a", "a symbol of",
                 "left an indelible", "indelible mark", "a force to be reckoned with", "in the modern era",
                 "the beautiful game", "it is worth noting", "solidified", "showcasing", "a true legend")


_STOCK = re.compile(r"\b(" + "|".join(re.escape(p) for p in sorted(STOCK_PHRASES, key=len, reverse=True)) + r")\b", re.IGNORECASE)
STYLE_FAULT = "the stock phrase"


def stock_phrases(text: str) -> list[str]:
    found = dict.fromkeys(m.group(1).lower() for m in _STOCK.finditer(text))
    return [f"{STYLE_FAULT} '{phrase}': say what you mean in concrete words of your own" for phrase in found]


# A crowd, fee or total written in figures ("69,344", "90,000", "120000"): it must be one of the notebook's numbers.
_BIG_NUMBER = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d{5,})(?![\d,])")
# a count of goals, games or caps ("27 goals"): the notebook must hold the number (an invented 27 goals, 8 Dec 2063)
_COUNT_CLAIM = re.compile(r"(?<![\d.,])(\d{1,3})\s+(?:league\s+|cup\s+|career\s+|international\s+)?(goals|assists|appearances|games|caps|clean sheets|starts)\b", re.I)
_UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
          "seventeen eighteen nineteen").split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_COUNT_WORD = (r"(\d{1,3}|(?:" + "|".join(_TENS) + r")(?:[- ](?:" + "|".join(_UNITS[1:10]) + r"))?|" + "|".join(_UNITS[2:]) + r")")
# "forty consecutive titles", "41 straight league titles", "eight FA Cups in a row"
_RUN_CLAIM = re.compile(r"\b" + _COUNT_WORD + r"\s+(?:(?:consecutive|straight|successive|unbroken)\b|(?:[\w'-]+\s+){0,3}?in\s+a\s+row\b)",
                        re.IGNORECASE)
_RUN_FACT = re.compile(r'"wins_in_a_row": (\d+)')
_TROPHY = re.compile(r"\b(titles?|championships?|crowns?|troph(?:y|ies)|cups?|leagues?|shields?|finals?|doubles?|trebles?)\b",
                     re.IGNORECASE)


def _count(word: str) -> int:
    word = word.lower()
    if word.isdigit():
        return int(word)
    if word in _UNITS:
        return _UNITS.index(word)
    tens, _, unit = word.replace("-", " ").partition(" ")
    return _TENS[tens] + (_UNITS.index(unit) if unit else 0)


def number_faults(text: str, facts: str) -> list[str]:
    """Large figures the notebook does not hold, and runs of titles it does not count ("forty in a row" where it says 41).
    A run is checked only where the notebook counts runs (a club's history)."""
    known = {int(n) for n in re.findall(r"\d+", re.sub(r"(?<=\d),(?=\d{3})", "", facts))}
    faults = [f"the number {n}" for n in dict.fromkeys(m.group(1) for m in _BIG_NUMBER.finditer(text))
              if int(n.replace(",", "")) not in known]
    faults += [f"the count '{m.group(0)}'" for m in _COUNT_CLAIM.finditer(text) if int(m.group(1)) not in known and int(m.group(1)) > 1]
    runs = {int(n) for n in _RUN_FACT.findall(facts)}
    if runs:
        for m in _RUN_CLAIM.finditer(text):
            about = m.group(0) + " " + " ".join(text[m.end():m.end() + 60].split()[:4])  # "forty consecutive Premier League titles"
            if _TROPHY.search(about) and _count(m.group(1)) not in runs:
                faults.append(f"'{m.group(0).strip()}': the notebook counts the runs (longest_run, latest_run); use its number")
    return faults


_ABSENT = (r"\b(?:the\s+)?(injured|suspended|banned|sidelined|unavailable)\s+((?:[A-ZÀ-Ý][\w'’\-]+\s?){1,3})|"
           r"((?:[A-ZÀ-Ý][\w'’\-]+\s){1,3})(?:is|are|was|were|remains|will be|still)\s+(?:still\s+)?"
           r"(injured|suspended|banned|sidelined|unavailable|ruled out|out injured|out with [a-z ]{3,30})")
_ABSENT_RE = re.compile(_ABSENT)
_ABSENT_KEYS = ("injur", "suspend", "ban", "out", "fitness", "missing", "absent", "unavailable", "sent_off", "red")
_TOP_SCORER_RE = re.compile(r"((?:[A-ZÀ-Ý][\w'’\-]+\s?){1,3}),?\s(?:[a-z'’\-]+\s){0,6}(leading|top) scorer|"
                            r"\b(leading|top) scorer[^.;,]{0,30}?((?:[A-ZÀ-Ý][\w'’\-]+\s?){1,3})")
_TOP_SCORER_KEYS = ("scorer", "goals", "scoring")


def _facts_under(notebook: dict, keys: tuple[str, ...]) -> str:
    """The notebook's values whose key holds one of ``keys``, as text, at any depth."""
    out = []
    def walk(value, key=""):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif any(word in key.lower() for word in keys):
            out.append(str(value))
    walk(notebook)
    return " ".join(out).lower()


def absence_faults(text: str, notebook: dict) -> list[str]:
    """A player told as injured, banned or missing, or as the leading scorer, when the notebook says no such thing: the
    writer explained a man's absence from an expected line-up with an injury he did not have (Belmonte, 1 Nov 2026)."""
    faults = []
    absent = _facts_under(notebook, _ABSENT_KEYS)
    for m in _ABSENT_RE.finditer(text):
        name = (m.group(2) or m.group(3) or "").strip()
        word = m.group(1) or m.group(4)
        if name and name.split()[-1].lower() not in absent:
            faults.append(f"'{m.group(0).strip()}': the notebook says nothing of {name} being {word}; a player not in an expected "
                          "line-up is simply not expected to start, so leave the reason out")
    scorers = _facts_under(notebook, _TOP_SCORER_KEYS)
    for m in _TOP_SCORER_RE.finditer(text):
        name = (m.group(1) or m.group(4) or "").strip()
        if name and name.split()[-1].lower() not in scorers:
            faults.append(f"'{m.group(0).strip()}': the notebook does not make {name} a leading scorer; say only what it gives")
    return faults


def hard_faults(faults: list[str]) -> list[str]:
    """The faults that stop a text: wrong facts and copying. A stock phrase left in a second draft is only logged."""
    return [fault for fault in faults if not fault.startswith(STYLE_FAULT)]


def level_score_faults(text: str) -> list[str]:
    """A level score told as a win or a defeat ("beaten 1-1"); a shoot-out or an aggregate in the words or just after is allowed."""
    faults = []
    for match in _LEVEL_AS_RESULT.finditer(text):
        home, away = (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
        if home == away and not _DRAW_WORDS.search(text[match.start():match.end() + 30]):
            faults.append(f"'{match.group(0)}': {home}-{away} is a draw, not a win or a defeat")
    return faults


_TABLE_TALK = re.compile(r"\b(?:points clear|title race|league leaders|top of the table|top of the league\b(?! phase))", re.I)


def own_league_lead_faults(text: str, notebook: dict) -> list[str]:
    """A continental game's piece that opens with a club's own league: its name in the headline, the standfirst or the first
    sentence of the body. The league may come later as background (2 Nov 2026: "Seventeen points clear as Chelsea march
    on" reported a Champions League game)."""
    leagues = {str(v).split(":")[0] for k, v in notebook.items() if k.endswith("_in_their_own_league")}
    if not leagues:
        return []
    paragraphs = [p for p in text.split("\n") if p.strip()]
    opening = " ".join(paragraphs[:2]) + " " + (re.split(r"(?<=[.!?])\s", paragraphs[2], maxsplit=1)[0] if len(paragraphs) > 2 else "")
    faults = [f"'{league}' in the opening: this game is in the {notebook.get('competition')}; lead with it, the league only as background later"
              for league in sorted(leagues) if league.lower() in opening.lower()]
    faults += [f"'{m.group(0)}' in the opening: league table talk; this game is in the {notebook.get('competition')}, lead with it"
               for m in _TABLE_TALK.finditer(opening)]
    return list(dict.fromkeys(faults))


def _plays(value, found: list[str]) -> list[str]:
    """Every how_he_plays in a notebook, each trait once."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "how_he_plays" and isinstance(item, str):
                found += [t.strip() for t in item.split(",") if t.strip()]
            else:
                _plays(item, found)
    elif isinstance(value, list):
        for item in value:
            _plays(item, found)
    return list(dict.fromkeys(found))


_ARTICLES, _WORDS = re.compile(r"\b(?:the|a|an)\b\s*"), re.compile(r"\s+")


def trait_copies(text: str, notebook: dict) -> list[str]:
    """The game's player traits written out in its own words ("he likes to cut inside from the left wing and plays
    one-twos"): the user wants a hint of a player's style, not the trait list read out (3 Nov 2026). Articles aside."""
    flat = " " + _WORDS.sub(" ", _ARTICLES.sub("", re.sub(r"[^a-z' -]", " ", text.lower()))) + " "
    return [f"{STYLE_FAULT} '{trait}' (a player trait in the game's own words): hint at his style in a few words of your "
            "own, at most once, or leave it out" for trait in _plays(notebook, [])
            if len(trait.split()) >= 2 and f" {_ARTICLES.sub('', trait)} " in flat]


def text_faults(text: str, notebook: dict) -> list[str]:
    """Years and scores in the text that the notebook does not have, and copied background text."""
    facts = json.dumps(notebook, ensure_ascii=False, default=str)
    years = set(_YEAR.findall(facts))
    for start, _ in _SEASON.findall(facts):
        years.add(str(int(start) + 1))  # "2061/62" also allows 2062
    scores = set()
    for a, b in _SCORE.findall(facts):
        scores |= {(a, b), (b, a)}
    faults = [f"the year {year}" for year in sorted(set(_YEAR.findall(text)) - years)]
    faults += [f"the score {a}-{b}" for a, b in _SCORE.findall(text) if (a, b) not in scores]
    faults += [f"the ground '{name}'" for name in dict.fromkeys(_GROUND.findall(text)) if not _known_ground(name, facts)]
    faults += number_faults(text, facts)
    faults += level_score_faults(text)
    faults += absence_faults(text, notebook)
    faults += own_league_lead_faults(text, notebook)
    if "home_form_before" in notebook and not notebook.get("match_report"):  # a match known only by its goals
        faults += [f"'{word}': the notebook has no match report, so tell nothing of the play beyond the goals"
                   for word in dict.fromkeys(m.group(0) for m in _PLAY.finditer(text))]
    if copied_text(text, notebook):
        faults.append("passages copied word for word from an older text: write it new, in your own words")
    faults += stock_phrases(text)
    faults += trait_copies(text, notebook)
    return list(dict.fromkeys(faults))


def _write(prompt: str, notebook: dict, voice: str | None = None) -> tuple[str, str]:
    """The checked text and the model that wrote it, in the voice chosen (writing_voices)."""
    prompt = voice_prompt(chosen_voice(voice)) + prompt
    model = get_deepseek_model()
    key = resolve_ai_api_key(None, model=model)
    if not key:
        raise StoryError("No API key is set for the AI model chosen in Settings.")
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(notebook, ensure_ascii=False, default=str)}]
    draft = _request(model, key, messages)
    faults = text_faults(draft, notebook)
    if faults:
        messages += [{"role": "assistant", "content": json.dumps({"text": draft}, ensure_ascii=False)},
                     {"role": "user", "content": "Write the text again, fixing these faults (details not in the notebook must go): "
                                                 + "; ".join(faults)}]
        draft = _request(model, key, messages)
        faults = text_faults(draft, notebook)
        if faults and not hard_faults(faults):
            current_app.logger.info("text kept with style faults: %s", "; ".join(faults))
            faults = []
        if faults:
            current_app.logger.warning("text refused after a second try: %s", "; ".join(faults))
            raise StoryError("The AI model's second draft still had faults: " + "; ".join(faults[:5]))
    return draft, model


def _request(model: str, key: str, messages: list[dict]) -> str:
    answer = request_json(model, key, messages)
    text = answer.get("text")
    if not isinstance(text, str):  # the text under another name ({"story": ...}): the longest string in the answer
        strings = [value for value in answer.values() if isinstance(value, str)]
        text = max(strings, key=len) if strings else None
    if not isinstance(text, str):
        current_app.logger.warning("AI answer without a text: keys %s", list(answer))
        raise StoryError("The AI model's answer was not a text.")
    text = text.strip()
    if not text:
        raise StoryError("The AI model returned an empty text.")
    return text


def request_json(model: str, key: str, messages: list[dict], temperature: float = 0.5, retry: bool = True) -> dict:
    """The model's answer, a JSON object; an answer that is not JSON is asked for once more."""
    url, headers, payload = ai_request_config(model=model, api_key=key, messages=messages, max_tokens=MAX_TOKENS,
                                              temperature=temperature, response_format={"type": "json_object"})
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=ai_timeout(model, TIMEOUT_SECONDS))
    except requests.RequestException as exc:
        raise StoryError(f"The AI model could not be reached: {exc}") from exc
    if response.status_code != 200:
        current_app.logger.warning("story request failed: %s %s", response.status_code, response.text[:500])
        raise StoryError(f"The AI model answered with an error ({response.status_code}).")
    content, finish = "", None
    try:
        check_ollama_answer(response.json(), payload)
        content, finish = chat_content(response.json())
        answer = json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        current_app.logger.warning("AI answer not JSON (finish %s): %r", finish, content[:300])
        if not retry:
            raise StoryError("The AI model's answer was not a text.") from exc
        again = messages + [{"role": "assistant", "content": content[:4000]},
                            {"role": "user", "content": 'Answer again, only with a JSON object: {"text": "..."}.'}]
        return request_json(model, key, again, temperature, retry=False)
    if not isinstance(answer, dict):
        current_app.logger.warning("AI answer not a JSON object: %r", content[:300])
        raise StoryError("The AI model's answer was not a text.")
    return answer


def request_with_questions(model: str, key: str, messages: list[dict], tools: list[dict], answer, max_questions: int,
                           temperature: float = 0.5) -> tuple[dict, list[dict]]:
    """The model's JSON answer after it has asked up to ``max_questions`` questions with the tools (27 Oct 2026): each
    question is answered by ``answer(name, arguments) -> str`` and the conversation goes on. Returns the answer and the
    questions with their answers. A provider that refuses tools gets the plain request."""
    from app.services.ai_settings import ai_model_provider

    ollama = ai_model_provider(model) == "ollama"
    messages, asked = list(messages), []
    for _ in range(max_questions + 3):  # every question, the last turn, and a little room; never an endless conversation
        url, headers, payload = ai_request_config(model=model, api_key=key, messages=messages, max_tokens=MAX_TOKENS,
                                                  temperature=temperature, response_format={"type": "json_object"})
        if len(asked) < max_questions:
            payload["tools"] = tools
        elif not ollama and asked:
            payload["tools"], payload["tool_choice"] = tools, "none"  # the questions are over: write (the history names the tools)
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=ai_timeout(model, TIMEOUT_SECONDS))
        except requests.RequestException as exc:
            raise StoryError(f"The AI model could not be reached: {exc}") from exc
        if response.status_code != 200:
            current_app.logger.warning("questions request failed: %s %s", response.status_code, response.text[:500])
            if not asked:  # the provider takes no tools: write without questions
                return request_json(model, key, messages, temperature), []
            raise StoryError(f"The AI model answered with an error ({response.status_code}).")
        data = response.json()
        message = data["choices"][0]["message"] if "choices" in data else data.get("message") or {}
        calls = message.get("tool_calls") or []
        if not calls:
            break
        if len(asked) >= max_questions:  # it asked again after the last question: the next turn may not
            messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
            messages += [{"role": "tool", "content": '{"error": "no more questions"}', **({"tool_name": (c.get("function") or {}).get("name")}
                          if ollama else {"tool_call_id": c.get("id")})} for c in calls]
            continue
        messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        for call in calls:
            function = call.get("function") or {}
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments or "{}")
                except ValueError:
                    arguments = {}
            reply = answer(function.get("name", ""), arguments if isinstance(arguments, dict) else {})
            asked.append({"question": f"{function.get('name')}({json.dumps(arguments, ensure_ascii=False)})", "answer": reply})
            messages.append({"role": "tool", "content": reply, **({"tool_name": function.get("name")} if ollama else {"tool_call_id": call.get("id")})})
        if len(asked) >= max_questions:  # a model told only by tool_choice sent back blank text (27 Oct 2026): say it in words
            messages.append({"role": "user", "content": "That was the last question. Write the piece now, as the JSON object asked for."})
    else:
        raise StoryError("The AI model's answer was not a text: it kept asking questions.")
    content = message.get("content") or ""
    try:
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("not an object")
    except ValueError:
        current_app.logger.warning("AI answer after questions not JSON: %r", content[:300])
        again = messages + [{"role": "assistant", "content": content[:4000]},
                            {"role": "user", "content": "Answer again, only with the JSON object asked for."}]
        if not ollama and asked:  # a conversation with tool turns needs the tools named, even when none may be called
            url, headers, payload = ai_request_config(model=model, api_key=key, messages=again, max_tokens=MAX_TOKENS,
                                                      temperature=temperature, response_format={"type": "json_object"})
            payload["tools"], payload["tool_choice"] = tools, "none"
            response = requests.post(url, headers=headers, json=payload, timeout=ai_timeout(model, TIMEOUT_SECONDS))
            try:
                result = json.loads(chat_content(response.json())[0])
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise StoryError("The AI model's answer was not a text.") from exc
        else:
            result = request_json(model, key, again, temperature, retry=False)
    return result, asked


CLUB_PROMPT = (
    "You write a football club's reference history for its page in a private Football Manager archive: the kind a "
    "careful club historian or a good encyclopedia would write. It is not a news article, a feature or a fan's memoir. "
    "Return a JSON object {\"summary\": \"...\", \"history\": \"...\"}.\n\n"
    "summary: 80-120 words, one paragraph: what the club is, where it plays, its standing and its manager now, and its "
    "principal honours.\n\n"
    "history: 800-1200 words, chronological, in 4-7 sections, each with a plain, informative heading on its own line "
    "(for example 'Before 2022', 'The Dimario era', 'European record', 'Records and players', 'Rivalries', 'Ownership "
    "and finances', 'The present'), a blank line before the prose, paragraphs under 90 words with a blank line between "
    "them. Plain text: no Markdown, bullets, title or preamble.\n\n"
    "WHAT TO COVER, where the notebook holds it: the club's origins and ground (club_background); its eras season by "
    "season in outline, with the trophies and the years they were won (honours, seasons); its runs of titles "
    "(longest_run, latest_run); its notable finals, won and lost, one sentence each with the score (great_finals); its "
    "record wins and defeats (biggest_wins, heaviest_defeats); its leading scorers and appearance makers "
    "(top_league_scorers, most_league_games); the legends and icons it honours (club_relationships); its rivalries with "
    "the head-to-head record (against_rivals); its managers since the game began, with their records and how they left "
    "(managers); its owners and money now (club_background, finances); and where it stands "
    "at the game date.\n\n"
    "STYLE. Third person throughout: never 'I', 'we', 'our' or 'you'. Past tense for the history, present tense for the "
    "club now. Clear, precise and readable, with the occasional well-chosen detail, but no scene-setting openings, no "
    "imagined atmosphere, no quotes, no hyperbole and no rhetorical questions. Start with the club's beginnings, not "
    "with a match. Prefer dates, scores and names to adjectives.\n\n"
    "FACTS. Every name, date, score, crowd, season, competition and number comes from the notebook. Counts come from the "
    "notebook's fields (times, longest_run, latest_run, against_rivals); never count yourself. club_background is the "
    "curator's text: use its facts about the years before the save, the ground and the owners, but the notebook's other "
    "fields win where they disagree, its claims about the present are only true as of when it was written, and its "
    "sentences are never copied. Do not mention notebooks, saves, databases or data. Write in British English. "
    "Avoid stock phrases such as 'testament to', 'storied', 'iconic' and 'etched'."
)


def _connect():
    return db.engines[world_models.BIND].connect()


def _game_date(notebook: dict) -> date | None:
    return date.fromisoformat(notebook["game_date"]) if notebook.get("game_date") else None


def keep_version(story, kind: str, subject_uid: int, year: int) -> None:
    """Keep the text a new story is about to replace, so it can be restored."""
    if story.text:
        db.session.add(StoryVersion(kind=kind, subject_uid=subject_uid, year=year, text=story.text, source=story.source,
                                    model=story.model, game_date=story.game_date, written_at=story.written_at or _now()))


def restore_version(version_id: int) -> tuple[str, int, int]:
    """Make an earlier version the story again; the story it replaces becomes a version itself."""
    version = db.session.get(StoryVersion, version_id)
    if version is None:
        raise StoryError("That earlier version is not in the archive.")
    if version.kind == "club":
        return _restore_club_text(version)
    if version.kind == "season":
        story = db.session.get(ClubSeasonStory, (version.subject_uid, version.year)) or ClubSeasonStory(
            club_uid=version.subject_uid, season=version.year)
    else:
        story = db.session.get(EditionStory, (version.subject_uid, version.year)) or EditionStory(
            competition_uid=version.subject_uid, year=version.year)
    keep_version(story, version.kind, version.subject_uid, version.year)
    story.text, story.source, story.model, story.game_date, story.written_at = (
        version.text, version.source, version.model, version.game_date, version.written_at)
    db.session.add(story)
    db.session.delete(version)
    db.session.commit()
    return version.kind, version.subject_uid, version.year


def _club_text_of(text: ClubText) -> str:
    return "\n\n".join(part.strip() for part in (text.profile_summary, text.historical_context) if part and part.strip())


def write_club_text(club_uid: int) -> ClubText:
    """A new "About the club" (a summary and a factual history) from the club's notebook; the text it replaces is
    kept as an earlier version (kind "club", year 0), the curator's own as well. It takes no voice: it is the
    club's reference history, not a story."""
    with _connect() as connection:
        notebook = story_notebooks.club_notebook(connection, club_uid)
    if notebook is None:
        raise StoryError("This club is not in the world database.")
    if not notebook["seasons"]:
        raise StoryError("The save has no league seasons for this club, so there is nothing to write from.")
    model = get_deepseek_model()
    key = resolve_ai_api_key(None, model=model)
    if not key:
        raise StoryError("No API key is set for the AI model chosen in Settings.")
    messages = [{"role": "system", "content": CLUB_PROMPT},
                {"role": "user", "content": json.dumps(notebook, ensure_ascii=False, default=str)}]
    summary, history, faults = "", "", []
    for _ in range(2):
        answer = request_json(model, key, messages)
        summary, history = (str(answer.get(k) or "").strip() for k in ("summary", "history"))
        if not summary or not history:
            raise StoryError("The AI model's answer was not a summary and a history.")
        faults = text_faults(f"{summary}\n\n{history}", notebook)
        if not faults:
            break
        messages += [{"role": "assistant", "content": json.dumps({"summary": summary, "history": history}, ensure_ascii=False)},
                     {"role": "user", "content": "Write both texts again, fixing these faults: "
                                                 + "; ".join(faults)}]
    if hard_faults(faults):
        raise StoryError("The AI model's second draft still had faults: " + "; ".join(hard_faults(faults)[:5]))
    if faults:
        current_app.logger.info("club history kept with style faults: %s", "; ".join(faults))
    text = db.session.get(ClubText, club_uid) or ClubText(club_uid=club_uid)
    if _club_text_of(text):
        db.session.add(StoryVersion(kind="club", subject_uid=club_uid, year=0, text=_club_text_of(text),
                                    source=text.source or "curator", model=text.model, written_at=text.written_at or _now()))
    text.profile_summary, text.historical_context = summary, history
    text.source, text.model, text.written_at = GENERATED, model, _now()
    db.session.add(text)
    db.session.commit()
    return text


def _restore_club_text(version: StoryVersion) -> tuple[str, int, int]:
    """A club's earlier text back: its first paragraph is the summary, the rest the history."""
    text = db.session.get(ClubText, version.subject_uid) or ClubText(club_uid=version.subject_uid)
    if _club_text_of(text):
        db.session.add(StoryVersion(kind="club", subject_uid=version.subject_uid, year=0, text=_club_text_of(text),
                                    source=text.source or "curator", model=text.model, written_at=text.written_at or _now()))
    summary, _, history = version.text.partition("\n\n")
    text.profile_summary, text.historical_context = summary.strip(), history.strip() or None
    text.source = None if version.source == "curator" else version.source
    text.model, text.written_at = version.model, version.written_at
    db.session.add(text)
    db.session.delete(version)
    db.session.commit()
    return version.kind, version.subject_uid, version.year


def write_club_season_story(club_uid: int, season: int, voice: str | None = None) -> ClubSeasonStory:
    with _connect() as connection:
        notebook = story_notebooks.club_season_notebook(connection, club_uid, season)
    if notebook is None:
        raise StoryError("This club is not in the world database.")
    if not notebook["results"]:
        raise StoryError("The save has no played matches for this club and season.")
    text, model = _write(SEASON_PROMPT, notebook, voice)
    story = db.session.get(ClubSeasonStory, (club_uid, season)) or ClubSeasonStory(club_uid=club_uid, season=season)
    keep_version(story, "season", club_uid, season)
    story.text, story.source, story.model, story.game_date, story.written_at = text, GENERATED, model, _game_date(notebook), _now()
    db.session.add(story)
    db.session.commit()
    return story


def write_edition_story(competition_uid: int, year: int, voice: str | None = None) -> EditionStory:
    with _connect() as connection:
        notebook = story_notebooks.edition_notebook(connection, competition_uid, year)
    if notebook is None:
        raise StoryError("This edition is not in the world database.")
    if not notebook["games_played"]:
        raise StoryError("The save has no played matches for this edition.")
    text, model = _write(EDITION_PROMPT, notebook, voice)
    story = db.session.get(EditionStory, (competition_uid, year)) or EditionStory(competition_uid=competition_uid, year=year)
    keep_version(story, "edition", competition_uid, year)
    story.text, story.source, story.model, story.game_date, story.written_at = text, GENERATED, model, _game_date(notebook), _now()
    db.session.add(story)
    db.session.commit()
    return story


def match_report_of(fixture: dict) -> MatchText | None:
    return db.session.query(MatchText).filter_by(date=date.fromisoformat(str(fixture["date"])), home_uid=fixture["home_uid"],
                                                 away_uid=fixture["away_uid"], kind=REPORT).first()


def write_match_report(fixture_id: int, replace_own: bool = False, voice: str | None = None) -> MatchText:
    """Write the report of a played match. A report written by the curator or the game is
    only replaced with ``replace_own``."""
    with _connect() as connection:
        fixture = q._one(connection, "select * from fixtures where id = :id", id=fixture_id)
        if fixture is None:
            raise StoryError("This match is not in the world database.")
        if fixture["home_goals"] is None:
            raise StoryError("This match is not played yet.")
        notebook = story_notebooks.match_notebook(connection, fixture_id)
    prompt = MATCH_PROMPT if notebook.get("match_report") else MATCH_SUMMARY_PROMPT
    existing = match_report_of(fixture)
    if existing and existing.source in (CURATOR, GAME) and not replace_own:
        raise StoryError("This match already has a report written by you or by the game; confirm to replace it.")
    text, _model = _write(prompt, notebook, voice)
    report = existing or MatchText(date=date.fromisoformat(str(fixture["date"])), home_uid=fixture["home_uid"],
                                   away_uid=fixture["away_uid"], kind=REPORT)
    report.text, report.source, report.written_at = text, MATCH_GENERATED, _now()
    db.session.add(report)
    db.session.commit()
    return report
