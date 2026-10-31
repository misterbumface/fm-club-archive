"""The voices the archive's texts can be written in, each after a great sportswriter (the models know them),
and the house style. Chosen at each "Write" button; Settings holds the default the automatic jobs use.

The voice changes how a text reads, never what it says: every name, score and number stays the save's. In a
first-person voice the narrator may stand in the crowd, the one invention allowed.
"""

from __future__ import annotations

from flask import has_app_context

from app.extensions import db
from app.models import AppSetting

HOUSE = "house"
DEFAULT_VOICE = "gonzo"
VOICE_SETTING_KEY = "writing_voice"

_FIRST_PERSON = (" A first-person narrator may stand in the crowd or the press box: that presence is the only "
                 "invention allowed, and the narrator sees only what the notebook holds.")

VOICES: dict[str, tuple[str, str]] = {
    "gonzo": ("Gonzo, after Hunter S. Thompson",
              "Write as Hunter S. Thompson would have: gonzo journalism. A first-person witness who is inside the story, "
              "not above it: savage, funny, paranoid, furious and in love at the same time. Wild, exact metaphors; long "
              "rolling sentences that break into short, brutal ones; contempt for the pompous and the dull; the crowd as "
              "a beast with a thousand throats. The madness comes from football, not from drink or drugs. A sample of "
              "the energy (the facts in it are invented): 'By the eighty-ninth minute the whole stand had gone feral, "
              "sixty thousand people howling at a referee who looked like a tax inspector caught in a riot, and I knew "
              "then that nothing about this club would ever again be sane.'" + _FIRST_PERSON),
    "mcilvanney": ("Grand and lyrical, after Hugh McIlvanney",
                   "Write as Hugh McIlvanney would have: the great British sportswriter's voice, authoritative, lyrical "
                   "and humane, with long, balanced sentences that land on a line the reader remembers. Sport as the "
                   "drama of character under pressure; respect for greatness, a sharp eye for vanity."),
    "galeano": ("Football as myth, after Eduardo Galeano",
                "Write as Eduardo Galeano would have in Football in Sun and Shadow: short, poetic fragments, each a small "
                "fable; football as memory, magic, injustice and joy; the people in the stands as much as the players."),
    "hornby": ("The fan's confession, after Nick Hornby",
               "Write as Nick Hornby did in Fever Pitch: the first-person confession of an obsessive supporter whose "
               "moods and years are measured in results; funny, self-mocking, painfully honest about obsession and the "
               "defeats that still hurt." + _FIRST_PERSON),
    "talese": ("Scene by scene, after Gay Talese",
               "Write as Gay Talese would have: New Journalism, built scene by scene, with the patience of a novelist: "
               "the telling detail, the people at the edges, the quiet before the noise."),
    HOUSE: ("House style", ""),
}


def voice_label(voice: str | None) -> str:
    return VOICES.get(voice or "", VOICES[HOUSE])[0]


def voice_prompt(voice: str | None) -> str:
    """The voice's part of a system prompt (put first), or nothing for the house style."""
    instruction = VOICES.get(voice or "", ("", ""))[1]
    if not instruction:
        return ""
    return (f"THE VOICE. {instruction} The voice decides how the text reads; the rules below still decide what it may "
            "say and how long it is.\n\n")


def default_voice() -> str:
    """The voice chosen in Settings, which automatic jobs use and every "Write" button proposes."""
    if not has_app_context():
        return DEFAULT_VOICE
    setting = AppSetting.query.filter_by(key=VOICE_SETTING_KEY).first()
    return setting.value if setting and setting.value in VOICES else DEFAULT_VOICE


def set_default_voice(voice: str) -> None:
    if voice not in VOICES:
        raise ValueError("Choose one of the voices in the list.")
    setting = AppSetting.query.filter_by(key=VOICE_SETTING_KEY).first()
    if setting is None:
        db.session.add(AppSetting(key=VOICE_SETTING_KEY, value=voice))
    else:
        setting.value = voice


def chosen(voice: str | None) -> str:
    """A voice from a form, or the default when none (or an unknown one) is given."""
    return voice if voice in VOICES else default_voice()
