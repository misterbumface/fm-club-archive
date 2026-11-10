"""Pictures from an InvokeAI server (29 Oct 2026: InvokeAI 6.14 on a PC with an RTX 5070 Ti, on the home network).

InvokeAI draws from a graph of steps, not from one request: load the model, encode the prompt, denoise, decode. The
graphs here follow the ones its own web app builds (read from its queue): Z-Image Turbo and FLUX.2 Klein draw from a
prompt; FLUX.1 Kontext and Qwen Image Edit draw from a prompt and a reference picture (the player's portrait), keeping
the face. A graph goes in the queue (POST /api/v1/queue/default/enqueue_batch), the app asks after it until it is done,
then fetches the picture (GET /api/v1/images/i/<name>/full). The helper models (text encoders, VAEs) are the server's
own of the right kind, found in its model list.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_URL = "http://localhost:9090"
POLL_EVERY, POLL_FOR = 1.5, 900.0  # seconds; the first picture after a model change loads the model (a minute or more)
# The families with a graph here, by the model's "base": what each can do.
CREATE_BASES = ("z-image", "flux2", "flux3")
EDIT_BASES = ("flux", "qwen-image", "flux3")  # FLUX.1 Kontext (a Kontext model only), Qwen Image Edit, FLUX.3
FLUX3_STEPS = 16  # the server's own default for FLUX.3 Klein (4-16 work)
KONTEXT_STEPS, KONTEXT_GUIDANCE = 20, 2.5
QWEN_LIGHTNING = "Lightning (8-step"  # the Qwen Image Edit speed LoRA, when the server has it: 8 steps at CFG 1


class InvokeError(RuntimeError):
    """The server gave no picture."""


def _call(url: str, path: str, body: bytes | None = None, content_type: str = "application/json", timeout: int = 60):
    request = urllib.request.Request(url.rstrip("/") + path, data=body, headers={"Content-Type": content_type} if body else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
            return json.loads(data) if response.headers.get_content_type() == "application/json" else data
    except urllib.error.HTTPError as exc:
        why = exc.read()[:400].decode(errors="replace")
        raise InvokeError(f"InvokeAI refused the request ({exc.code}): {why}") from exc
    except OSError as exc:
        raise InvokeError(f"InvokeAI did not answer at {url}: {exc}") from exc


def online(url: str) -> bool:
    try:
        _call(url, "/api/v1/app/version", timeout=4)
        return True
    except InvokeError:
        return False


def models(url: str) -> list[dict]:
    """The server's models: key, name, base, type, variant and default settings."""
    return _call(url, "/api/v2/models/", timeout=15).get("models", [])


def main_models(url: str) -> dict[str, list[str]]:
    """The names of the models this app can draw with: {"create": [...], "edit": [...]}."""
    found = {"create": [], "edit": []}
    for m in models(url):
        if m["type"] != "main":
            continue
        if m["base"] in CREATE_BASES:
            found["create"].append(m["name"])
        if (m["base"] == "flux" and "kontext" in m["name"].lower()) or (m["base"] == "qwen-image" and m.get("variant") == "edit") \
                or m["base"] == "flux3":
            found["edit"].append(m["name"])
    return {k: sorted(v, key=str.casefold) for k, v in found.items()}


def _ident(m: dict) -> dict:
    return {k: m[k] for k in ("key", "hash", "name", "base", "type") if k in m}


def _find(all_models: list[dict], name: str | None = None, *, type_: str = "main", base: str | None = None, has: str | None = None) -> dict | None:
    for m in all_models:
        if m["type"] != type_ or (base and m["base"] != base) or (name and m["name"] != name) or (has and has not in m["name"]):
            continue
        return m
    return None


def _model(all_models: list[dict], name: str) -> dict:
    model = _find(all_models, name)
    if model is None:
        raise InvokeError(f"InvokeAI has no model called {name!r}.")
    return model


def _helper(all_models: list[dict], type_: str, base: str | None = None) -> dict:
    found = _find(all_models, type_=type_, base=base)
    if found is None:
        raise InvokeError(f"InvokeAI has no {type_} model{f' for {base}' if base else ''}.")
    return _ident(found)


