"""InvokeAI pictures (invoke_ai): the graphs for each model family, and the maker's settings. No server is called."""

import pytest

from app.services import invoke_ai, picture_makers

MODELS = [
    {"key": "z", "hash": "h", "name": "Z-Image Turbo (Q8)", "base": "z-image", "type": "main", "default_settings": {"steps": 9}},
    {"key": "k", "hash": "h", "name": "FLUX.1 Kontext dev (quantized)", "base": "flux", "type": "main"},
    {"key": "q", "hash": "h", "name": "Qwen Image Edit 2511 (Q2_K)", "base": "qwen-image", "type": "main", "variant": "edit"},
    {"key": "j", "hash": "h", "name": "Juggernaut XL v9", "base": "sdxl", "type": "main"},
    {"key": "l", "hash": "h", "name": "Qwen Image Edit Lightning (8-step, bf16)", "base": "qwen-image", "type": "lora"},
    {"key": "fv", "hash": "h", "name": "FLUX.1-schnell_ae", "base": "flux", "type": "vae"},
    {"key": "qv", "hash": "h", "name": "Qwen Image VAE", "base": "qwen-image", "type": "vae"},
    {"key": "e3", "hash": "h", "name": "Z-Image Qwen3 Text Encoder", "base": "any", "type": "qwen3_encoder"},
    {"key": "t5", "hash": "h", "name": "t5", "base": "any", "type": "t5_encoder"},
    {"key": "cl", "hash": "h", "name": "clip", "base": "any", "type": "clip_embed"},
    {"key": "vl", "hash": "h", "name": "Qwen2.5-VL Encoder", "base": "any", "type": "qwen_vl_encoder"},
]


def _types(graph):
    return sorted(n["type"] for n in graph["nodes"].values())


def _linked(graph, into):
    return {(graph["nodes"][e["source"]["node_id"]]["type"], e["source"]["field"]) for e in graph["edges"]
            if graph["nodes"][e["destination"]["node_id"]]["type"] == into}


def test_the_graphs_follow_the_model_family():
    graph, output = invoke_ai.create_graph(MODELS, "Z-Image Turbo (Q8)", "a footballer", 2**40, 1344, 768)
    assert _types(graph) == ["z_image_denoise", "z_image_l2i", "z_image_model_loader", "z_image_text_encoder"]
    denoise = next(n for n in graph["nodes"].values() if n["type"] == "z_image_denoise")
    assert (denoise["width"], denoise["height"], denoise["steps"]) == (1344, 768, 9) and denoise["seed"] < 2**31
    assert graph["nodes"][output]["type"] == "z_image_l2i" and graph["nodes"][output]["is_intermediate"] is False

    kontext, _ = invoke_ai.edit_graph(MODELS, "FLUX.1 Kontext dev (quantized)", "in action", "face.png", 5)
    assert ("collect", "collection") in _linked(kontext, "flux_denoise") and "flux_kontext_image_prep" in _types(kontext)

    qwen, _ = invoke_ai.edit_graph(MODELS, "Qwen Image Edit 2511 (Q2_K)", "in action", "face.png", 5)
    denoise = next(n for n in qwen["nodes"].values() if n["type"] == "qwen_image_denoise")
    assert ("qwen_image_lora_loader", "transformer") in _linked(qwen, "qwen_image_denoise") and (denoise["steps"], denoise["cfg_scale"]) == (8, 1.0)

    with pytest.raises(invoke_ai.InvokeError, match="Z-Image or FLUX.2"):
        invoke_ai.create_graph(MODELS, "Juggernaut XL v9", "x", 1, 1024, 1024)
    with pytest.raises(invoke_ai.InvokeError, match="cannot edit"):
        invoke_ai.edit_graph(MODELS, "Z-Image Turbo (Q8)", "x", "face.png", 1)
    with pytest.raises(invoke_ai.InvokeError, match="no model"):
        invoke_ai.create_graph(MODELS, "Missing", "x", 1, 1024, 1024)


def test_the_models_offered(monkeypatch):
    monkeypatch.setattr(invoke_ai, "models", lambda url: MODELS)
    assert invoke_ai.main_models("http://x") == {"create": ["Z-Image Turbo (Q8)"],
                                                 "edit": ["FLUX.1 Kontext dev (quantized)", "Qwen Image Edit 2511 (Q2_K)"]}


@pytest.mark.usefixtures("app")
def test_invoke_is_a_maker_and_draws_the_portraits(monkeypatch):
    picture_makers.set_maker("invoke", commit=False)
    picture_makers.set_invoke("http://10.0.0.5:9090/app", "Z-Image Turbo (Q8)", "")
    assert picture_makers.invoke_url() == "http://10.0.0.5:9090" and picture_makers.invoke_edit_model() == picture_makers.DEFAULT_INVOKE_EDIT
    with pytest.raises(ValueError):
        picture_makers.set_invoke("10.0.0.5:9090", None, None)
    calls = []
    monkeypatch.setattr(invoke_ai, "draw", lambda url, model, prompt, seed, w, h: calls.append(("draw", model, w)) or b"png")
    monkeypatch.setattr(invoke_ai, "edit", lambda url, model, prompt, ref, seed, w=1024, h=1024: calls.append(("edit", model)) or b"png")
    assert picture_makers.draw_portrait("a face", 1) == b"png"
    picture_makers.draw("a scene", 1, 1344, 768)
    picture_makers.edit("in action", b"face", 1)
    assert calls == [("draw", "Z-Image Turbo (Q8)", 1024), ("draw", "Z-Image Turbo (Q8)", 1344), ("edit", picture_makers.DEFAULT_INVOKE_EDIT)]
