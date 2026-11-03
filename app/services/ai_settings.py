"""Persistent model selections shared by the archive's AI workflows."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from flask import current_app, has_app_context

from app.extensions import db
from app.models import AppSetting

DEEPSEEK_MODEL_SETTING_KEY = "deepseek_model"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_OPENAI_MODEL = "gpt-6-luna"
# Models the archive offered before, and what a saved choice of them now means.
# DeepSeek retired "deepseek-v4-flash" in October 2026 (its requests went to V4.1 Flash): its name is "deepseek-flash".
RENAMED_MODELS = {"gpt-5.6-luna": "gpt-6-luna", "deepseek-v4-flash": "deepseek-flash"}
# What the texts written with a former model name say they were written with.
FORMER_LABELS = {"gpt-5.6-luna": "GPT-5.6 Luna", "deepseek-v4-flash": "DeepSeek V4 Flash"}
DEFAULT_OPENAI_REASONING_EFFORT = "low"


@dataclass(frozen=True, slots=True)
class DeepSeekModelOption:
    value: str
    label: str
    strapline: str
    description: str
    recommended: bool = False
    provider: str = "deepseek"
    supports_images: bool = False


DEEPSEEK_MODEL_OPTIONS = (
    DeepSeekModelOption(
        value="deepseek-v4-pro",
        label="DeepSeek V4 Pro",
        strapline="Best editorial depth",
        description=(
            "Use for season features, competition stories and player bios."
        ),
        recommended=True,
    ),
    DeepSeekModelOption(
        value="deepseek-flash",
        label="DeepSeek V4.1 Flash",
        strapline="Faster and more economical",
        description=(
            "Use when speed matters more than maximum narrative and reasoning depth."
        ),
    ),
    DeepSeekModelOption(
        value="gpt-6-luna",
        label="GPT-6 Luna",
        strapline="Fast, economical reasoning",
        description=(
            "OpenAI's efficient GPT-6 model for focused, high-volume writing ($0.10 in, $0.50 out per "
            "million tokens). It runs at low reasoning effort to keep jobs quick and economical."
        ),
        provider="openai",
        supports_images=True,
    ),
)
ALLOWED_DEEPSEEK_MODELS = frozenset(
    option.value for option in DEEPSEEK_MODEL_OPTIONS
)

# Local models through Ollama (https://ollama.com) on this Mac: free, private, no key. A model is chosen
# as "ollama:<name>" ("ollama:qwen3:14b") from the models Ollama has installed.
OLLAMA_PREFIX = "ollama:"
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
# The context each request asks Ollama for: the prompt (estimated at OLLAMA_CHARS_PER_TOKEN, a little under the measured rate for
# JSON full of numbers and foreign names) plus the answer, in steps, between the limits. A prompt larger than the
# context is cut from its start by Ollama without an error, and the start holds the instructions. A 19-season
# player's bio is about 21 000 tokens; 48K stays within a 24 GB Mac for a 12-14B model.
OLLAMA_CONTEXT_MIN = 8192
OLLAMA_CONTEXT_MAX = 49152
OLLAMA_CONTEXT_STEP = 4096
OLLAMA_CHARS_PER_TOKEN = 2.4  # measured: 2.64 with Gemma 4 on a 19-season bio (23 620 tokens)
OLLAMA_CONTEXT = OLLAMA_CONTEXT_MAX  # the most a request asks for
OLLAMA_TIMEOUT_SECONDS = 1200  # a local model writes much more slowly than a hosted one
OLLAMA_KEY = "local"  # stands for the key a local model does not need
LOCAL_PROVIDERS = ("ollama", "lmstudio")


# Local models through LM Studio (https://lmstudio.ai), on this Mac or another machine of the network (3 Nov 2026): its
# OpenAI-compatible server at the address Settings keeps (the archive's own setting, never in the code). A model is chosen
# as "lmstudio:<name>" from the models the server lists.
LMSTUDIO_PREFIX = "lmstudio:"
LMSTUDIO_URL_KEY = "lmstudio_url"
DEFAULT_LMSTUDIO_URL = "http://127.0.0.1:1234"
LMSTUDIO_JSON = {"type": "json_schema", "json_schema": {"name": "answer", "schema": {"type": "object"}}}  # it takes no "json_object"


def lmstudio_url() -> str:
    """The LM Studio server's address: the archive's setting, else LMSTUDIO_URL, else this Mac's default port."""
    saved = None
    if has_app_context():
        setting = AppSetting.query.filter_by(key=LMSTUDIO_URL_KEY).first()
        saved = setting.value if setting else None
    return normal_url(saved or os.environ.get("LMSTUDIO_URL") or DEFAULT_LMSTUDIO_URL)


