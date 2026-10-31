"""JSON-schema building blocks for the Actions API catalog."""

from __future__ import annotations

from typing import Any




_ENTITY_TYPES = [
    "player",
    "club",
    "competition",
    "competition_edition",
    "match",
    "season",
]

def _object_schema(
    properties: dict[str, dict[str, Any]] | None = None,
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    # Although OpenAPI 3.1 permits an empty required array, the Custom GPT
    # schema importer applies a stricter validator. Omit it entirely when no
    # fields are required.
    if required:
        schema["required"] = required
    return schema

def _integer(description: str) -> dict[str, Any]:
    return {"type": "integer", "description": description}

def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}

def _boolean(description: str) -> dict[str, Any]:
    return {"type": "boolean", "description": description, "default": False}

def _club_patch_schema(
    description: str, *, include_name: bool = True
) -> dict[str, Any]:
    properties = {
        "name": _string("Canonical club name."),
        "short_name": _string("Short display name."),
        "fm_uid": _string("Authoritative Football Manager club UID."),
        "country": _string("Club country."),
        "city": _string("Home city."),
        "home_stadium": _string("Current home stadium."),
        "founded_year": _integer("Four-digit founding year."),
        "current_manager_name": _string("Current manager's name."),
        "profile_summary": _string(
            "Polished short club biography for the club profile."
        ),
        "historical_context": _string(
            "Detailed editorial history and universe-specific context."
        ),
        "is_archive_club": {
            "type": "boolean",
            "description": "Whether this is a primary managed archive club.",
        },
    }
    if not include_name:
        properties.pop("name")
    schema = _object_schema(properties)
    schema["description"] = description
    return schema

def _competition_patch_schema(description: str) -> dict[str, Any]:
    schema = _object_schema(
        {
            "name": _string("Canonical competition name."),
            "competition_type": {
                "type": "string",
                "enum": [
                    "league",
                    "domestic_cup",
                    "continental_cup",
                    "super_cup",
                    "friendly",
                    "international",
                    "other",
                    "unknown",
                ],
            },
            "country": _string("Competition country, when applicable."),
            "level": _integer("Competition pyramid level, when applicable."),
            "fm_uid": _string("Authoritative Football Manager competition UID."),
        }
    )
    schema["description"] = description
    return schema

def _player_patch_schema(description: str) -> dict[str, Any]:
    schema = _object_schema(
        {
            "full_name": _string("Canonical full name."),
            "known_as": _string("Preferred display name."),
            "fm_uid": _string("Authoritative Football Manager player UID."),
            "club_id": _integer("Current club's stable archive ID."),
            "date_of_birth": _string("ISO date in YYYY-MM-DD format."),
            "birthplace": _string("Place of birth."),
            "nationality": _string("Primary nationality."),
            "second_nationality": _string("Second nationality."),
            "primary_position": _string("Primary playing position."),
            "secondary_positions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Other playing positions.",
            },
            "preferred_foot": {
                "type": "string",
                "enum": ["Left", "Right", "Either"],
            },
            "height_cm": _integer("Height in centimetres."),
            "weight_kg": _integer("Weight in kilograms."),
            "is_active": {"type": "boolean"},
            "is_newgen": {"type": "boolean"},
            "playing_status": {
                "type": "string",
                "enum": ["unknown", "active", "retired"],
            },
            "employment_status": {
                "type": "string",
                "enum": ["unknown", "employed", "unemployed", "retired"],
            },
            "current_role": _string("Current playing or staff role."),
            "current_organization_name": _string(
                "Current club, national team, or employer."
            ),
            "retired_date": _string("ISO retirement date."),
            "identity_status": {
                "type": "string",
                "enum": ["confirmed", "provisional", "unknown"],
            },
            "career_narrative": _string(
                "Polished long-form player biography."
            ),
        }
    )
    schema["description"] = description
    return schema

def _split_selection_schema() -> dict[str, Any]:
    categories = [
        "career_spells",
        "honours",
        "career_seasons",
        "club_associations",
        "events",
        "goals",
        "appearances",
        "player_seasons",
        "transfers",
        "national_selections",
        "national_goals",
    ]
    return _object_schema(
        {
            category: {
                "type": "array",
                "items": {"type": "integer"},
                "description": f"Selected {category.replace('_', ' ')} record IDs.",
            }
            for category in categories
        }
    )

def _split_source_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "club_id": _integer("Correct current club ID for the source player."),
            "birthplace": _string("Correct birthplace for the source player."),
            "nationality": _string("Correct nationality for the source player."),
            "primary_position": _string(
                "Correct primary position for the source player."
            ),
            "playing_status": {
                "type": "string",
                "enum": ["unknown", "active", "retired"],
            },
            "employment_status": {
                "type": "string",
                "enum": [
                    "unknown",
                    "employed",
                    "unemployed",
                    "retired",
                    "none",
                ],
            },
            "current_role": _string("Correct current role."),
            "current_organization_name": _string(
                "Correct current organization."
            ),
            "retired_date": _string("ISO retirement date."),
            "is_active": {"type": "boolean"},
        }
    )

def _matchday_goal_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "scorer_name": _string("Goalscorer's archive-visible name."),
            "minute": {"type": "integer", "minimum": 0, "maximum": 130},
            "stoppage_time": {
                "type": "integer",
                "minimum": 0,
                "maximum": 30,
                "default": 0,
            },
            "raw_text": _string("Optional original scorer text."),
            "is_penalty": {"type": "boolean", "default": False},
            "is_own_goal": {"type": "boolean", "default": False},
        },
        required=["scorer_name", "minute"],
    )

def _matchday_fixture_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "home_team": _string("Home team name."),
            "away_team": _string("Away team name."),
            "match_date": _string(
                "Authoritative fixture ISO date; reschedules the matched fixture "
                "or exact recorded result."
            ),
            "group_name": _string("International or cup group name, when known."),
            "home_score": {"type": "integer", "minimum": 0, "maximum": 99},
            "away_score": {"type": "integer", "minimum": 0, "maximum": 99},
            "home_goals": {
                "type": "array",
                "items": _matchday_goal_schema(),
            },
            "away_goals": {
                "type": "array",
                "items": _matchday_goal_schema(),
            },
            "went_to_extra_time": {"type": "boolean", "default": False},
            "went_to_penalties": {"type": "boolean", "default": False},
            "home_penalty_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 99,
            },
            "away_penalty_score": {
                "type": "integer",
                "minimum": 0,
                "maximum": 99,
            },
        },
        required=["home_team", "away_team"],
    )

def _article_entity_link_schema() -> dict[str, Any]:
    return _object_schema(
        {
            "entity_type": {
                "type": "string",
                "enum": ["person", "club", "competition", "edition", "club_season", "match", "nation"],
            },
            "entity_id": _integer("FM unique id; for a match, the match_id."),
            "year": _integer("For an edition its end year; for a club_season its start year."),
            "label": _string("Optional display label used in the story."),
        },
        required=["entity_type", "entity_id"],
    )
