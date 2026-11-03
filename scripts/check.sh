#!/usr/bin/env bash
# check.sh — the whole gate, locally. Format-free, dependency-free, ~2 seconds.
#
# This is what a CI job would run, except it runs here: the user's repos are
# private, GitHub Actions bills per minute on private repos, and no automation
# should accrue cost without being asked for. Keeping the gate in one script
# means adding hosted CI later is one step rather than a project.
#
# Exits non-zero on the first failure.
#
#   bash scripts/check.sh
#   bash scripts/check.sh --quick    # skip the accuracy gate
#   bash scripts/check.sh --live     # unit tests also run the live-install tests

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

QUICK=0
LIVE=0
for arg in "$@"; do
  case "$arg" in
    --quick) QUICK=1 ;;
    --live) LIVE=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

fail() { printf '\n\033[31mFAILED:\033[0m %s\n' "$1"; exit 1; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "Syntax"
for f in scripts/*.py tests/*.py; do
  python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$f" \
    || fail "syntax error in $f"
done
echo "  all python files parse"

step "Unit tests"
if [ "$LIVE" = "1" ]; then
  SKILL_ROUTER_CHECK_LIVE=1 python3 -m pytest tests -q || fail "unit tests (live)"
else
  python3 -m pytest tests -q || fail "unit tests"
fi

step "Hygiene"
if [ -e .hygiene-patterns ] || ls docs/jev-eval-*/cases.json >/dev/null 2>&1; then
  python3 scripts/hygiene.py || fail "hygiene — personal identifier or real prompt in a tracked file"
else
  echo "hygiene: no pattern file, skipped"
fi

if [ "$QUICK" = "0" ]; then
  step "Routing accuracy (109 curated prompts)"
  python3 tests/calibration.py --min-accuracy 95 || fail "accuracy gate"
fi

step "Health"
python3 scripts/doctor.py --gate || fail "doctor — routing is degraded"

printf '\n\033[32mAll checks passed.\033[0m\n'
