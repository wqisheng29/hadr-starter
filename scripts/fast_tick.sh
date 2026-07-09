#!/usr/bin/env bash
# Fast / urgent tick — the ~30-minute poll+triage entry (ADR-0005, PRD #3).
#
# This is NOT a GitHub Action, and deliberately so: the urgent push is delivered
# over the harness's NATIVE push, which needs the harness agent context. So this
# runs as a Claude Code scheduled cloud agent (a routine, ~every 30 min) that
# shells out to the deterministic Python below; the agent layer only carries the
# push, it does not decide anything. The 08:30 brief, which needs no native push,
# lives in GitHub Actions instead (.github/workflows/sitrep.yml).
#
# What it does each firing:
#   1. Run the deterministic tick (fetch -> reconcile -> ledger -> [urgent push]
#      -> render). scripts/run.py fires the Slice-4 urgent push via the injected
#      sink; wiring the production sink to the harness push is the agent's job.
#   2. Commit ledger.db ONLY when the tick changed state, via the single-writer
#      commit helper (disjoint paths + pull --rebase + retry so it never clobbers
#      the brief's dashboard/snapshot commits).
#
# The tick also renders dashboard.html locally, but does NOT commit it — the
# 08:30 brief is the canonical writer of the committed dashboard (single-writer;
# see config.BRIEF_COMMIT_PATHS). On this cloud agent that render is ephemeral.
#
# Secrets live in the agent environment (ADR-0005), never in the repo.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Use the repo venv if present (harness image may already have deps on PATH).
if [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  . .venv/bin/activate
fi

DB="${HADR_DB:-state/ledger.db}"
OUT="${HADR_OUT:-dashboard.html}"

python scripts/run.py --live --db "$DB" --out "$OUT"

python scripts/commit_state.py --role tick \
  -m "chore(tick): reconcile ledger $(date -u +%Y-%m-%dT%H:%M:%SZ)"
