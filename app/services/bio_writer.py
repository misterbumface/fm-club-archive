"""Write a player bio from the save data (docs/player-bios-plan.md).

``write_bio`` builds the notebook (``player_notebook``), asks the AI model chosen in
Settings for a summary and a full profile, checks that every year and score in the text
is in the notebook, and saves the result in ``person_bios``. One wrong draft gets one more
try with its faults listed; a second wrong draft is refused.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

import requests
from flask import current_app

from app.extensions import db
from app.models import world as world_models
from app.models.person_bio import GENERATED, PersonBio, PersonOldText
from app.services.ai_settings import ai_request_config, ai_timeout, chat_content, check_ollama_answer, get_deepseek_model, resolve_ai_api_key
from app.services.player_notebook import player_notebook
from app.services.story_writer import hard_faults, number_faults, stock_phrases
from app.services.writing_voices import chosen as chosen_voice, voice_prompt

TIMEOUT_SECONDS = 180
MAX_TOKENS = 4000

SYSTEM_PROMPT = (
    "You are the senior football features writer for a private Football Manager history "
    "archive. From the supplied notebook, write two texts about one player and return them "
    'as a JSON object: {"summary": "...", "profile": "..."}.\n\n'
    "summary: about 100 words, one paragraph, shown under the player's name. Who the player "
    "is, where the career happened and what defines it. No headings.\n\n"
    "profile: a distinctive feature of 450-900 words, according to the depth of evidence. Find "
    "the career's revealing shape: a decisive final, a long partnership with a teammate, an "
    "improbable move, an international thread, a peak season, a second act. Open on the "
    "strongest supported image or idea, not automatically on birth, debut or a career total. "
    "Give it 3-5 short, player-specific section headings, each on its own line with a blank "
    "line before the prose; avoid stock headings such as Career overview. Keep paragraphs "
    "under 90 words. Plain text: no Markdown, bullets, title, preamble or sources note.\n\n"
    "The page already shows the full trophy cabinet and the season table, so explain the "
    "honours and numbers that shape the story; never list them all. player.main_position is where he plays: never "
    "give him another position, and tell a goalkeeper's story through saves, clean sheets and goals conceded, not goals "
    "scored. teammates proves shared "
    "seasons and trophies only, not friendship, assists or chemistry. goals_in_finals gives "
    "the score from the player's side; a goal there may be called a goal in the final, but "
    "never 'the winner' unless the score makes it one. club_league_position is the club's "
    "place in the table built from the results.\n\n"
    "The hard facts must come from the notebook and nothing may contradict it: every name, "
    "date, number, club, score, result, competition, fee and move. old_bio, when present, is an older text: use it for colour and for facts the "
    "notebook does not cover (such as birthplace), but where it disagrees with the notebook, "
    "the notebook wins and the old claim is left out silently. Follow player.career_status: "
    "an active player's career is still going on: tell past seasons in the past tense, use the "
    "present tense only for where the player stands now, and never call a season the last or "
    "final one; for the others, write in the past tense but do not claim a retirement, an "
    "injury or a reason. Do not say a club retained, defended or regained a title unless the "
    "notebook shows the seasons before. Do not name stadiums, managers, chairmen, cities or "
    "other people and places that are not in the notebook, even when you know them. Around "
    "the facts, bring the career to life: emotion, atmosphere, character, the drama of the big "
    "days and short quotes in character. The colour must add no new hard facts. Do not "
    "mention notebooks, saves, "
    "databases or data. Avoid stock phrases such as 'footballing journey', 'etched his name', "
    "'testament to', 'rollercoaster' and 'the rest is history'. Write with life, colour, "
    "specificity and rhythm, in British English."
)

_YEAR = re.compile(r"\b(1[89]\d\d|2[01]\d\d)\b")
_SEASON = re.compile(r"\b(\d{4})/(\d{2})\b")
_SCORE = re.compile(r"(?<![\d/])(\d{1,2})\s?[-–]\s?(\d{1,2})(?![\d/])")


class BioError(RuntimeError):
    """The bio could not be written; the message says why, for the page."""


@dataclass
class Draft:
    summary: str
    profile: str


def old_bio_of(uid: int) -> str | None:
    """The old archive's bio, by FM id: FM's own words first, else the archive's text."""
    old = db.session.get(PersonOldText, uid)
    if old is None:
        return None
    return old.raw_source or old.text or None


