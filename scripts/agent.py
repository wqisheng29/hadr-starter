#!/usr/bin/env python3
"""Interactive HADR agent: a chat loop over the model with a system prompt and
two tools.

This is the whole idea in one script:

1. **Chat loop** — read a line, append it to the ``messages`` array, send the
   array to the model, print the reply.
2. **Standing orders** — the system prompt is just a text file, prepended as the
   first message (``--system``, default ``prompts/agent_system.md``). This is
   all a CLAUDE.md is.
3. **Tools** — ``fetch_feed`` and ``write_dashboard`` are wired in; the model
   asks, ``hadr.agent`` runs them, and the results go back into ``messages``.
4. **Agent loop** — ``hadr.agent.run_agent`` keeps going while the model keeps
   requesting tools.

Examples:

    export OPENCODE_API_KEY=...
    python scripts/agent.py                                   # live USGS, interactive
    python scripts/agent.py --fixture fixtures/usgs/all_day.json
    python scripts/agent.py --once --prompt "Brief me on today's earthquakes"
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from hadr import config, llm
from hadr.agent import ToolInvocation, run_agent
from hadr.clock import FrozenClock, SystemClock
from hadr.fetch import FixtureFeedSource, HttpFeedSource
from hadr.tools import ToolRegistry, fetch_feed_tool, write_dashboard_tool

DEFAULT_SYSTEM_PROMPT = Path("prompts/agent_system.md")
DEFAULT_OUT = Path("reports/sitrep.html")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive HADR sitrep agent.")
    parser.add_argument("--system", default=str(DEFAULT_SYSTEM_PROMPT),
                        help="text file whose contents become the system prompt (default %(default)s)")
    parser.add_argument("--model", help=f"model id (default {config.OPENCODE_MODEL})")
    parser.add_argument("--fixture", metavar="PATH",
                        help="serve fetch_feed from a recorded USGS body instead of the live feed")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="where write_dashboard saves its HTML (default %(default)s)")
    parser.add_argument("--as-of", metavar="ISO8601",
                        help="freeze the clock (e.g. 2026-07-08T00:30:00Z) so the dashboard is reproducible")
    parser.add_argument("--max-steps", type=int, default=8,
                        help="max model/tool round-trips per user turn (default %(default)s)")
    parser.add_argument("--prompt", help="a single user message to send")
    parser.add_argument("--once", action="store_true",
                        help="send --prompt (or one stdin line) and exit, without an interactive loop")
    return parser.parse_args(argv)


def _build_registry(fixture: str | None, out_path: str, clock) -> ToolRegistry:
    usgs = FixtureFeedSource(fixture) if fixture else HttpFeedSource(config.USGS_URL)
    return ToolRegistry([
        fetch_feed_tool({"usgs": usgs}),
        write_dashboard_tool(out_path, clock),
    ])


def _print_tool(inv: ToolInvocation) -> None:
    args = inv.arguments_json or "{}"
    if len(args) > 200:
        args = args[:200] + "…"
    print(f"  → {inv.name}({args})", file=sys.stderr)


def _turn(model, messages, registry, max_steps: int) -> None:
    """Run one user turn and print the reply (and any failure) to the user."""
    result = run_agent(model, messages, registry, max_steps=max_steps, on_tool=_print_tool)
    if result.ok:
        print(f"\n{result.reply}\n")
    else:
        print(f"\n✗ agent stopped: {result.error}\n", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    try:
        model = llm.from_env()
    except RuntimeError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    if args.model:
        model = llm.OpenCodeChatModel(config.OPENCODE_BASE_URL, _key(), args.model)

    try:
        system_prompt = Path(args.system).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"✗ could not read system prompt {args.system!r}: {exc}", file=sys.stderr)
        return 2

    clock = (
        FrozenClock(datetime.fromisoformat(args.as_of.replace("Z", "+00:00")))
        if args.as_of
        else SystemClock()
    )
    registry = _build_registry(args.fixture, args.out, clock)
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    print(f"model: {model.model} · tools: {', '.join(registry.names)} · "
          f"feed: {'fixture' if args.fixture else 'live'} · dashboard: {args.out}",
          file=sys.stderr)

    # One-shot: send a single message and exit.
    if args.once or args.prompt:
        user = args.prompt if args.prompt else sys.stdin.readline().strip()
        if not user:
            print("✗ nothing to send (use --prompt or pipe a line in)", file=sys.stderr)
            return 2
        messages.append({"role": "user", "content": user})
        _turn(model, messages, registry, args.max_steps)
        return 0

    # Interactive chat loop: read, append, run the agent, print, repeat.
    print("Type a message (Ctrl-D or /exit to quit).", file=sys.stderr)
    while True:
        try:
            user = input("you> ").strip()
        except EOFError:
            print(file=sys.stderr)
            break
        if user in {"/exit", "/quit"}:
            break
        if not user:
            continue
        messages.append({"role": "user", "content": user})
        _turn(model, messages, registry, args.max_steps)
    return 0


def _key() -> str:
    import os
    return os.environ[llm.ENV_API_KEY]


if __name__ == "__main__":
    raise SystemExit(main())
