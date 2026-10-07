import json

import httpx
import pytest

from coach.report import REPORT_SCHEMA, call_openrouter

GOOD = {"no_clear_pattern": True, "headline": "h", "problems": [],
        "proposed_edit": {"target": "none", "current_text": "", "new_text": "", "why": "", "how_to_test": ""}}


def _transport(seen, payload=None, status=200, finish="stop"):
    def handle(request):
        seen.append(request)
        body = payload if payload is not None else {
            "model": "deepseek/deepseek-v4.1-flash", "provider": "DeepSeek",
            "choices": [{"finish_reason": finish, "message": {"content": json.dumps(GOOD)}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "cost": 0.0003}}
        return httpx.Response(status, json=body)
    return httpx.MockTransport(handle)


def test_request_shape_and_parse(settings):
    settings.env.update(OPENROUTER_API_KEY="or-test", REPORT_MODEL="deepseek/deepseek-v4.1-flash")
    seen = []
    out, usage = call_openrouter(settings, "stats + transcripts", transport=_transport(seen))
    assert out == GOOD
    assert usage["cost_usd"] == 0.0003 and usage["provider"] == "DeepSeek"
    req = seen[0]
    body = json.loads(req.content)
    assert req.headers["authorization"] == "Bearer or-test"
    assert body["model"] == "deepseek/deepseek-v4.1-flash"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == REPORT_SCHEMA
    assert body["provider"] == {"require_parameters": True, "data_collection": "deny"}
    assert body["messages"][0]["role"] == "system" and body["messages"][1]["content"] == "stats + transcripts"


def test_model_switch(settings):
    settings.env.update(OPENROUTER_API_KEY="or-test", REPORT_MODEL="openai/gpt-6-luna")
    seen = []
    call_openrouter(settings, "x", transport=_transport(seen))
    assert json.loads(seen[0].content)["model"] == "openai/gpt-6-luna"


@pytest.mark.parametrize("kw,msg", [
    ({"status": 401, "payload": {}}, "rejected the key"),
    ({"finish": "length"}, "max_tokens"),
    ({"payload": {"choices": [{"finish_reason": "stop", "message": {"content": "not json"}}]}}, "invalid JSON"),
    ({"payload": {"error": {"message": "no provider"}}}, "OpenRouter error"),
])
def test_failures_are_explicit(settings, kw, msg):
    settings.env.update(OPENROUTER_API_KEY="or-test")
    with pytest.raises(RuntimeError, match=msg):
        call_openrouter(settings, "x", transport=_transport([], **kw))


def test_missing_key(settings):
    settings.env["OPENROUTER_API_KEY"] = ""
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        call_openrouter(settings, "x")