def normal_url(value: str) -> str:
    """An address as typed ("host:1234", "http://host:1234/v1/") made plain: "http://host:1234"."""
    value = value.strip().rstrip("/")
    value = value if "://" in value else f"http://{value}"
    return value.removesuffix("/v1").rstrip("/")


def set_lmstudio_url(value: str | None) -> None:
    """Keep the LM Studio address (an empty value goes back to the default)."""
    setting = AppSetting.query.filter_by(key=LMSTUDIO_URL_KEY).first()
    value = normal_url(value) if value and value.strip() else None
    if value is None:
        if setting is not None:
            db.session.delete(setting)
    elif setting is None:
        db.session.add(AppSetting(key=LMSTUDIO_URL_KEY, value=value))
    else:
        setting.value = value


def lmstudio_models() -> list[dict[str, Any]]:
    """The models the LM Studio server lists, or none when it does not answer (or in tests)."""
    if has_app_context() and current_app.testing:
        return []
    try:
        answer = requests.get(f"{lmstudio_url()}/v1/models", timeout=3)
        answer.raise_for_status()
        models = answer.json().get("data", [])
    except (requests.RequestException, ValueError, AttributeError):
        return []
    return [{"name": m["id"]} for m in models if m.get("id") and "embed" not in m["id"]]


def ollama_models() -> list[dict[str, Any]]:
    """The models Ollama has installed (name, size in GB, parameters), or none when it does not answer (or in tests)."""
    if has_app_context() and current_app.testing:
        return []
    try:
        answer = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2)
        answer.raise_for_status()
        models = answer.json().get("models", [])
    except (requests.RequestException, ValueError, AttributeError):
        return []
    return [{"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 1),
             "parameters": (m.get("details") or {}).get("parameter_size", "")}
            for m in models if m.get("name") and "embed" not in m["name"]]


def model_options() -> list[DeepSeekModelOption]:
    """The hosted models, then the local ones Ollama has installed, smallest first."""
    local = [
        DeepSeekModelOption(
            value=OLLAMA_PREFIX + m["name"],
            label=f"Ollama · {m['name']}",
            strapline=f"Local on this Mac, {m['parameters']}, {m['size_gb']} GB".replace(", ,", ","),
            description=("Free and private, no key. Slower than the hosted models: a large model can take "
                         "minutes for one text, and a small one writes less well."),
            provider="ollama",
        )
        for m in sorted(ollama_models(), key=lambda m: m["size_gb"])
    ]
    served = [
        DeepSeekModelOption(
            value=LMSTUDIO_PREFIX + m["name"],
            label=f"LM Studio · {m['name']}",
            strapline=f"Local, on the LM Studio server at {lmstudio_url()}",
            description="Free and private, no key. As fast as the machine it runs on.",
            provider="lmstudio",
        )
        for m in lmstudio_models()
    ]
    return [*DEEPSEEK_MODEL_OPTIONS, *local, *served]


def _known_model(model: str) -> bool:
    return model in ALLOWED_DEEPSEEK_MODELS or any(model.startswith(prefix) and len(model) > len(prefix)
                                                   for prefix in (OLLAMA_PREFIX, LMSTUDIO_PREFIX))


