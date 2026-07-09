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
class ChatResult:
    """Outcome of one model call. ``ok`` gates ``text`` vs ``error``."""

    ok: bool
    text: str | None = None
    error: str | None = None


# Default token budget. Reasoning models (glm-5.2 spends ~750 tokens of hidden
# reasoning before a one-word answer) need headroom, or they truncate to an
# empty reply.
DEFAULT_MAX_TOKENS = 2048


class ChatModel(Protocol):
    def complete(
        self, messages: list[dict], *, max_tokens: int = DEFAULT_MAX_TOKENS
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
        self, messages: list[dict], *, max_tokens: int = DEFAULT_MAX_TOKENS
    ) -> ChatResult:
        payload = {"model": self._model, "messages": messages, "max_tokens": max_tokens}
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
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            return ChatResult(ok=False, error=f"unexpected response shape: {exc}")

        # Reasoning models (e.g. glm-5.2) burn max_tokens on hidden reasoning
        # before emitting content; a too-small budget yields HTTP 200 with an
        # empty reply. That is a failure, not an answer.
        if not content and choice.get("finish_reason") == "length":
            return ChatResult(
                ok=False,
                error="empty reply: max_tokens exhausted by reasoning before any "
                "content (finish_reason=length) — raise max_tokens",
            )

        return ChatResult(ok=True, text=content)

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
