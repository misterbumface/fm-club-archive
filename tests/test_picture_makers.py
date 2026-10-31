"""Who draws the pictures (picture_makers): Draw Things by default; WaveSpeed's API with a key and a model path."""

import json

import pytest

from app.services import picture_makers


@pytest.mark.usefixtures("app")
def test_the_maker_is_a_setting_with_a_model_path():
    assert picture_makers.maker() == "draw_things" and picture_makers.wavespeed_model() == picture_makers.DEFAULT_WAVESPEED_MODEL
    picture_makers.set_maker("wavespeed", "wavespeed-ai/flux-2-max/text-to-image")
    assert picture_makers.maker() == "wavespeed" and picture_makers.wavespeed_model() == "wavespeed-ai/flux-2-max/text-to-image"
    with pytest.raises(ValueError):
        picture_makers.set_maker("wavespeed", "not-a-path")
    with pytest.raises(ValueError):
        picture_makers.set_maker("paint")
    assert picture_makers.online() == (False, "WaveSpeed has no API key: add it on the Settings page")  # no keys in tests


@pytest.mark.usefixtures("app")
def test_wavespeed_submits_polls_and_fetches_the_picture(monkeypatch):
    calls = []
    answers = iter([{"data": {"id": "p1", "status": "created", "outputs": []}},
                    {"data": {"id": "p1", "status": "processing", "outputs": []}},
                    {"data": {"id": "p1", "status": "completed", "outputs": ["https://cdn/pic.jpg"]}}])
    monkeypatch.setattr(picture_makers, "wavespeed_key", lambda: "ws-key")
    monkeypatch.setattr(picture_makers, "_call", lambda url, key, body=None, timeout=60: (calls.append((url, key, body)), next(answers))[1])
    monkeypatch.setattr(picture_makers, "_fetch", lambda url, timeout=120: b"jpeg:" + url.encode())
    picture = picture_makers.wavespeed_draw("A press photograph.", seed=7, width=1344, height=768, sleep=lambda s: None)
    assert picture == b"jpeg:https://cdn/pic.jpg"
    assert calls[0][0].endswith("/api/v3/wavespeed-ai/z-image/turbo") and calls[0][1] == "ws-key"
    assert calls[0][2] == {"prompt": "A press photograph.", "size": "1344*768", "seed": 7, "output_format": "jpeg",
                           "enable_sync_mode": False, "enable_base64_output": False}
    assert [c[0] for c in calls[1:]] == [picture_makers.WAVESPEED_URL + "/predictions/p1/result"] * 2 and all(c[2] is None for c in calls[1:])


@pytest.mark.usefixtures("app")
def test_wavespeed_failures_are_told(monkeypatch):
    monkeypatch.setattr(picture_makers, "wavespeed_key", lambda: "ws-key")
    monkeypatch.setattr(picture_makers, "_call", lambda *a, **k: {"data": {"id": "p2", "status": "failed", "error": "content policy"}})
    with pytest.raises(picture_makers.PictureError, match="failed: content policy"):
        picture_makers.wavespeed_draw("x", seed=1, width=1024, height=1024, sleep=lambda s: None)
    monkeypatch.setattr(picture_makers, "_call", lambda *a, **k: {"code": 401, "message": "bad key"})
    with pytest.raises(picture_makers.PictureError, match="refused"):
        picture_makers.wavespeed_draw("x", seed=1, width=1024, height=1024, sleep=lambda s: None)
    monkeypatch.setattr(picture_makers, "wavespeed_key", lambda: None)
    with pytest.raises(picture_makers.PictureError, match="no API key"):
        picture_makers.wavespeed_draw("x", seed=1, width=1024, height=1024)
    big = {}
    monkeypatch.setattr(picture_makers, "wavespeed_key", lambda: "ws-key")
    monkeypatch.setattr(picture_makers, "_call", lambda url, key, body=None, timeout=60: (big.update(body or {}), {"data": {"id": "p3", "status": "completed", "outputs": ["u"]}})[1])
    monkeypatch.setattr(picture_makers, "_fetch", lambda url, timeout=120: b"x")
    picture_makers.wavespeed_draw("x", seed=1, width=2048, height=1024, sleep=lambda s: None)
    assert big["size"] == "1536*768"  # scaled to the API's longest side, the same shape