def get_deepseek_model() -> str:
    """Return the saved model, with V4 Pro as the safe migration default.

    Pure parser/unit callers sometimes exercise an AI request helper outside a
    Flask application context. They should still produce a valid payload rather
    than requiring a database merely to discover the default.
    """
    if not has_app_context():
        return DEFAULT_DEEPSEEK_MODEL
    setting = AppSetting.query.filter_by(key=DEEPSEEK_MODEL_SETTING_KEY).first()
    value = RENAMED_MODELS.get(setting.value, setting.value) if setting else None
    if value is None or not _known_model(value):
        return DEFAULT_DEEPSEEK_MODEL
    return value


def set_deepseek_model(model: str) -> AppSetting:
    model = RENAMED_MODELS.get(model, model)
    if not _known_model(model):
        raise ValueError(
            "Choose DeepSeek V4 Pro, DeepSeek V4.1 Flash, GPT-6 Luna or a local Ollama or LM Studio model."
        )
    setting = AppSetting.query.filter_by(key=DEEPSEEK_MODEL_SETTING_KEY).first()
    if setting is None:
        setting = AppSetting(key=DEEPSEEK_MODEL_SETTING_KEY, value=model)
        db.session.add(setting)
    else:
        setting.value = model
    return setting


PAPERS_MODEL_SETTING_KEY = "papers_model"


def get_papers_model() -> str:
    """The model the papers are written with (GPT-6 Luna unless Settings says otherwise): a hosted one, or an LM Studio
    model (3 Nov 2026). A news cycle writes dozens of articles at once, so Ollama on this Mac is not offered."""
    if not has_app_context():
        return DEFAULT_OPENAI_MODEL
    setting = AppSetting.query.filter_by(key=PAPERS_MODEL_SETTING_KEY).first()
    value = RENAMED_MODELS.get(setting.value, setting.value) if setting else DEFAULT_OPENAI_MODEL
    return value if _papers_model(value) else DEFAULT_OPENAI_MODEL


def _papers_model(model: str) -> bool:
    return model in ALLOWED_DEEPSEEK_MODELS or (model.startswith(LMSTUDIO_PREFIX) and len(model) > len(LMSTUDIO_PREFIX))


def set_papers_model(model: str) -> None:
    model = RENAMED_MODELS.get(model, model)
    if not _papers_model(model):
        raise ValueError("Choose DeepSeek V4 Pro, DeepSeek V4.1 Flash, GPT-6 Luna or an LM Studio model for the papers.")
    setting = AppSetting.query.filter_by(key=PAPERS_MODEL_SETTING_KEY).first()
    if setting is None:
        db.session.add(AppSetting(key=PAPERS_MODEL_SETTING_KEY, value=model))
    else:
        setting.value = model


CHECKER_MODEL_SETTING_KEY = "checker_model"


def get_checker_model() -> str:
    """The hosted model that lists an article's hard facts for the fact checker (3 Nov 2026): Settings' choice, else the
    papers' model when it is hosted, else DeepSeek V4 Pro. A local model listed too few and let wrong facts through."""
    papers = get_papers_model()
    fallback = papers if papers in ALLOWED_DEEPSEEK_MODELS else DEFAULT_DEEPSEEK_MODEL
    if not has_app_context():
        return fallback
    setting = AppSetting.query.filter_by(key=CHECKER_MODEL_SETTING_KEY).first()
    value = RENAMED_MODELS.get(setting.value, setting.value) if setting else None
    return value if value in ALLOWED_DEEPSEEK_MODELS else fallback