def write_bio(uid: int, *, api_key: str | None = None, voice: str | None = None) -> PersonBio:
    """Write and save the bio of one person; raises BioError with a reason for the page."""
    with db.engines[world_models.BIND].connect() as connection:
        notebook = player_notebook(connection, uid, old_bio=old_bio_of(uid))
    if notebook is None:
        raise BioError("This person is not in the world database.")
    if not notebook["seasons"]:
        raise BioError("The save has no season statistics for this person, so there is nothing to write from.")
    model = get_deepseek_model()
    key = resolve_ai_api_key(api_key, model=model)
    if not key:
        raise BioError("No API key is set for the AI model chosen in Settings.")

    messages = [{"role": "system", "content": voice_prompt(chosen_voice(voice)) + SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(notebook, ensure_ascii=False, default=str)}]
    draft = _request(model, key, messages)
    faults = bio_faults(draft, notebook)
    if faults:
        messages += [{"role": "assistant", "content": json.dumps({"summary": draft.summary, "profile": draft.profile})},
                     {"role": "user", "content": "Write both texts again, fixing these faults (details not in the notebook must go): "
                                                 + "; ".join(faults)}]
        draft = _request(model, key, messages)
        faults = hard_faults(bio_faults(draft, notebook))
        if faults:
            raise BioError("The AI model's second draft still had faults: " + "; ".join(faults[:5]))

    bio = db.session.get(PersonBio, uid) or PersonBio(fm_uid=uid)
    bio.summary, bio.profile, bio.source, bio.model = draft.summary, draft.profile, GENERATED, model
    bio.game_date = date.fromisoformat(notebook["game_date"]) if notebook["game_date"] else None
    bio.written_at = datetime.now(UTC).replace(tzinfo=None)
    db.session.add(bio)
    db.session.commit()
    return bio


def bio_faults(draft: Draft, notebook: dict) -> list[str]:
    """Years and scores in the texts that the notebook does not have."""
    facts = json.dumps(notebook, ensure_ascii=False, default=str)
    years = set(_YEAR.findall(facts))
    for start, _ in _SEASON.findall(facts):
        years.add(str(int(start) + 1))  # "2061/62" also allows 2062
    scores = set()
    for final in notebook.get("goals_in_finals", []):
        for score in filter(None, (final.get("score"), final.get("penalties"))):
            a, b = score.split("-")
            scores |= {(a, b), (b, a)}
    faults = []
    text = f"{draft.summary}\n{draft.profile}"
    for year in sorted(set(_YEAR.findall(text)) - years):
        faults.append(f"the year {year}")
    for a, b in _SCORE.findall(text):
        if (a, b) not in scores:
            faults.append(f"the score {a}-{b}")
    faults += number_faults(text, facts) + stock_phrases(text)
    return list(dict.fromkeys(faults))


def _request(model: str, key: str, messages: list[dict]) -> Draft:
    url, headers, payload = ai_request_config(model=model, api_key=key, messages=messages, max_tokens=MAX_TOKENS,
                                              temperature=0.5, response_format={"type": "json_object"})
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=ai_timeout(model, TIMEOUT_SECONDS))
    except requests.RequestException as exc:
        raise BioError(f"The AI model could not be reached: {exc}") from exc
    if response.status_code != 200:
        current_app.logger.warning("bio request failed: %s %s", response.status_code, response.text[:500])
        raise BioError(f"The AI model answered with an error ({response.status_code}).")
    try:
        check_ollama_answer(response.json(), payload)
        content = json.loads(chat_content(response.json())[0])
        summary, profile = content["summary"].strip(), content["profile"].strip()
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        raise BioError("The AI model's answer was not a summary and a profile.") from exc
    if not summary or not profile:
        raise BioError("The AI model returned an empty text.")
    return Draft(summary, profile)