def _qwen3_size(model: dict) -> str:
    """The Qwen3 text encoder a model reads its prompt with: Z-Image and FLUX.2 Klein 4B the 4B (2,560 wide), Klein 9B the 8B
    (4,096 wide). The wrong one fails in the denoise ("tensor a (4096) must match tensor b (2560)")."""
    tell = f"{model.get('variant') or ''} {model['name']}".lower().replace(" ", "").replace("-", "")
    return "8b" if model["base"] == "flux2" and "9b" in tell else "4b"


def _qwen3_encoder(all_models: list[dict], model: dict) -> dict:
    """The server's Qwen3 encoder of the size the model needs (its variant, else its name, says the size). With no encoder
    that says its size, one that says no other size; with none of those, the first."""
    size = _qwen3_size(model)
    found = [m for m in all_models if m["type"] == "qwen3_encoder"]
    if not found:
        raise InvokeError("InvokeAI has no qwen3_encoder model.")

    def says(m: dict) -> set[str]:
        tell = f"{m.get('variant') or ''} {m['name']}".lower().replace(" ", "").replace("-", "").replace("_", "")
        return {s for s in ("4b", "8b") if s in tell}

    best = next((m for m in found if size in says(m)), None) or next((m for m in found if not says(m)), None)
    if best is None:
        raise InvokeError(f"InvokeAI has no Qwen3 {size.upper()} text encoder for {model['name']} (it has: "
                          f"{', '.join(m['name'] for m in found)}).")
    return _ident(best)


def _flux3(g: "_Graph", all_models: list[dict], model: dict, prompt: str, seed: int, width: int, height: int,
           image_name: str | None = None) -> str:
    """FLUX.3 (Klein): one graph draws and edits; a reference picture goes in as Kontext conditioning. It shares FLUX.2's
    VAE and reads the prompt with a Qwen3-VL encoder. Returns the decoding step."""
    loader = g.node("flux3_model_loader", model=_ident(model), vae_model=_helper(all_models, "vae", "flux2"),
                    qwen3_vl_encoder_model=_helper(all_models, "qwen3_vl_encoder"))
    text = g.node("flux3_text_encoder", prompt=prompt)
    denoise = g.node("flux3_denoise", width=width, height=height, steps=_steps(model, FLUX3_STEPS), seed=seed)
    decode = g.node("flux2_vae_decode")
    g.link(loader, "qwen3_vl_encoder", text, "qwen3_vl_encoder")
    g.link(loader, "transformer", denoise, "transformer")
    g.link(loader, "vae", denoise, "vae")
    g.link(text, "conditioning", denoise, "positive_conditioning")
    if image_name:
        kontext = g.node("flux_kontext", image={"image_name": image_name})
        kontexts = g.node("collect")
        g.link(kontext, "kontext_cond", kontexts, "item")
        g.link(kontexts, "collection", denoise, "kontext_conditioning")
    g.link(denoise, "latents", decode, "latents")
    g.link(loader, "vae", decode, "vae")
    return decode


def _steps(model: dict, fallback: int) -> int:
    return ((model.get("default_settings") or {}).get("steps")) or fallback


class _Graph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.edges: list[dict] = []

    def node(self, type_: str, **fields) -> str:
        node_id = f"{type_}:{uuid.uuid4().hex[:10]}"
        self.nodes[node_id] = {"id": node_id, "type": type_, "is_intermediate": True, "use_cache": False, **fields}
        return node_id

    def link(self, source: str, field: str, destination: str, into: str) -> None:
        self.edges.append({"source": {"node_id": source, "field": field}, "destination": {"node_id": destination, "field": into}})

    def body(self) -> dict:
        return {"id": f"fm-archive-{uuid.uuid4().hex[:8]}", "nodes": self.nodes, "edges": self.edges}


