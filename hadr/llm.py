"""The model boundary: an injected, OpenAI-compatible chat client.

The app's LLM provider is OpenCode Go — a gateway that speaks the OpenAI
``/chat/completions`` protocol (base ``https://opencode.ai/zen/go/v1``, bearer
auth). This module is the *edge*: like ``FeedSource`` and ``Clock`` it is a thin,
injectable seam, so callers can be tested against a fake model with no network.

Design mirrors the feed boundary:

* ``ChatModel`` is a Protocol — production wires ``OpenCodeChatModel``; tests
  wire a fake.
* Transport and HTTP failures come back as data (``ChatResult(ok=False, ...)``),
  never an exception to the caller — the same "failures are data" posture the
  pipeline takes toward an unreachable feed.

No feature calls this yet. It is the seam the LLM judgement layer (Slice 6,
ADR-0001) will consume; today it exists so an OpenCode Go key can be verified
against the app's config.
"""

import os
from dataclasses import dataclass
from typing import Protocol

import httpx

from . import config

# Environment variables (never hardcode the key). The base URL and model can be
# overridden per environment; only the key is required.
ENV_API_KEY = "OPENCODE_API_KEY"
ENV_BASE_URL = "OPENCODE_BASE_URL"
ENV_MODEL = "OPENCODE_MODEL"


@dataclass(frozen=True)
class ToolCall:
    """One tool the model asked us to run, straight off the wire.

    ``arguments_json`` is the raw JSON string the model emitted; it is *not*
    parsed here (a model can emit invalid JSON, and parsing it is the tool
    dispatcher's job so the failure can be handed back to the model as data).
    """

    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ChatResult:
    """Outcome of one model call.

    ``ok`` gates ``text``/``tool_calls`` vs ``error``. When the model wants a
    tool, ``tool_calls`` is non-empty and ``text`` is usually empty. ``message``
    is the assistant turn to append verbatim to the thread before the tool
    results — the OpenAI protocol requires the assistant's ``tool_calls`` message
    to precede the matching ``role: "tool"`` messages.
    """

    ok: bool
    text: str | None = None
    error: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    message: dict | None = None


# Default token budget. Reasoning models (glm-5.2 spends ~750 tokens of hidden
# reasoning before a one-word answer) need headroom, or they truncate to an
# empty reply.
DEFAULT_MAX_TOKENS = 2048


class ChatModel(Protocol):
    def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> ChatResult:
        ...


class OpenCodeChatModel:
    """Calls an OpenAI-compatible ``/chat/completions`` endpoint (OpenCode Go).

    ``base_url``, ``api_key`` and ``model`` are passed in — no secret or host
    lives in library code (the CLI/factory reads them from the environment).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._client = client or httpx.Client(follow_redirects=True, timeout=60.0)

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> ChatResult:
        payload: dict = {"model": self._model, "messages": messages, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            resp = self._client.post(
                f"{self._base}/chat/completions", json=payload, headers=self._headers
            )
        except httpx.HTTPError as exc:
            return ChatResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        if resp.status_code != 200:
            return ChatResult(ok=False, error=f"HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            choice = resp.json()["choices"][0]
            message = choice["message"]
            content = message.get("content")
            raw_tool_calls = message.get("tool_calls") or []
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            return ChatResult(ok=False, error=f"unexpected response shape: {exc}")

        tool_calls = tuple(
            ToolCall(
                id=tc.get("id", ""),
                name=tc.get("function", {}).get("name", ""),
                arguments_json=tc.get("function", {}).get("arguments", "") or "",
            )
            for tc in raw_tool_calls
            if isinstance(tc, dict)
        )

        # Rebuild the assistant turn to append to the thread. content may be null
        # when the model only calls tools; the protocol still wants the key.
        assistant_message: dict = {"role": "assistant", "content": content or ""}
        if raw_tool_calls:
            assistant_message["tool_calls"] = raw_tool_calls

        # Reasoning models (e.g. glm-5.2) burn max_tokens on hidden reasoning
        # before emitting content; a too-small budget yields HTTP 200 with an
        # empty reply. That is a failure, not an answer — unless the model spent
        # the turn asking for tools, in which case empty content is expected.
        if not content and not tool_calls and choice.get("finish_reason") == "length":
            return ChatResult(
                ok=False,
                error="empty reply: max_tokens exhausted by reasoning before any "
                "content (finish_reason=length) — raise max_tokens",
                message=assistant_message,
            )

        return ChatResult(
            ok=True, text=content, tool_calls=tool_calls, message=assistant_message
        )

    def list_models(self) -> list[str]:
        """Best-effort model ids from the gateway's ``/models`` endpoint.

        Returns ``[]`` on any failure — model discovery is a convenience, not a
        precondition for calling the model.
        """
        try:
            resp = self._client.get(f"{self._base}/models", headers=self._headers)
            resp.raise_for_status()
            data = resp.json().get("data", [])
        except (httpx.HTTPError, ValueError, AttributeError):
            return []
        return [m.get("id", "") for m in data if isinstance(m, dict)]


def from_env(client: httpx.Client | None = None) -> OpenCodeChatModel:
    """Build the model from the environment.

    Requires ``OPENCODE_API_KEY``; ``OPENCODE_BASE_URL`` and ``OPENCODE_MODEL``
    fall back to the OpenCode Go defaults in ``config``. Raises a clear error if
    the key is missing rather than making a doomed request.
    """
    api_key = os.environ.get(ENV_API_KEY)
    if not api_key:
        raise RuntimeError(
            f"{ENV_API_KEY} is not set. Export your OpenCode Go key, e.g. "
            f"`export {ENV_API_KEY}=...` (get one at https://opencode.ai/auth)."
        )
    base_url = os.environ.get(ENV_BASE_URL, config.OPENCODE_BASE_URL)
    model = os.environ.get(ENV_MODEL, config.OPENCODE_MODEL)
    return OpenCodeChatModel(base_url, api_key, model, client=client)
