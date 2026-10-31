"""Who draws the pictures (illustrations, pools, test pictures, action photos): Draw Things on this Mac, WaveSpeed's API,
or OpenAI's Images API.

The maker and WaveSpeed's model path are settings (the Settings page); the WaveSpeed key lives in the app's keys file
with the writers' keys. WaveSpeed's text-to-image call: POST https://api.wavespeed.ai/api/v3/<model path> with the
prompt, the size as "width*height" and a seed, then GET .../predictions/<id>/result until the status is "completed",
whose outputs are picture addresses (wavespeed.ai model pages, 24 Oct 2026).
"""

from __future__ import annotations

import base64
import json
import secrets
import time
import urllib.error
import urllib.request

from app.extensions import db
from app.models.app_setting import AppSetting
from app.services import world_portraits
from app.services.ai_settings import stored_key

MAKER_KEY, WAVESPEED_MODEL_KEY, WAVESPEED_EDIT_KEY = "picture_maker", "wavespeed_model", "wavespeed_edit_model"
OPENAI_MODEL_KEY, OPENAI_QUALITY_KEY = "openai_image_model", "openai_image_quality"
# Draw Things' models, chosen on the Settings page: each request names its model. Its API (27 Oct 2026) lists no installed
# models, only the one selected in the app (/sdapi/v1/options), so the page offers the defaults and every model it has
# seen selected there. An empty illustration model means "the one selected in Draw Things".
DT_ILLUSTRATION_KEY, DT_PORTRAIT_KEY, DT_EDIT_KEY, DT_SEEN_KEY = ("draw_things_illustration_model", "draw_things_portrait_model",
                                                                  "draw_things_edit_model", "draw_things_seen_models")
MAKERS = {"draw_things": "Draw Things (on this Mac)", "invoke": "InvokeAI (server)", "wavespeed": "WaveSpeed (API)", "openai": "OpenAI (API)"}
# InvokeAI on another computer (invoke_ai): its address and the two models, one that draws from a prompt and one that edits a
# portrait. With InvokeAI chosen the portraits are drawn there too.
INVOKE_URL_KEY, INVOKE_CREATE_KEY, INVOKE_EDIT_KEY = "invoke_url", "invoke_create_model", "invoke_edit_model"
DEFAULT_INVOKE_CREATE, DEFAULT_INVOKE_EDIT = "Z-Image Turbo (Q8)", "FLUX.1 Kontext dev (quantized)"
# OpenAI's Images API: the same key as the writers' (OPENAI_API_KEY). A GPT image model answers with the picture as base64;
# the standard sizes are 1536x1024, 1024x1536 and 1024x1024, the qualities low, medium, high (2.5 adds xhigh and max); an
# edit takes the portrait as a multipart file and, for gpt-image-2 and later, no input_fidelity (openai-python types,
# 24 Oct 2026). Low quality on gpt-image-2.5-sunburst costs about half a cent a picture.
OPENAI_URL = "https://api.openai.com/v1/images"
OPENAI_MODELS = ("gpt-image-2.5-sunburst", "gpt-image-2.5-flare", "gpt-image-2", "gpt-image-1.5", "gpt-image-1-mini")
OPENAI_QUALITIES = ("low", "medium", "high", "xhigh", "max", "auto")
DEFAULT_OPENAI_MODEL, DEFAULT_OPENAI_QUALITY = OPENAI_MODELS[0], "low"
OPENAI_SIZES = {"landscape": "1536x1024", "portrait": "1024x1536", "square": "1024x1024"}
WAVESPEED_KEY = "WAVESPEED_API_KEY"
WAVESPEED_URL = "https://api.wavespeed.ai/api/v3"
WAVESPEED_MODELS = ("wavespeed-ai/z-image/turbo", "openai/gpt-image-2/text-to-image", "wavespeed-ai/flux-2-flex/text-to-image",
                    "wavespeed-ai/flux-2-max/text-to-image", "wavespeed-ai/qwen-image/text-to-image")