def create_graph(all_models: list[dict], model_name: str, prompt: str, seed: int, width: int, height: int) -> tuple[dict, str]:
    """A text-to-image graph for the model's family, and the id of its last (decoding) step."""
    model = _model(all_models, model_name)
    g, seed = _Graph(), seed % 2_147_483_647
    if model["base"] == "z-image":
        loader = g.node("z_image_model_loader", model=_ident(model), vae_model=_helper(all_models, "vae", "flux"),
                        qwen3_encoder_model=_qwen3_encoder(all_models, model))
        text = g.node("z_image_text_encoder", prompt=prompt)
        denoise = g.node("z_image_denoise", width=width, height=height, steps=_steps(model, 9), guidance_scale=1.0, seed=seed)
        decode = g.node("z_image_l2i")
        g.link(loader, "qwen3_encoder", text, "qwen3_encoder")
        g.link(loader, "transformer", denoise, "transformer")
        g.link(text, "conditioning", denoise, "positive_conditioning")
        g.link(denoise, "latents", decode, "latents")
        g.link(loader, "vae", decode, "vae")
    elif model["base"] == "flux2":
        loader = g.node("flux2_klein_model_loader", model=_ident(model), vae_model=_helper(all_models, "vae", "flux2"),
                        qwen3_encoder_model=_qwen3_encoder(all_models, model))
        text = g.node("flux2_klein_text_encoder", prompt=prompt)
        texts = g.node("collect")
        denoise = g.node("flux2_denoise", width=width, height=height, num_steps=_steps(model, 4), seed=seed)
        decode = g.node("flux2_vae_decode")
        g.link(loader, "qwen3_encoder", text, "qwen3_encoder")
        g.link(loader, "max_seq_len", text, "max_seq_len")
        g.link(loader, "transformer", denoise, "transformer")
        g.link(loader, "vae", denoise, "vae")
        g.link(text, "conditioning", texts, "item")
        g.link(texts, "collection", denoise, "positive_text_conditioning")
        g.link(denoise, "latents", decode, "latents")
        g.link(loader, "vae", decode, "vae")
    elif model["base"] == "flux3":
        decode = _flux3(g, all_models, model, prompt, seed, width, height)
    else:
        raise InvokeError(f"{model_name} is a {model['base']} model: this app draws with Z-Image, FLUX.2 Klein or FLUX.3 on InvokeAI.")
    g.nodes[decode]["is_intermediate"] = False
    return g.body(), decode


def edit_graph(all_models: list[dict], model_name: str, prompt: str, image_name: str, seed: int, width: int = 1024,
               height: int = 1024) -> tuple[dict, str]:
    """A graph that draws a new picture from a reference picture already on the server (FLUX.1 Kontext, Qwen Image Edit or FLUX.3)."""
    model = _model(all_models, model_name)
    g, seed = _Graph(), seed % 2_147_483_647
    if model["base"] == "flux" and "kontext" in model["name"].lower():
        loader = g.node("flux_model_loader", model=_ident(model), t5_encoder_model=_helper(all_models, "t5_encoder"),
                        clip_embed_model=_helper(all_models, "clip_embed"), vae_model=_helper(all_models, "vae", "flux"))
        text = g.node("flux_text_encoder", prompt=prompt)
        texts = g.node("collect")
        prep = g.node("flux_kontext_image_prep", images=[{"image_name": image_name}], use_preferred_resolution=True)
        kontext = g.node("flux_kontext")
        kontexts = g.node("collect")
        denoise = g.node("flux_denoise", width=width, height=height, num_steps=KONTEXT_STEPS, guidance=KONTEXT_GUIDANCE, seed=seed)
        decode = g.node("flux_vae_decode")
        for field in ("clip", "t5_encoder"):
            g.link(loader, field, text, field)
        g.link(loader, "max_seq_len", text, "t5_max_seq_len")
        g.link(text, "conditioning", texts, "item")
        g.link(texts, "collection", denoise, "positive_text_conditioning")
        g.link(prep, "image", kontext, "image")
        g.link(kontext, "kontext_cond", kontexts, "item")
        g.link(kontexts, "collection", denoise, "kontext_conditioning")
        g.link(loader, "transformer", denoise, "transformer")
        g.link(loader, "vae", denoise, "controlnet_vae")
        g.link(denoise, "latents", decode, "latents")
        g.link(loader, "vae", decode, "vae")
    elif model["base"] == "qwen-image":
        loader = g.node("qwen_image_model_loader", model=_ident(model), vae_model=_helper(all_models, "vae", "qwen-image"),
                        qwen_vl_encoder_model=_helper(all_models, "qwen_vl_encoder"))
        lightning = _find(all_models, type_="lora", base="qwen-image", has=QWEN_LIGHTNING)
        transformer = (loader, "transformer")
        if lightning:
            lora = g.node("qwen_image_lora_loader", lora=_ident(lightning), weight=1.0)
            g.link(loader, "transformer", lora, "transformer")
            transformer = (lora, "transformer")
        text = g.node("qwen_image_text_encoder", prompt=prompt, reference_images=[{"image_name": image_name}])
        reference = g.node("qwen_image_i2l", image={"image_name": image_name}, width=width, height=height)
        steps, cfg = (8, 1.0) if lightning else (_steps(model, 40), 4.0)
        denoise = g.node("qwen_image_denoise", width=width, height=height, steps=steps, cfg_scale=cfg, seed=seed)
        decode = g.node("qwen_image_l2i")
        g.link(loader, "qwen_vl_encoder", text, "qwen_vl_encoder")
        g.link(loader, "vae", reference, "vae")
        g.link(reference, "latents", denoise, "reference_latents")
        g.link(*transformer, denoise, "transformer")
        g.link(text, "conditioning", denoise, "positive_conditioning")
        g.link(denoise, "latents", decode, "latents")
        g.link(loader, "vae", decode, "vae")
    elif model["base"] == "flux3":
        decode = _flux3(g, all_models, model, prompt, seed, width, height, image_name)
    else:
        raise InvokeError(f"{model_name} cannot edit: choose FLUX.1 Kontext, Qwen Image Edit or FLUX.3 on InvokeAI.")
    g.nodes[decode]["is_intermediate"] = False
    return g.body(), decode