def set_checker_model(model: str | None) -> None:
    """Keep the fact checker's model; an empty choice follows the papers (or DeepSeek V4 Pro when they are local)."""
    setting = AppSetting.query.filter_by(key=CHECKER_MODEL_SETTING_KEY).first()
    model = RENAMED_MODELS.get(model or "", model or "")
    if not model:
        if setting is not None:
            db.session.delete(setting)
        return
    if model not in ALLOWED_DEEPSEEK_MODELS:
        raise ValueError("Choose DeepSeek V4 Pro, DeepSeek V4.1 Flash or GPT-6 Luna for the fact checker.")
    if setting is None:
        db.session.add(AppSetting(key=CHECKER_MODEL_SETTING_KEY, value=model))
    else:
        setting.value = model


def deepseek_model_label(model: str) -> str:
    if model.startswith(OLLAMA_PREFIX):
        return f"Ollama · {model.removeprefix(OLLAMA_PREFIX)}"
    if model.startswith(LMSTUDIO_PREFIX):
        return f"LM Studio · {model.removeprefix(LMSTUDIO_PREFIX)}"
    if model in FORMER_LABELS:
        return FORMER_LABELS[model]
    return next(
        (
            option.label
            for option in DEEPSEEK_MODEL_OPTIONS
            if option.value == model
        ),
        model,
    )


def ai_model_provider(model: str | None = None) -> str:
    """Return the provider for the selected writing model."""
    selected = model or get_deepseek_model()
    if selected.startswith(OLLAMA_PREFIX):
        return "ollama"
    if selected.startswith(LMSTUDIO_PREFIX):
        return "lmstudio"
    return next(
        (
            option.provider
            for option in DEEPSEEK_MODEL_OPTIONS
            if option.value == selected
        ),
        "deepseek",
    )


def ai_provider_label(model: str | None = None) -> str:
    return {"openai": "OpenAI", "ollama": "Ollama", "lmstudio": "LM Studio"}.get(ai_model_provider(model), "DeepSeek")


def ai_model_supports_images(model: str | None = None) -> bool:
    """Whether the selected writing model accepts image content blocks."""
    selected = model or get_deepseek_model()
    return next(
        (
            option.supports_images
            for option in DEEPSEEK_MODEL_OPTIONS
            if option.value == selected
        ),
        False,
    )


def resolve_ai_api_key(
    api_key: str | None = None, *, model: str | None = None
) -> str | None:
    """Resolve the selected provider's key, honoring explicit test/caller keys. A local model needs none."""
    provider = ai_model_provider(model)
    if provider in LOCAL_PROVIDERS:
        return OLLAMA_KEY
    if has_app_context():
        configured = current_app.config.get(
            "OPENAI_API_KEY" if provider == "openai" else "DEEPSEEK_API_KEY"
        )
        if configured:
            return configured
    environment_key = (
        "OPENAI_API_KEY" if provider == "openai" else "DEEPSEEK_API_KEY"
    )
    return os.environ.get(environment_key) or stored_key(environment_key) or api_key


# The AI keys live in a small file of the app, beside its archives but outside them (so no archive or
# backup carries them), readable by this user only.
KEYS_FILE = Path(os.environ.get("FM_APP_DIR") or Path.home() / "Library/Application Support/FM Club Archive").expanduser() / "keys.json"


def stored_key(name: str) -> str | None:
    """An API key from the app's keys file ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"); None in tests or without one."""
    if has_app_context() and current_app.testing:
        return None
    try:
        return json.loads(KEYS_FILE.read_text(encoding="utf-8")).get(name) or None
    except (OSError, ValueError):
        return None


def set_stored_key(name: str, value: str | None) -> None:
    """Keep an API key in the app's keys file, or remove it (an empty value)."""
    try:
        keys = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        keys = {}
    if value:
        keys[name] = value
    else:
        keys.pop(name, None)
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEYS_FILE.touch(mode=0o600, exist_ok=True)
    KEYS_FILE.chmod(0o600)
    KEYS_FILE.write_text(json.dumps(keys, indent=2) + "\n", encoding="utf-8")