@pytest.mark.usefixtures("app")
def test_gpt_image_is_asked_for_a_shape_not_a_size(monkeypatch):
    picture_makers.set_maker("wavespeed", "openai/gpt-image-2/text-to-image")
    sent = {}
    monkeypatch.setattr(picture_makers, "wavespeed_key", lambda: "ws-key")
    monkeypatch.setattr(picture_makers, "_call", lambda url, key, body=None, timeout=60: (sent.update({"url": url, **(body or {})}),
                                                                                       {"data": {"id": "p4", "status": "completed", "outputs": ["u"]}})[1])
    monkeypatch.setattr(picture_makers, "_fetch", lambda url, timeout=120: b"x")
    picture_makers.wavespeed_draw("x", seed=1, width=1344, height=768, sleep=lambda s: None)
    assert sent["url"].endswith("/openai/gpt-image-2/text-to-image") and "size" not in sent and "seed" not in sent
    assert (sent["aspect_ratio"], sent["resolution"], sent["quality"], sent["output_format"]) == ("16:9", "1k", "medium", "jpeg")
    assert picture_makers.aspect_ratio(1024, 1024) == "1:1" and picture_makers.aspect_ratio(768, 1344) == "9:16"


@pytest.mark.usefixtures("app")
def test_an_action_picture_uploads_the_portrait_and_edits_it(monkeypatch):
    picture_makers.set_maker("wavespeed", None, "openai/gpt-image-2/edit")
    calls, puts = [], []
    answers = iter([{"code": 200, "data": {"upload": {"method": "PUT", "url": "https://up/1", "headers": {"x": "y"}}, "download_url": "https://cdn/in.png"}},
                    {"data": {"id": "e1", "status": "completed", "outputs": ["https://cdn/out.jpg"]}}])
    monkeypatch.setattr(picture_makers, "wavespeed_key", lambda: "ws-key")
    monkeypatch.setattr(picture_makers, "_call", lambda url, key, body=None, timeout=60: (calls.append((url, body)), next(answers))[1])
    monkeypatch.setattr(picture_makers, "_put", lambda url, headers, data, timeout=120: puts.append((url, headers, data)))
    monkeypatch.setattr(picture_makers, "_fetch", lambda url, timeout=120: b"jpeg:" + url.encode())
    assert picture_makers.edit("He strikes the ball.", b"png-bytes", seed=3) == b"jpeg:https://cdn/out.jpg"
    assert calls[0] == (picture_makers.UPLOAD_URL, {"filename": "portrait.png", "size": 9, "content_type": "image/png"})
    assert puts == [("https://up/1", {"x": "y"}, b"png-bytes")]
    assert calls[1][0].endswith("/openai/gpt-image-2/edit") and calls[1][1]["images"] == ["https://cdn/in.png"] and "seed" not in calls[1][1]
    assert (calls[1][1]["aspect_ratio"], calls[1][1]["output_format"]) == ("1:1", "jpeg")
    monkeypatch.setattr(picture_makers, "_call", lambda *a, **k: {"code": 200, "data": {}})
    with pytest.raises(picture_makers.PictureError, match="no upload address"):
        picture_makers.wavespeed_upload(b"x")
    assert issubclass(picture_makers.PictureError, picture_makers.world_portraits.PortraitError)


