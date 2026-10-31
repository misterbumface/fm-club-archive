"""The writers' questions to the archive (27 Oct 2026)."""

import json
from datetime import date

from app.services import archive_questions, story_writer
from tests.test_story_leads_now import LEAGUE, _season
from tests.test_world_pages import app, small_world  # noqa: F401


class _Reply:
    def __init__(self, data):
        self.status_code, self._data, self.text = 200, data, ""

    def json(self):
        return self._data


def test_the_writer_asks_then_writes(app, monkeypatch):
    """The model asks a question, gets the answer as a tool turn, then writes; the question and answer are returned."""
    sent = []
    replies = [
        {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "q1", "type": "function", "function": {"name": "league_table", "arguments": json.dumps({"competition": "Premier"})}}]},
            "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": json.dumps({"headline": "H", "body": "B"})}, "finish_reason": "stop"}]},
    ]
    monkeypatch.setattr(story_writer.requests, "post", lambda url, headers, json, timeout: (sent.append(json), _Reply(replies.pop(0)))[1])
    answer, asked = story_writer.request_with_questions("deepseek-chat", "k", [{"role": "user", "content": "{}"}],
                                                        archive_questions.TOOLS, lambda name, args: f"{name}:{args['competition']}", 6)
    assert answer == {"headline": "H", "body": "B"} and asked[0]["answer"] == "league_table:Premier"
    assert sent[1]["messages"][-1] == {"role": "tool", "content": "league_table:Premier", "tool_call_id": "q1"}
    assert "tools" in sent[0] and "tools" in sent[1]


def test_the_archive_answers_with_its_own_rules(small_world):
    _season(small_world)
    with small_world.connect() as c:
        archive = archive_questions.Archive(c, date(2063, 9, 3))
        table = json.loads(archive.answer("league_table", {"competition": "English Premier Division"}))
        assert table["table"][0].startswith("1. Chelsea: P4 W4 D0 L0") and table["table"][0].endswith("12 pts")
        wrong = json.loads(archive.answer("league_table", {"competition": "Premier League"}))
        assert "error" in wrong and "English Premier Division" in wrong["names"]  # not a guess at another country's league
        assert "results" in json.loads(archive.answer("recent_results", {"club": "Chelsea", "count": 2}))
        record = json.loads(archive.answer("head_to_head", {"club": "Chelsea", "other_club": "Liverpool"}))["meetings"][0]
        assert record.startswith("Chelsea in these")
        player = json.loads(archive.answer("player", {"name": "Sam Scorer"}))
        assert player["club"] == "Chelsea" and player["this_season"][0]["goals"] == 4
        rows = json.loads(archive.answer("sql", {"query": f"select count(*) from fixtures where competition_uid = {LEAGUE}"}))
        assert rows["rows"][0][0] > 0
        assert "error" in json.loads(archive.answer("sql", {"query": "delete from fixtures"}))
        assert "error" in json.loads(archive.answer("sql", {"query": "with x as (select 1) delete from fixtures"}))  # query_only refuses it
        assert c.exec_driver_sql("select count(*) from fixtures").scalar() > 0
        assert "error" in json.loads(archive.answer("nonsense", {}))


def test_after_the_last_question_the_writer_is_told_to_write(app, monkeypatch):
    sent = []
    ask = {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "q", "type": "function", "function": {"name": "player", "arguments": json.dumps({"name": "X"})}}]}}]}
    done = {"choices": [{"message": {"content": json.dumps({"headline": "H", "body": "B"})}}]}
    replies = [ask, ask, done]
    monkeypatch.setattr(story_writer.requests, "post", lambda url, headers, json, timeout: (sent.append(json), _Reply(replies.pop(0)))[1])
    answer, asked = story_writer.request_with_questions("deepseek-chat", "k", [{"role": "user", "content": "{}"}],
                                                        archive_questions.TOOLS, lambda name, args: "ok", 2)
    assert answer["headline"] == "H" and len(asked) == 2
    assert sent[2]["tool_choice"] == "none" and sent[2]["messages"][-1]["content"].startswith("That was the last question")
