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
    assert config.OPENCODE_BASE_URL == "https://opencode.ai/zen/go/v1"
