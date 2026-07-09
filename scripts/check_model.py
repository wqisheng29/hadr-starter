#!/usr/bin/env python3
"""Smoke-test the OpenCode Go model with your key.

Reads the key from the ``OPENCODE_API_KEY`` environment variable (never a flag —
keys do not belong in shell history), lists the models the gateway offers, and
sends one tiny prompt so you can confirm the credential and endpoint work before
anything in the app depends on them.

    export OPENCODE_API_KEY=...            # your OpenCode Go key
    python scripts/check_model.py          # uses config defaults (glm-5.2)
    python scripts/check_model.py --model kimi-k2.7-code
"""

import argparse
import sys

from hadr import config, llm


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the OpenCode Go model key.")
    parser.add_argument("--model", help=f"model id to test (default {config.OPENCODE_MODEL})")
    parser.add_argument("--prompt", default="Reply with exactly one word: pong",
                        help="prompt to send")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        model = llm.from_env()
    except RuntimeError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2

    if args.model:
        model = llm.OpenCodeChatModel(config.OPENCODE_BASE_URL,
                                      _key_from_env(), args.model)

    print(f"endpoint: {config.OPENCODE_BASE_URL}")
    print(f"model:    {model.model}")

    available = model.list_models()
    if available:
        print(f"models:   {', '.join(sorted(available))}")
        if model.model not in available:
            print(f"  note: '{model.model}' is not in the advertised list — the call may 404.")
    else:
        print("models:   (could not list — continuing to the test call anyway)")

    result = model.complete([{"role": "user", "content": args.prompt}])
    if result.ok:
        print(f"✓ reply:  {result.text!r}")
        return 0
    print(f"✗ call failed: {result.error}", file=sys.stderr)
    return 1


def _key_from_env() -> str:
    import os
    return os.environ[llm.ENV_API_KEY]


if __name__ == "__main__":
    raise SystemExit(main())
