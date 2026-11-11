from unittest.mock import patch

import pytest

from app import create_app
from app.extensions import db
from app.models import AppSetting
from app.services import story_writer
from app.services.ai_settings import get_deepseek_model


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {"choices": [{"message": {"content": '{"text": "Finished copy."}'}}]}


@pytest.fixture()
def settings_app():
    flask_app = create_app("testing")
    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()
        db.drop_all()


def test_deepseek_model_defaults_to_v4_pro(app):
    assert get_deepseek_model() == "deepseek-v4-pro"




def test_settings_page_saves_flash_for_every_ai_request(settings_app):
    settings_app.config["DEEPSEEK_API_KEY"] = "test-key"
    client = settings_app.test_client()

    response = client.post(
        "/settings/",
        data={"deepseek_model": "deepseek-flash"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"DeepSeek V4.1 Flash" in response.data
    assert AppSetting.query.filter_by(key="deepseek_model").one().value == (
        "deepseek-flash"
    )

    with patch(
        "app.services.story_writer.requests.post",
        return_value=_FakeResponse(),
    ) as post:
        story_writer._write("Write.", {"facts": "none"})

    assert post.call_args.kwargs["json"]["model"] == "deepseek-flash"


def test_settings_page_can_select_openai_luna_for_writing(settings_app):
    settings_app.config["OPENAI_API_KEY"] = "openai-test-key"
    client = settings_app.test_client()

    response = client.post(
        "/settings/",
        data={"deepseek_model": "gpt-6-luna"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"OpenAI model changed to GPT-6 Luna" in response.data
    assert AppSetting.query.filter_by(key="deepseek_model").one().value == (
        "gpt-6-luna"
    )
    assert b"OpenAI" in response.data and b"Public site" in response.data
    assert b"key set" in response.data

    with patch(
        "app.services.story_writer.requests.post",
        return_value=_FakeResponse(),
    ) as post:
        story_writer._write("Write.", {"facts": "none"})

    request = post.call_args
    assert request.args[0] == "https://api.openai.com/v1/chat/completions"
    assert request.kwargs["headers"]["Authorization"] == "Bearer openai-test-key"
    payload = request.kwargs["json"]
    assert payload["model"] == "gpt-6-luna"
    assert payload["reasoning_effort"] == "low"
    assert payload["max_completion_tokens"] == story_writer.MAX_TOKENS
    assert "thinking" not in payload
    assert "temperature" not in payload
    assert payload["response_format"] == {"type": "json_object"}
    assert "max_tokens" not in payload


def test_settings_page_rejects_unknown_model_without_changing_selection(settings_app):
    client = settings_app.test_client()
    client.post("/settings/", data={"deepseek_model": "deepseek-flash"})

    response = client.post(
        "/settings/",
        data={"deepseek_model": "invented-model"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Choose DeepSeek V4 Pro, DeepSeek V4.1 Flash, GPT-6 Luna or a local Ollama, LM Studio or oMLX model" in response.data
    assert get_deepseek_model() == "deepseek-flash"




def test_a_local_ollama_model_needs_no_key_and_asks_ollama_itself(settings_app):
    from app.services import ai_settings

    client = settings_app.test_client()
    client.post("/settings/", data={"deepseek_model": "ollama:qwen3:14b"})
    assert get_deepseek_model() == "ollama:qwen3:14b"
    assert ai_settings.ai_model_provider() == "ollama"
    assert ai_settings.resolve_ai_api_key() == ai_settings.OLLAMA_KEY
    url, headers, payload = ai_settings.ai_request_config(model="ollama:qwen3:14b", api_key="local", messages=[{"role": "user", "content": "hi"}],
                                                          max_tokens=100, temperature=0.4, response_format={"type": "json_object"})
    assert url.endswith("/api/chat") and "Authorization" not in headers
    assert (payload["model"], payload["format"], payload["think"], payload["stream"]) == ("qwen3:14b", "json", False, False)
    assert payload["options"] == {"num_ctx": ai_settings.OLLAMA_CONTEXT_MIN, "num_predict": 100, "temperature": 0.4}  # a short prompt
    big = [{"role": "user", "content": "x" * 60_000}]
    assert ai_settings.ollama_context(big, 5000) == 32768  # 25 000 + 5 000 + 1 024, rounded up to 4 096s
    assert ai_settings.ollama_context([{"role": "user", "content": "x" * 400_000}], 5000) == ai_settings.OLLAMA_CONTEXT_MAX
    assert ai_settings.ai_timeout("ollama:qwen3:14b", 180) == ai_settings.OLLAMA_TIMEOUT_SECONDS
    assert ai_settings.chat_content({"message": {"content": '{"text": "x"}'}, "done_reason": "stop"}) == ('{"text": "x"}', "stop")
    assert ai_settings.chat_content({"choices": [{"message": {"content": "y"}, "finish_reason": "length"}]}) == ("y", "length")
    assert ai_settings.deepseek_model_label("ollama:qwen3:14b") == "Ollama · qwen3:14b"


def test_an_lm_studio_model_on_another_machine_writes_archive_texts_and_papers(settings_app):
    """3 Nov 2026: LM Studio on a machine of the home network, at the address Settings keeps (never in the code)."""
    from app.services import ai_settings

    client = settings_app.test_client()
    assert ai_settings.lmstudio_url() == "http://127.0.0.1:1234"  # LM Studio's own default
    client.post("/settings/lmstudio", data={"lmstudio_url": " studio.example.test:1235/v1/ "})
    assert ai_settings.lmstudio_url() == "http://studio.example.test:1235"
    client.post("/settings/", data={"deepseek_model": "lmstudio:qwen3-30b-a3b"})
    assert get_deepseek_model() == "lmstudio:qwen3-30b-a3b" and ai_settings.ai_model_provider() == "lmstudio"
    assert ai_settings.resolve_ai_api_key() == ai_settings.OLLAMA_KEY  # no key
    url, headers, payload = ai_settings.ai_request_config(model="lmstudio:qwen3-30b-a3b", api_key="local", messages=[{"role": "user", "content": "hi"}],
                                                          max_tokens=100, temperature=0.4, response_format={"type": "json_object"})
    assert url == "http://studio.example.test:1235/v1/chat/completions" and "Authorization" not in headers
    assert (payload["model"], payload["max_tokens"], payload["temperature"], payload["reasoning_effort"]) == ("qwen3-30b-a3b", 100, 0.4, "none")
    assert payload["response_format"]["type"] == "json_schema"  # LM Studio takes no "json_object"
    assert ai_settings.ai_timeout("lmstudio:qwen3-30b-a3b", 180) == ai_settings.OLLAMA_TIMEOUT_SECONDS
    assert ai_settings.chat_content({"choices": [{"message": {"content": '<think>hm</think>\n```json\n{"text": "x"}\n```'}, "finish_reason": "stop"}]}) == (
        '{"text": "x"}', "stop")
    assert ai_settings.deepseek_model_label("lmstudio:qwen3-30b-a3b") == "LM Studio · qwen3-30b-a3b"
    ai_settings.set_papers_model("lmstudio:qwen3-30b-a3b")  # the papers may use it too
    assert ai_settings.get_papers_model() == "lmstudio:qwen3-30b-a3b"
    assert ai_settings.get_checker_model() == "deepseek-v4-pro"  # but the fact checker stays hosted
    client.post("/settings/checker-model", data={"checker_model": "gpt-6-luna"})
    assert ai_settings.get_checker_model() == "gpt-6-luna"
    client.post("/settings/checker-model", data={"checker_model": "lmstudio:qwen3-30b-a3b"})  # refused
    assert ai_settings.get_checker_model() == "gpt-6-luna"
    client.post("/settings/checker-model", data={"checker_model": ""})  # back to following the papers
    ai_settings.set_papers_model("deepseek-flash")
    assert ai_settings.get_checker_model() == "deepseek-flash"
    ai_settings.set_papers_model("lmstudio:qwen3-30b-a3b")
    client.post("/settings/lmstudio", data={"lmstudio_url": ""})
    assert ai_settings.lmstudio_url() == "http://127.0.0.1:1234"
    assert b"LM Studio" in client.get("/settings/").data


def test_a_saved_older_openai_model_becomes_its_successor(app):
    from app.extensions import db
    from app.models import AppSetting
    from app.services.ai_settings import DEEPSEEK_MODEL_SETTING_KEY, get_deepseek_model

    with app.app_context():
        db.session.add(AppSetting(key=DEEPSEEK_MODEL_SETTING_KEY, value="gpt-5.6-luna"))
        db.session.commit()
        assert get_deepseek_model() == "gpt-6-luna"


def test_the_papers_use_their_own_hosted_model(app):
    from app.extensions import db
    from app.services import ai_settings

    with app.app_context():
        assert ai_settings.get_papers_model() == "gpt-6-luna"  # the default
        ai_settings.set_deepseek_model("ollama:qwen3.5:4b")  # the fans and the rest: local
        ai_settings.set_papers_model("deepseek-flash")
        db.session.commit()
        assert ai_settings.get_papers_model() == "deepseek-flash" and ai_settings.get_deepseek_model() == "ollama:qwen3.5:4b"
        try:
            ai_settings.set_papers_model("ollama:qwen3.5:4b")
        except ValueError as exc:
            assert "for the papers" in str(exc)
        else:
            raise AssertionError("a local model must be refused for the papers")


def test_a_saved_choice_of_the_retired_flash_name_means_deepseek_flash_and_old_texts_keep_their_label(app):
    from app.extensions import db
    from app.services import ai_settings

    with app.app_context():
        db.session.add_all([AppSetting(key="deepseek_model", value="deepseek-v4-flash"),
                            AppSetting(key="papers_model", value="deepseek-v4-flash")])
        db.session.commit()
        assert ai_settings.get_deepseek_model() == ai_settings.get_papers_model() == "deepseek-flash"
        assert ai_settings.ai_model_provider() == "deepseek"
    assert ai_settings.deepseek_model_label("deepseek-v4-flash") == "DeepSeek V4 Flash"  # what the article was written with
    assert ai_settings.deepseek_model_label("deepseek-flash") == "DeepSeek V4.1 Flash"


def test_the_automatic_switch_reads_a_settled_new_save_and_chains_the_publish(settings_app, monkeypatch, tmp_path):
    """Settings > Automatic (25 Oct 2026): on, the archive reads its newest new save by itself once the game has finished
    writing it, writes the papers and publishes when a Cloudflare token is kept."""
    from datetime import datetime, timedelta

    from app.services import auto_pilot, save_jobs

    client = settings_app.test_client()
    assert auto_pilot.enabled() is False
    page = client.get("/settings/").data.decode()
    assert 'name="automatic" value="on"' in page and "checked" not in page.split('id="automatic"')[1].split("</section>")[0]
    assert client.post("/settings/automatic", data={"automatic": ["off", "on"]}).status_code == 302 and auto_pilot.enabled() is True  # as the form sends it
    assert "checked" in client.get("/settings/").data.decode().split('id="automatic"')[1].split("</section>")[0]

    fresh = save_jobs.SaveFile("Sam Archer - Chelsea v2.fm", datetime.now() - timedelta(seconds=10), 400.0, None, None)
    monkeypatch.setattr(save_jobs, "newest_waiting", lambda: fresh)
    assert auto_pilot.due() is None  # the game may still be writing it
    fresh.modified = datetime.now() - timedelta(seconds=auto_pilot.SETTLE_SECONDS + 1)
    assert auto_pilot.due() is fresh

    started, real_start_auto = [], save_jobs.start_auto
    monkeypatch.setattr(save_jobs, "busy", lambda app=None: False)
    monkeypatch.setattr(save_jobs, "start_auto", lambda app, name, on_failure=None: started.append(name))
    assert auto_pilot.run_once(settings_app) == fresh.name and started == [fresh.name]
    assert auto_pilot.last()["save"] == fresh.name and auto_pilot.last_look is not None
    monkeypatch.setattr(save_jobs, "list_saves", lambda: [fresh])
    monkeypatch.setattr(save_jobs, "belongs", lambda name: True)
    assert auto_pilot.watching()["newest"] == f"{fresh.name} is new and waits for the next look"
    assert "last look" in client.get("/settings/").data.decode()
    client.post("/settings/automatic", data={"automatic": ["off"]})
    assert auto_pilot.run_once(settings_app) is None and started == [fresh.name]  # off: nothing starts

    # the chain: the import with its editions, then the publish when the cloud is set up
    steps = []
    monkeypatch.setattr(save_jobs, "_import", lambda path, make_editions, set_status, min_days=None: (steps.append(("import", path.name, make_editions, min_days)), set_status("Done: imported it; 3 editions.")))
    monkeypatch.setattr(save_jobs, "_publish", lambda app, set_status, reuse=False, prefix="", workers=None: steps.append(("publish", prefix, workers)))
    monkeypatch.setattr(save_jobs, "cloud_configured", lambda: True)
    monkeypatch.setattr(save_jobs, "_start", lambda app, work: work(save_jobs._setter(app)))
    monkeypatch.setattr(save_jobs, "GAMES_DIR", tmp_path)
    (tmp_path / fresh.name).write_bytes(b"fm")
    real_start_auto(settings_app, fresh.name)
    assert steps == [("import", fresh.name, True, 2), ("publish", "imported it; 3 editions; ", 5)]  # papers for two days or more of game time; five render processes
    steps.clear()
    monkeypatch.setattr(save_jobs, "cloud_configured", lambda: False)
    real_start_auto(settings_app, fresh.name)
    assert steps == [("import", fresh.name, True, 2)]  # no token: no publish

    # a failed import is not started again every look (27 Oct 2026): only when the game writes the save again
    def broken(path, make_editions, set_status, min_days=None):
        raise TypeError("bad row")
    monkeypatch.setattr(save_jobs, "_import", broken)
    with pytest.raises(TypeError):
        real_start_auto(settings_app, fresh.name, on_failure=lambda: auto_pilot.failed(fresh))
    client.post("/settings/automatic", data={"automatic": ["off", "on"]})
    started.clear()
    assert auto_pilot.run_once(settings_app) is None and started == []
    fresh.modified += timedelta(seconds=1)  # the game saved again
    assert auto_pilot.run_once(settings_app) == fresh.name and started == [fresh.name]


@pytest.mark.parametrize("chosen, automatic, expected", [("3", None, 3), ("4", 5, 4), ("8", 5, 5), ("8", None, 8), ("99", None, 8)])
def test_the_publish_box_sets_the_cores_of_the_export(settings_app, monkeypatch, chosen, automatic, expected):
    from app.services import save_jobs, site_publish

    client = settings_app.test_client()
    client.post("/settings/publish", data={"hostname": "", "account_id": "", "workers": chosen})
    page = client.get("/settings/").data.decode()
    assert "Cores for the export" in page and f'<option value="{site_publish.export_workers()}" selected>' in page
    used = {}
    monkeypatch.setattr(site_publish, "publish", lambda *a, **k: used.update(k) or {"address": "x", "files": 1})
    monkeypatch.setattr("app.public.create_public_app", lambda: None)
    monkeypatch.setattr("app.services.story_leads.followed_clubs", lambda: (630,))
    save_jobs._publish(settings_app, lambda text: None, workers=automatic)
    assert used["workers"] == expected


def test_a_second_draw_things_address_is_saved_and_draws_when_chosen(settings_app, monkeypatch):
    from app.services import picture_makers, world_portraits

    asked = []
    monkeypatch.setattr(world_portraits, "_request_image", lambda endpoint, body, url, timeout: (asked.append(endpoint), b"png")[1])
    client = settings_app.test_client()
    data = {"picture_maker": "draw_things", "dt_second_address": "http://192.168.1.20:7860/", "dt_server": "this_mac"}
    client.post("/settings/pictures", data=data)
    assert picture_makers.draw_things_second_address() == "http://192.168.1.20:7860" and picture_makers.draw_things_url() == world_portraits.DRAW_THINGS_URL
    world_portraits.draw("a face", 1)
    client.post("/settings/pictures", data={**data, "dt_server": "second"})
    page = client.get("/settings/").data.decode()
    assert 'value="http://192.168.1.20:7860"' in page and '<option value="second" selected>' in page
    world_portraits.draw("a face", 1)
    world_portraits.draw_edit("in action", b"face", 1)
    assert asked == [f"{world_portraits.DRAW_THINGS_URL}/sdapi/v1/txt2img", "http://192.168.1.20:7860/sdapi/v1/txt2img", "http://192.168.1.20:7860/sdapi/v1/img2img"]
    client.post("/settings/pictures", data={**data, "dt_second_address": "not an address", "dt_server": "second"})
    assert picture_makers.draw_things_second_address() == "http://192.168.1.20:7860"  # a bad address changes nothing
    with pytest.raises(ValueError):
        picture_makers.set_draw_things_server("", True)


def test_draw_things_over_grpc_sends_the_reference_where_the_model_reads_it(settings_app, monkeypatch):
    """11 Nov 2026: over HTTP FLUX.3 drew a new man for an age-up; gRPC sends Kontext its canvas, the others a moodboard."""
    from app.services import draw_things_grpc, picture_makers, world_portraits

    sent = []
    monkeypatch.setattr(draw_things_grpc, "generate", lambda url, prompt, model, seed, width=1024, height=1024, **kw:
                        (sent.append((url, model, sorted(k for k, v in kw.items() if v and k in ("image", "references")))), b"png")[1])
    client = settings_app.test_client()
    client.post("/settings/pictures", data={"picture_maker": "draw_things", "dt_second_address": "https://192.168.1.20:7859",
                                            "dt_server": "second", "dt_protocol": "grpc"})
    assert picture_makers.draw_things_grpc() and picture_makers.draw_things_url() == "https://192.168.1.20:7859"
    assert '<option value="grpc" selected>' in client.get("/settings/").data.decode()
    world_portraits.draw("a face", 1, model="flux_3_klein_i8x.ckpt")
    world_portraits.draw_edit("older", b"face", 1, model="flux_1_kontext_dev_q5p.ckpt")
    world_portraits.draw_edit("older", b"face", 1, model="flux_3_klein_i8x.ckpt")
    assert sent == [("https://192.168.1.20:7859", "flux_3_klein_i8x.ckpt", []),
                    ("https://192.168.1.20:7859", "flux_1_kontext_dev_q5p.ckpt", ["image"]),
                    ("https://192.168.1.20:7859", "flux_3_klein_i8x.ckpt", ["references"])]
    assert draw_things_grpc.settings_for("flux_1_kontext_dev_q5p.ckpt")["steps"] == 20 and draw_things_grpc.settings_for("flux_3_klein_i8x.ckpt")["steps"] == 8
    client.post("/settings/pictures", data={"picture_maker": "draw_things", "dt_second_address": "https://192.168.1.20:7859",
                                            "dt_server": "second", "dt_protocol": "http"})
    assert not picture_makers.draw_things_grpc()


def test_an_omlx_server_is_a_second_local_writer(settings_app):
    """10 Nov 2026: oMLX on a machine of the home network, beside LM Studio, at its own address (never in the code)."""
    from app.services import ai_settings

    client = settings_app.test_client()
    assert ai_settings.server_url("omlx") == "http://127.0.0.1:8000"
    client.post("/settings/local-server/omlx", data={"omlx_url": "mlx.example.test:8000/v1"})
    assert ai_settings.server_url("omlx") == "http://mlx.example.test:8000" and ai_settings.lmstudio_url() == "http://127.0.0.1:1234"
    model = "omlx:Qwen3.8-27B-oQ4e-mtp"
    client.post("/settings/", data={"deepseek_model": model})
    assert ai_settings.get_deepseek_model() == model and ai_settings.ai_model_provider(model) == "omlx"
    url, headers, payload = ai_settings.ai_request_config(model=model, api_key="local", messages=[{"role": "user", "content": "Hi"}],
                                                          max_tokens=50, response_format={"type": "json_object"})
    assert url == "http://mlx.example.test:8000/v1/chat/completions" and payload["model"] == "Qwen3.8-27B-oQ4e-mtp" and "Authorization" not in headers
    assert payload["chat_template_kwargs"] == {"enable_thinking": False} and payload["response_format"]["type"] == "json_schema"
    assert ai_settings.resolve_ai_api_key(model=model) == "local" and ai_settings.deepseek_model_label(model) == "oMLX · Qwen3.8-27B-oQ4e-mtp"
    ai_settings.set_papers_model(model)
    assert ai_settings.get_papers_model() == model and ai_settings.get_checker_model() == ai_settings.DEFAULT_DEEPSEEK_MODEL  # never checks facts
    assert client.post("/settings/local-server/nothing").status_code == 404
    page = client.get("/settings/").data.decode()
    assert 'id="omlx"' in page and 'value="http://mlx.example.test:8000"' in page and 'id="lmstudio"' in page

