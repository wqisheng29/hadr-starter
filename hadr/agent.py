"""The agent harness: a thin loop over a chat-completions model.

The model is injected, exactly as ``FeedSource`` and ``Clock`` are for the
deterministic core — so the loop is pure and testable with a fake, and the only
edge that touches the network is ``HttpChatModel``. Same seam, same discipline.

Level 1 — read input, send the messages array to the model, print the reply.
Level 2 — prepend a standing-orders file (e.g. ``CLAUDE.md``) as the system
prompt, loaded once at startup and carried in every model call.
Level 3 — one tool: the model may request ``fetch_feed``; the loop runs it and
puts the result back into the messages before asking the model again.
Level 4 — the agent loop: keep running tools and re-asking while the model keeps
requesting them; stop when it replies with plain content (or the iteration cap
fires). This is the loop ``/goal`` wraps a checker around.
"""

from pathlib import Path
from typing import Protocol

import httpx

from .tools import ToolRegistry, run_tool_calls


class ChatModel(Protocol):
    """Anything that turns a messages array (+ optional tool specs) into a reply.

    Returns the full assistant message dict (``{"role": "assistant", ...}``) so
    the loop can see both ``content`` and ``tool_calls``.
    """

    def complete(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> dict:
        ...


class HttpChatModel:
    """Calls an OpenAI-compatible ``/chat/completions`` endpoint over HTTP.

    The one network edge in the harness. Endpoint, key and model name are passed
    in (the CLI reads them from the environment) so no secret or host lives in
    library code.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._client = client or httpx.Client(timeout=60.0)

    def complete(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> dict:
        payload: dict = {"model": self._model, "messages": messages}
        if tools:
            payload["tools"] = tools
        resp = self._client.post(
            self._url,
            json=payload,
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]


def load_standing_orders(path: str | Path) -> str | None:
    """Read a standing-orders file (``CLAUDE.md`` by default).

    Returns the file text, or ``None`` if the file is absent — a missing orders
    file is a warning, not a crash, so the harness still runs without standing
    orders. An empty file is a real (if unusual) order set and is returned as ``""``.
    """
    p = Path(path)
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8")


def _print_content(msg: dict, writer) -> None:
    content = msg.get("content")
    if content:
        writer.write(content + "\n")
        writer.flush()


def chat_loop(
    model: ChatModel,
    reader,
    writer,
    system: str | None = None,
    tools: ToolRegistry | None = None,
    max_iterations: int = 10,
) -> list[dict]:
    """Read a line, ask the model, print the reply. Repeat until EOF.

    ``system`` is prepended once as a ``{"role": "system"}`` message and stays at
    the head of the history for every turn — these are the standing orders, not a
    per-turn instruction.

    ``tools`` is a ``ToolRegistry``; its specs are sent with every model call.
    This is the **agent loop**: after the model replies, if it requested tool
    calls, the loop runs them, appends the results, and asks the model again —
    and keeps doing so while the model keeps requesting tools. It stops when the
    model replies with plain content (no tool calls), or when ``max_iterations``
    tool-rounds have run for one user turn (a runaway-guard; the loop ``/goal``
    wraps a checker around can lower this). Each assistant reply's content is
    printed as it arrives.

    Returns the accumulated messages so callers and tests can inspect state. A
    failed turn prints the error and keeps the session alive — the loop degrades
    gracefully rather than dying on one bad request, the same posture the
    pipeline takes toward an unreachable feed.
    """
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    specs = tools.specs if tools else None
    while True:
        writer.write("> ")
        writer.flush()
        line = reader.readline()
        if not line:  # EOF (Ctrl-D / closed stream) — clean stop
            break
        text = line.strip()
        if not text:
            continue
        messages.append({"role": "user", "content": text})
        try:
            assistant = model.complete(messages, tools=specs)
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            writer.write(f"(error: {exc})\n")
            writer.flush()
            continue
        messages.append(assistant)
        _print_content(assistant, writer)

        # The agent loop: keep running tools while the model keeps asking.
        iterations = 0
        while assistant.get("tool_calls") and tools:
            run_tool_calls(assistant["tool_calls"], tools, messages)
            iterations += 1
            if iterations >= max_iterations:
                writer.write(f"(stopped: tool-call round limit {max_iterations} reached)\n")
                writer.flush()
                break
            try:
                assistant = model.complete(messages, tools=specs)
            except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
                writer.write(f"(error: {exc})\n")
                writer.flush()
                break
            messages.append(assistant)
            _print_content(assistant, writer)
    return messages