# Models asked for a shape, not a size: OpenAI's GPT Image takes an aspect ratio, a resolution (1k, 2k, 4k) and a quality,
# and no seed (its page, 24 Oct 2026).
ASPECT_MODELS = ("openai/gpt-image-2",)
ASPECT_RATIOS = ("1:1", "1:2", "2:1", "1:3", "3:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "9:21", "21:9")
DEFAULT_WAVESPEED_MODEL = WAVESPEED_MODELS[0]
# The players' action pictures are edits of their portraits (the face kept): the edit model takes the portrait as an
# uploaded picture. GPT Image's edit route is verified (its page, 24 Oct 2026); any other edit path can be typed in.
WAVESPEED_EDIT_MODELS = ("openai/gpt-image-2/edit",)
DEFAULT_WAVESPEED_EDIT_MODEL = WAVESPEED_EDIT_MODELS[0]
UPLOAD_URL = f"{WAVESPEED_URL}/media/uploads"  # a ticket (PUT address and the download address), then the bytes
POLL_EVERY, POLL_FOR = 2.0, 240.0  # seconds
SIZE_LIMIT = 1536  # WaveSpeed's longest side for Z-Image; a bigger picture is asked for at this size, same shape


class PictureError(world_portraits.PortraitError):
    """The maker gave no picture (a PortraitError too: the pages that drew with Draw Things catch it as before)."""


def _setting(key: str, default: str) -> str:
    row = AppSetting.query.filter_by(key=key).first()
    return (row.value if row and row.value else default)


def _set(key: str, value: str) -> None:
    row = AppSetting.query.filter_by(key=key).first()
    if row is None:
        db.session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def maker() -> str:
    return _setting(MAKER_KEY, "draw_things")


def wavespeed_model() -> str:
    return _setting(WAVESPEED_MODEL_KEY, DEFAULT_WAVESPEED_MODEL)


def wavespeed_edit_model() -> str:
    return _setting(WAVESPEED_EDIT_KEY, DEFAULT_WAVESPEED_EDIT_MODEL)


def openai_model() -> str:
    return _setting(OPENAI_MODEL_KEY, DEFAULT_OPENAI_MODEL)


def openai_quality() -> str:
    return _setting(OPENAI_QUALITY_KEY, DEFAULT_OPENAI_QUALITY)


def _app_setting(key: str, default):
    try:
        return _setting(key, default)
    except Exception:  # noqa: BLE001 - no app context (a command-line run): the default
        return default


def draw_things_illustration_model() -> str | None:
    return _app_setting(DT_ILLUSTRATION_KEY, "") or world_portraits.ILLUSTRATION_DEFAULT


def draw_things_portrait_model() -> str:
    return _app_setting(DT_PORTRAIT_KEY, "") or world_portraits.PORTRAIT_MODEL


def draw_things_edit_model() -> str:
    return _app_setting(DT_EDIT_KEY, "") or world_portraits.EDIT_MODEL


def draw_things_selected() -> str | None:
    """The model selected in the Draw Things app now, or None when it does not answer."""
    try:
        with urllib.request.urlopen(f"{world_portraits.DRAW_THINGS_URL}/sdapi/v1/options", timeout=3) as response:
            return json.load(response).get("model") or None
    except (OSError, ValueError, AttributeError):
        return None


def draw_things_known(selected: str | None = None) -> list[str]:
    """The model names to offer: the defaults, the chosen ones, and every model seen selected in Draw Things."""
    try:
        seen = json.loads(_setting(DT_SEEN_KEY, "[]"))
    except ValueError:
        seen = []
    if selected and selected not in seen:
        seen.append(selected)
        _set(DT_SEEN_KEY, json.dumps(seen))
        db.session.commit()
    names = [world_portraits.PORTRAIT_MODEL, world_portraits.EDIT_MODEL, draw_things_portrait_model(), draw_things_edit_model(),
             draw_things_illustration_model() or "", *seen]
    return sorted({name for name in names if name}, key=str.casefold)


def set_draw_things_models(illustration: str | None, portrait: str | None, edit_model: str | None) -> None:
    for key, given in ((DT_ILLUSTRATION_KEY, illustration), (DT_PORTRAIT_KEY, portrait), (DT_EDIT_KEY, edit_model)):
        if given is None:
            continue
        name = given.strip()
        if name and ("/" in name or " " in name):
            raise ValueError("A Draw Things model is a file name like z_image_turbo_1.0_i8x.ckpt.")
        _set(key, name)


def invoke_url() -> str:
    from app.services import invoke_ai

    return _app_setting(INVOKE_URL_KEY, "") or invoke_ai.DEFAULT_URL


def invoke_create_model() -> str:
    return _app_setting(INVOKE_CREATE_KEY, "") or DEFAULT_INVOKE_CREATE


def invoke_edit_model() -> str:
    return _app_setting(INVOKE_EDIT_KEY, "") or DEFAULT_INVOKE_EDIT


def set_invoke(url: str | None, create_model: str | None, edit_model: str | None) -> None:
    if url is not None:
        url = url.strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("The InvokeAI address starts with http://, like http://localhost:9090.")
        _set(INVOKE_URL_KEY, url.removesuffix("/app"))
    for key, given in ((INVOKE_CREATE_KEY, create_model), (INVOKE_EDIT_KEY, edit_model)):
        if given is not None:
            _set(key, given.strip())


def invoke_models() -> dict[str, list[str]]:
    """The server's models this app can use, or empty lists when it does not answer."""
    from app.services import invoke_ai

    try:
        return invoke_ai.main_models(invoke_url())
    except invoke_ai.InvokeError:
        return {"create": [], "edit": []}


def draw_portrait(prompt: str, seed: int) -> bytes:
    """A portrait: on InvokeAI when it is the maker, else with Draw Things' portrait model (WaveSpeed and OpenAI draw no
    portraits)."""
    if maker() == "invoke":
        return invoke_draw(prompt, seed, 1024, 1024)
    return world_portraits.draw(prompt, seed=seed, model=draw_things_portrait_model())


def invoke_draw(prompt: str, seed: int, width: int, height: int) -> bytes:
    from app.services import invoke_ai

    try:
        return invoke_ai.draw(invoke_url(), invoke_create_model(), prompt, seed, width, height)
    except invoke_ai.InvokeError as exc:
        raise PictureError(str(exc)) from exc


def invoke_edit(prompt: str, reference: bytes, seed: int, width: int = 1024, height: int = 1024) -> bytes:
    from app.services import invoke_ai

    try:
        return invoke_ai.edit(invoke_url(), invoke_edit_model(), prompt, reference, seed, width, height)
    except invoke_ai.InvokeError as exc:
        raise PictureError(str(exc)) from exc


def set_maker(name: str, model_path: str | None = None, edit_model_path: str | None = None, openai_model_name: str | None = None,
              openai_quality_name: str | None = None, commit: bool = True) -> None:
    if name not in MAKERS:
        raise ValueError("Choose Draw Things, InvokeAI, WaveSpeed or OpenAI.")
    _set(MAKER_KEY, name)
    for key, given, default in ((WAVESPEED_MODEL_KEY, model_path, DEFAULT_WAVESPEED_MODEL), (WAVESPEED_EDIT_KEY, edit_model_path, DEFAULT_WAVESPEED_EDIT_MODEL)):
        if given is not None:
            path = given.strip().strip("/")
            if path and "/" not in path:
                raise ValueError("A WaveSpeed model is a path like wavespeed-ai/z-image/turbo.")
            _set(key, path or default)
    if openai_model_name is not None:
        _set(OPENAI_MODEL_KEY, openai_model_name.strip() or DEFAULT_OPENAI_MODEL)
    if openai_quality_name is not None:
        if openai_quality_name not in OPENAI_QUALITIES:
            raise ValueError("An OpenAI quality is low, medium, high, xhigh, max or auto.")
        _set(OPENAI_QUALITY_KEY, openai_quality_name)
    if commit:
        db.session.commit()


def wavespeed_key() -> str | None:
    return stored_key(WAVESPEED_KEY)


def openai_key() -> str | None:
    from flask import current_app

    return current_app.config.get("OPENAI_API_KEY") or stored_key("OPENAI_API_KEY")


def online() -> tuple[bool, str]:
    """Whether the chosen maker can draw now, and why not when it cannot."""
    if maker() == "wavespeed":
        return (True, "") if wavespeed_key() else (False, "WaveSpeed has no API key: add it on the Settings page")
    if maker() == "invoke":
        from app.services import invoke_ai

        return (True, "") if invoke_ai.online(invoke_url()) else (False, f"InvokeAI does not answer at {invoke_url()}")
    if maker() == "openai":
        return (True, "") if openai_key() else (False, "OpenAI has no API key: add it on the Settings page")
    from app.services.article_pictures import drawing_online

    return (True, "") if drawing_online() else (False, "Draw Things is not running")


def draw(prompt: str, seed: int, width: int, height: int, model: str | None = None) -> bytes:
    """A picture from the chosen maker, as PNG or JPEG bytes."""
    if maker() == "invoke":
        return invoke_draw(prompt, seed, width, height)
    if maker() == "wavespeed":
        return wavespeed_draw(prompt, seed, width, height)
    if maker() == "openai":
        return openai_draw(prompt, width, height)
    try:
        return world_portraits.draw(prompt, seed=seed, width=width, height=height, model=model)
    except world_portraits.PortraitError as exc:
        raise PictureError(str(exc)) from exc


# -- OpenAI ---------------------------------------------------------------------------------------------------------------
def openai_size(width: int, height: int) -> str:
    """The standard size of the picture's shape: landscape, portrait or square."""
    return OPENAI_SIZES["landscape" if width > height else "portrait" if height > width else "square"]


def _openai(path: str, key: str, body: bytes, content_type: str, timeout: int = 300) -> dict:
    request = urllib.request.Request(f"{OPENAI_URL}/{path}", data=body,
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            why = json.load(exc).get("error", {}).get("message") or exc.reason
        except (ValueError, AttributeError):
            why = exc.reason
        raise PictureError(f"OpenAI refused the picture: {why}") from exc
    except OSError as exc:
        raise PictureError(f"OpenAI did not answer: {exc}") from exc
    except ValueError as exc:
        raise PictureError("OpenAI answered with something that is not JSON.") from exc


def _openai_picture(answer: dict) -> bytes:
    data = answer.get("data") or []
    if not data or not data[0].get("b64_json"):
        raise PictureError(f"OpenAI returned no picture: {answer.get('error') or answer}")
    return base64.b64decode(data[0]["b64_json"])


def openai_draw(prompt: str, width: int, height: int) -> bytes:
    key = openai_key()
    if not key:
        raise PictureError("OpenAI has no API key: add it on the Settings page.")
    body = {"model": openai_model(), "prompt": prompt, "size": openai_size(width, height), "quality": openai_quality(), "n": 1,
            "output_format": "jpeg"}
    return _openai_picture(_openai("generations", key, json.dumps(body).encode(), "application/json"))


def openai_edit(prompt: str, reference: bytes, width: int = 1024, height: int = 1024) -> bytes:
    """A new picture from the portrait with the GPT image model (a multipart edit, the portrait as a PNG file)."""
    key = openai_key()
    if not key:
        raise PictureError("OpenAI has no API key: add it on the Settings page.")
    boundary = "----fm-club-archive-" + secrets.token_hex(8)
    fields = {"model": openai_model(), "prompt": prompt, "size": openai_size(width, height), "quality": openai_quality(), "n": "1", "output_format": "jpeg"}
    parts = [f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode() for name, value in fields.items()]
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"image[]\"; filename=\"portrait.png\"\r\n"
                 f"Content-Type: image/png\r\n\r\n".encode() + reference + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    return _openai_picture(_openai("edits", key, body, f"multipart/form-data; boundary={boundary}"))


def edit(prompt: str, reference: bytes, seed: int, width: int = 1024, height: int = 1024) -> bytes:
    """A new picture from a portrait and an instruction, the face kept: Draw Things' editing model, or WaveSpeed's edit
    model with the portrait uploaded to it."""
    if maker() == "invoke":
        return invoke_edit(prompt, reference, seed, width, height)
    if maker() == "wavespeed":
        return wavespeed_edit(prompt, reference, seed, width=width, height=height)
    if maker() == "openai":
        return openai_edit(prompt, reference, width, height)
    try:
        return world_portraits.draw_edit(prompt, reference, seed, model=draw_things_edit_model(), width=width, height=height)
    except world_portraits.PortraitError as exc:
        raise PictureError(str(exc)) from exc


def _call(url: str, key: str, body: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except OSError as exc:
        raise PictureError(f"WaveSpeed did not answer: {exc}") from exc
    except ValueError as exc:
        raise PictureError("WaveSpeed answered with something that is not JSON.") from exc


def _fetch(url: str, timeout: int = 120) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except OSError as exc:
        raise PictureError(f"The picture could not be fetched from WaveSpeed: {exc}") from exc


def aspect_ratio(width: int, height: int) -> str:
    """The nearest of the ratios a shape-taking model offers (1344 x 768 is 16:9)."""
    return min(ASPECT_RATIOS, key=lambda r: abs(int(r.split(":")[0]) / int(r.split(":")[1]) - width / height))


def _put(url: str, headers: dict, data: bytes, timeout: int = 120) -> None:
    request = urllib.request.Request(url, data=data, headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return
    except OSError as exc:
        raise PictureError(f"The picture could not be uploaded to WaveSpeed: {exc}") from exc


def wavespeed_upload(data: bytes, filename: str = "portrait.png", content_type: str = "image/png") -> str:
    """A picture's address on WaveSpeed (the way its SDK uploads: a ticket, then a PUT), for the edit models."""
    key = wavespeed_key()
    if not key:
        raise PictureError("WaveSpeed has no API key: add it on the Settings page.")
    ticket = _call(UPLOAD_URL, key, {"filename": filename, "size": len(data), "content_type": content_type}).get("data") or {}
    how = ticket.get("upload") or {}
    if (how.get("method") or "").upper() != "PUT" or not how.get("url") or not ticket.get("download_url"):
        raise PictureError("WaveSpeed gave no upload address.")
    _put(how["url"], how.get("headers") or {}, data)
    return ticket["download_url"]


def wavespeed_edit(prompt: str, reference: bytes, seed: int, sleep=time.sleep, clock=time.monotonic, width: int = 1024, height: int = 1024) -> bytes:
    model = wavespeed_edit_model()
    address = wavespeed_upload(reference)
    body = {"prompt": prompt, "output_format": "jpeg", "enable_sync_mode": False, "enable_base64_output": False}
    if model.startswith(ASPECT_MODELS):
        body |= {"images": [address], "aspect_ratio": "3:2" if width > height else "2:3" if height > width else "1:1", "resolution": "1k",
                 "quality": "medium"}
    else:
        body |= {"image": address, "seed": seed % 2_147_483_647}
    return _finish(model, body, sleep, clock)


def wavespeed_draw(prompt: str, seed: int, width: int, height: int, sleep=time.sleep, clock=time.monotonic) -> bytes:
    key = wavespeed_key()
    if not key:
        raise PictureError("WaveSpeed has no API key: add it on the Settings page.")
    model = wavespeed_model()
    body = {"prompt": prompt, "output_format": "jpeg", "enable_sync_mode": False, "enable_base64_output": False}
    if model.startswith(ASPECT_MODELS):
        body |= {"aspect_ratio": aspect_ratio(width, height), "resolution": "1k", "quality": "medium"}
    else:
        if max(width, height) > SIZE_LIMIT:
            scale = SIZE_LIMIT / max(width, height)
            width, height = round(width * scale / 16) * 16, round(height * scale / 16) * 16
        body |= {"size": f"{width}*{height}", "seed": seed % 2_147_483_647}
    return _finish(model, body, sleep, clock)


def _finish(model: str, body: dict, sleep, clock) -> bytes:
    """Submit to a model and wait for its picture."""
    key = wavespeed_key()
    submitted = _call(f"{WAVESPEED_URL}/{model}", key, body)
    data = submitted.get("data") or {}
    prediction = data.get("id")
    if not prediction:
        raise PictureError(f"WaveSpeed refused the request: {submitted.get('message') or submitted}")
    started = clock()
    while True:
        status, outputs = data.get("status"), data.get("outputs") or []
        if status == "completed" and outputs:
            first = outputs[0]
            return _fetch(first if isinstance(first, str) else first.get("url", ""))
        if status in ("failed", "cancelled", "timeout", "deleted"):
            raise PictureError(f"WaveSpeed {status}: {data.get('error') or data.get('message') or 'no reason given'}")
        if clock() - started > POLL_FOR:
            raise PictureError("WaveSpeed took too long.")
        sleep(POLL_EVERY)
        data = _call(f"{WAVESPEED_URL}/predictions/{prediction}/result", key).get("data") or {}
