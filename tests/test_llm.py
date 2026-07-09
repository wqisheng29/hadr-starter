"""Model-boundary tests: response parsing and error mapping via MockTransport
(no network), plus the env factory."""

import httpx
import pytest

from hadr import config, llm


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def _model(handler, model: str = "glm-5.2") -> llm.OpenCodeChatModel:
    return llm.OpenCodeChatModel("https://opencode.ai/zen/go/v1", "test-key", model, _client(handler))


def test_complete_extracts_content_and_sends_auth_and_model():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

    result = _model(handler).complete([{"role": "user", "content": "ping"}], max_tokens=8)

    assert result.ok and result.text == "pong"
    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer test-key"          # key goes in the header
    assert seen["body"]["model"] == "glm-5.2"          # configured model is sent
    assert seen["body"]["max_tokens"] == 8


def test_non_200_maps_to_error_not_exception():
    def handler(request):
        return httpx.Response(401, text="unauthorized")

    result = _model(handler).complete([{"role": "user", "content": "x"}])
    assert not result.ok and "HTTP 401" in result.error


def test_transport_error_maps_to_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    result = _model(handler).complete([{"role": "user", "content": "x"}])
    assert not result.ok and "ConnectError" in result.error


def test_unexpected_shape_maps_to_error():
    def handler(request):
        return httpx.Response(200, json={"nope": True})  # no choices

    result = _model(handler).complete([{"role": "user", "content": "x"}])
    assert not result.ok and "unexpected response shape" in result.error


def test_empty_reply_truncated_by_reasoning_is_an_error():
    # Verified live against glm-5.2: a reasoning model can spend the whole
    # max_tokens budget on reasoning_content and return 200 with content "".
    def handler(request):
        return httpx.Response(200, json={"choices": [{
            "message": {"content": "", "reasoning_content": "thinking..."},
            "finish_reason": "length",
        }]})

    result = _model(handler).complete([{"role": "user", "content": "x"}], max_tokens=32)
    assert not result.ok and "max_tokens" in result.error


def test_empty_reply_without_truncation_is_still_ok():
    # An empty string the model chose to return (finish_reason=stop) is a valid
    # answer — only length-truncation makes emptiness a failure.
    def handler(request):
        return httpx.Response(200, json={"choices": [{
            "message": {"content": ""}, "finish_reason": "stop",
        }]})

    result = _model(handler).complete([{"role": "user", "content": "x"}])
    assert result.ok and result.text == ""


def test_tools_are_sent_and_tool_calls_parsed():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "fetch_feed", "arguments": '{"source": "usgs"}'},
                }],
            },
            "finish_reason": "tool_calls",
        }]})

    tools = [{"type": "function", "function": {"name": "fetch_feed", "parameters": {}}}]
    result = _model(handler).complete([{"role": "user", "content": "go"}], tools=tools)

    assert result.ok
    assert seen["body"]["tools"] == tools          # schemas forwarded
    assert seen["body"]["tool_choice"] == "auto"
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert (call.id, call.name, call.arguments_json) == ("call_1", "fetch_feed", '{"source": "usgs"}')
    # the assistant turn is rebuilt for appending, content coerced from null
    assert result.message["role"] == "assistant" and result.message["content"] == ""
    assert result.message["tool_calls"][0]["id"] == "call_1"


def test_no_tools_means_no_tools_key():
    seen = {}

    def handler(request):
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    _model(handler).complete([{"role": "user", "content": "x"}])
    assert "tools" not in seen["body"] and "tool_choice" not in seen["body"]


def test_list_models_reads_data_ids_and_degrades():
    def ok(request):
        return httpx.Response(200, json={"data": [{"id": "glm-5.2"}, {"id": "kimi-k2.7-code"}]})

    assert _model(ok).list_models() == ["glm-5.2", "kimi-k2.7-code"]

    def down(request):
        return httpx.Response(500, text="err")

    assert _model(down).list_models() == []  # discovery failure is not fatal


def test_from_env_requires_key(monkeypatch):
    monkeypatch.delenv(llm.ENV_API_KEY, raising=False)
    with pytest.raises(RuntimeError, match=llm.ENV_API_KEY):
        llm.from_env()


def test_from_env_uses_defaults_and_overrides(monkeypatch):
    monkeypatch.setenv(llm.ENV_API_KEY, "k")
    monkeypatch.delenv(llm.ENV_BASE_URL, raising=False)
    monkeypatch.setenv(llm.ENV_MODEL, "deepseek-v4-pro")
    model = llm.from_env()
    assert model.model == "deepseek-v4-pro"            # env override wins
    # default base url comes from config when the env var is absent
    assert model.base_url == config.OPENCODE_BASE_URL == "https://opencode.ai/zen/go/v1"


def test_from_env_model_arg_overrides_but_keeps_base_url_env(monkeypatch):
    # A --model flag must not reset a custom base URL to the default.
    monkeypatch.setenv(llm.ENV_API_KEY, "k")
    monkeypatch.setenv(llm.ENV_BASE_URL, "https://gw.internal/v1")
    monkeypatch.setenv(llm.ENV_MODEL, "glm-5.2")
    model = llm.from_env(model="kimi-k2.7-code")
    assert model.model == "kimi-k2.7-code"             # explicit arg wins over env
    assert model.base_url == "https://gw.internal/v1"  # env base URL preserved


def test_null_content_without_tool_calls_is_empty_string():
    def handler(request):
        return httpx.Response(200, json={"choices": [{
            "message": {"content": None}, "finish_reason": "stop",
        }]})

    result = _model(handler).complete([{"role": "user", "content": "x"}])
    assert result.ok and result.text == ""  # never None, so the CLI won't print "None"