def ai_request_config(
    *,
    model: str,
    api_key: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float | None = None,
    response_format: dict | None = None,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Build a compatible chat-completions request for the model's provider."""
    provider = ai_model_provider(model)
    if provider == "ollama":
        # Ollama's own endpoint: unlike its OpenAI-compatible one it takes the context size; thinking is off,
        # as for DeepSeek, so the answer is the text alone. Read the answer with chat_content.
        options: dict[str, Any] = {"num_ctx": ollama_context(messages, max_tokens), "num_predict": max_tokens}
        if temperature is not None:
            options["temperature"] = temperature
        payload = {"model": model.removeprefix(OLLAMA_PREFIX), "messages": messages, "stream": False, "think": False,
                   "options": options}
        if response_format is not None:
            payload["format"] = "json"
        return f"{OLLAMA_URL}/api/chat", {"Content-Type": "application/json"}, payload
    if provider == "lmstudio":
        # thinking off, as for DeepSeek and Ollama: Gemma 4 spent 197 of 200 tokens thinking and wrote nothing (3 Nov 2026)
        payload = {"model": model.removeprefix(LMSTUDIO_PREFIX), "messages": messages, "max_tokens": max_tokens, "stream": False,
                   "reasoning_effort": "none"}
        if temperature is not None:
            payload["temperature"] = temperature
        if response_format is not None:
            payload["response_format"] = LMSTUDIO_JSON
        return f"{lmstudio_url()}/v1/chat/completions", {"Content-Type": "application/json"}, payload
    payload = {"model": model, "messages": messages}
    if provider == "openai":
        payload["reasoning_effort"] = DEFAULT_OPENAI_REASONING_EFFORT
        payload["max_completion_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format
        return (
            OPENAI_CHAT_COMPLETIONS_URL,
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            payload,
        )

    payload["thinking"] = {"type": "disabled"}
    if response_format is not None:
        payload["response_format"] = response_format
    if temperature is not None:
        payload["temperature"] = temperature
    payload["max_tokens"] = max_tokens
    return (
        "https://api.deepseek.com/chat/completions",
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        payload,
    )




def ai_timeout(model: str, hosted_seconds: int) -> int:
    """How long to wait for one answer: a local model is given much longer."""
    return OLLAMA_TIMEOUT_SECONDS if ai_model_provider(model) in LOCAL_PROVIDERS else hosted_seconds


def chat_content(answer: dict) -> tuple[str, str | None]:
    """The text of a chat answer and why it stopped, from a hosted provider (OpenAI's form) or from Ollama."""
    if "choices" in answer:
        choice = answer["choices"][0]
        return _without_thinking(choice["message"]["content"] or ""), choice.get("finish_reason")
    return answer["message"]["content"] or "", answer.get("done_reason")


def _without_thinking(text: str) -> str:
    """A local model's answer without its thinking ("<think>...</think>") or a code fence around its JSON."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.S)
    return fenced.group(1) if fenced else text


def ollama_context(messages: list[dict[str, Any]], max_tokens: int) -> int:
    """The context for one request: the prompt's estimated tokens and the answer's, rounded up, within the limits."""
    chars = sum(len(m["content"]) if isinstance(m.get("content"), str) else len(json.dumps(m.get("content"))) for m in messages)
    wanted = int(chars / OLLAMA_CHARS_PER_TOKEN) + max_tokens + 1024
    steps = -(-wanted // OLLAMA_CONTEXT_STEP)
    return max(OLLAMA_CONTEXT_MIN, min(OLLAMA_CONTEXT_MAX, steps * OLLAMA_CONTEXT_STEP))


def check_ollama_answer(answer: dict, payload: dict) -> None:
    """Log a warning when a local model's prompt filled nearly all its context: Ollama may have cut its start."""
    used, context = answer.get("prompt_eval_count"), (payload.get("options") or {}).get("num_ctx")
    if used and context and used >= context - (payload["options"].get("num_predict") or 0) - 256 and has_app_context():
        current_app.logger.warning("local model prompt of %s tokens in a %s context: its start may have been cut", used, context)
