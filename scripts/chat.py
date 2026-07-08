#!/usr/bin/env python3
"""CLI entrypoint for the agent harness.

Thin by design, like ``scripts/run.py``: read config from the environment, load
standing orders, build a model + tools, hand them to ``chat_loop``. All
behaviour lives in the ``hadr`` package; nothing network-shaped is constructed
here.

    OPENAI_BASE_URL=https://api.example.com/v1 \
    OPENAI_API_KEY=... HADR_MODEL=glm-5.2 python scripts/chat.py

    # offline: feed the agent a recorded fixture instead of the live feed
    python scripts/chat.py --fixture fixtures/usgs/all_day.json
"""

import argparse
import os
import sys

from hadr import config
from hadr.agent import HttpChatModel, chat_loop, load_standing_orders
from hadr.fetch import FixtureFeedSource, HttpFeedSource
from hadr.tools import make_default_tools


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HADR agent harness")
    parser.add_argument(
        "--system",
        default="CLAUDE.md",
        help="standing-orders file prepended as the system prompt (default: %(default)s)",
    )
    parser.add_argument(
        "--fixture",
        metavar="PATH",
        help="use a recorded USGS feed body instead of the live feed (offline mode)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=10,
        help="cap on tool-call rounds per user turn (runaway guard, default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        default=str(config.DEFAULT_OUT_PATH),
        help="where write_dashboard saves the assessed HTML page (default: %(default)s)",
    )
    parser.add_argument(
        "--as-of", metavar="ISO8601",
        help="freeze the dashboard 'as of' time (e.g. 2026-07-08T00:30:00Z) for reproducible demos",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    api_key = os.environ.get("OPENAI_API_KEY")
    model = os.environ.get("HADR_MODEL", "glm-5.2")
    if not api_key:
        print("OPENAI_API_KEY is not set", file=sys.stderr)
        return 2

    system = load_standing_orders(args.system)
    if system is None:
        print(f"(no standing orders at {args.system}; running without a system prompt)",
              file=sys.stderr)

    source = FixtureFeedSource(args.fixture) if args.fixture else HttpFeedSource(config.USGS_URL)
    now = None
    if args.as_of:
        from datetime import datetime
        now = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
    tools = make_default_tools({"usgs": source}, dashboard_path=args.out, now=now)

    chat_loop(HttpChatModel(base_url, api_key, model), sys.stdin, sys.stdout,
              system=system, tools=tools, max_iterations=args.max_iterations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