def upload(url: str, picture: bytes, name: str = "reference.png") -> str:
    """Put a picture on the server (an intermediate, kept out of its gallery); its name there."""
    boundary = "----fm-archive-" + uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n").encode() \
        + picture + f"\r\n--{boundary}--\r\n".encode()
    query = urllib.parse.urlencode({"image_category": "user", "is_intermediate": "true"})
    answer = _call(url, f"/api/v1/images/upload?{query}", body, f"multipart/form-data; boundary={boundary}")
    return answer["image_name"]


def run(url: str, graph: dict, output: str, timeout: float = POLL_FOR) -> bytes:
    """Queue the graph, wait for it, and return the picture its output step made."""
    answer = _call(url, "/api/v1/queue/default/enqueue_batch", json.dumps({"batch": {"graph": graph, "runs": 1}, "prepend": False}).encode())
    item_ids = answer.get("item_ids") or []
    if not item_ids:
        raise InvokeError(f"InvokeAI queued nothing: {answer}")
    deadline = time.monotonic() + timeout
    while True:
        item = _call(url, f"/api/v1/queue/default/i/{item_ids[0]}")
        status = item.get("status")
        if status == "completed":
            break
        if status in ("failed", "canceled"):
            raise InvokeError(f"InvokeAI {status} the picture: {(item.get('error_message') or item.get('error_type') or '')[:300]}")
        if time.monotonic() > deadline:
            raise InvokeError("InvokeAI took too long to draw the picture.")
        time.sleep(POLL_EVERY)
    session = item.get("session") or {}
    for prepared in (session.get("source_prepared_mapping") or {}).get(output, []):
        image = ((session.get("results") or {}).get(prepared) or {}).get("image")
        if image and image.get("image_name"):
            return _call(url, f"/api/v1/images/i/{urllib.parse.quote(image['image_name'])}/full", timeout=120)
    raise InvokeError("InvokeAI finished but gave no picture.")


def draw(url: str, model_name: str, prompt: str, seed: int, width: int, height: int) -> bytes:
    graph, output = create_graph(models(url), model_name, prompt, seed, width, height)
    return run(url, graph, output)


def edit(url: str, model_name: str, prompt: str, reference: bytes, seed: int, width: int = 1024, height: int = 1024) -> bytes:
    image_name = upload(url, reference)
    graph, output = edit_graph(models(url), model_name, prompt, image_name, seed, width, height)
    return run(url, graph, output)