@pytest.mark.usefixtures("app")
def test_openai_draws_and_edits_with_the_gpt_image_model(monkeypatch):
    import base64

    picture_makers.set_maker("openai", openai_model_name="gpt-image-2.5-sunburst", openai_quality_name="low")
    assert picture_makers.maker() == "openai" and picture_makers.online() == (False, "OpenAI has no API key: add it on the Settings page")
    with pytest.raises(ValueError):
        picture_makers.set_maker("openai", openai_quality_name="ultra")
    sent = []
    monkeypatch.setattr(picture_makers, "openai_key", lambda: "sk-test")
    monkeypatch.setattr(picture_makers, "_openai", lambda path, key, body, content_type, timeout=300: (
        sent.append((path, key, body, content_type)), {"data": [{"b64_json": base64.b64encode(b"jpeg-bytes").decode()}]})[1])
    assert picture_makers.draw("A press photograph.", seed=1, width=1344, height=768) == b"jpeg-bytes"
    path, key, body, content_type = sent[0]
    assert (path, key, content_type) == ("generations", "sk-test", "application/json")
    assert json.loads(body) == {"model": "gpt-image-2.5-sunburst", "prompt": "A press photograph.", "size": "1536x1024", "quality": "low",
                                "n": 1, "output_format": "jpeg"}
    assert picture_makers.openai_size(768, 1344) == "1024x1536" and picture_makers.openai_size(1024, 1024) == "1024x1024"
    assert picture_makers.edit("He strikes the ball.", b"\x89PNG-bytes", seed=2) == b"jpeg-bytes"
    path, _, body, content_type = sent[1]
    assert path == "edits" and content_type.startswith("multipart/form-data; boundary=")
    assert b'name="image[]"; filename="portrait.png"' in body and b"\x89PNG-bytes" in body and b'name="model"\r\n\r\ngpt-image-2.5-sunburst' in body
    assert b"input_fidelity" not in body
    monkeypatch.setattr(picture_makers, "_openai", lambda *a, **k: {"error": {"message": "safety"}})
    with pytest.raises(picture_makers.PictureError, match="no picture"):
        picture_makers.openai_draw("x", 1024, 1024)


@pytest.mark.usefixtures("app")
def test_draw_things_models_are_chosen_in_settings(monkeypatch):
    from app.extensions import db
    from app.services import world_portraits

    assert picture_makers.draw_things_illustration_model() == world_portraits.ILLUSTRATION_DEFAULT
    assert picture_makers.draw_things_portrait_model() == world_portraits.PORTRAIT_MODEL
    picture_makers.set_draw_things_models("flux_2_klein.ckpt", "qwen_image.ckpt", "")
    db.session.commit()
    assert picture_makers.draw_things_illustration_model() == "flux_2_klein.ckpt"
    assert picture_makers.draw_things_portrait_model() == "qwen_image.ckpt"
    assert picture_makers.draw_things_edit_model() == world_portraits.EDIT_MODEL  # empty: the default
    with pytest.raises(ValueError):
        picture_makers.set_draw_things_models(None, "not a file/name", None)
    assert "cyberrealistic_xl_f16.ckpt" in picture_makers.draw_things_known("cyberrealistic_xl_f16.ckpt")
    assert "cyberrealistic_xl_f16.ckpt" in picture_makers.draw_things_known(None)  # remembered
    sent = {}
    monkeypatch.setattr(world_portraits, "draw_edit", lambda prompt, reference, seed, model, **size: sent.update(model=model) or b"png")
    picture_makers.edit("x", b"face", 1)
    assert sent["model"] == world_portraits.EDIT_MODEL


@pytest.mark.usefixtures("app")
def test_a_portrait_comes_from_the_chosen_maker(monkeypatch):
    calls = []
    monkeypatch.setattr(picture_makers, "openai_draw", lambda prompt, w, h: calls.append(("openai", w, h)) or b"png")
    monkeypatch.setattr(picture_makers, "wavespeed_draw", lambda prompt, seed, w, h: calls.append(("wavespeed", w, h)) or b"png")
    monkeypatch.setattr(picture_makers.world_portraits, "draw", lambda *a, **k: calls.append(("draw things",)) or b"png")
    for name in ("openai", "wavespeed", "draw_things"):
        picture_makers.set_maker(name, commit=False)
        picture_makers.draw_portrait("a face", 1)
    assert calls == [("openai", 1024, 1024), ("wavespeed", 1024, 1024), ("draw things",)]
