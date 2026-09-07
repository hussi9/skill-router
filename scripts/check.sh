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

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

fail() { printf '\n\033[31mFAILED:\033[0m %s\n' "$1"; exit 1; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "Syntax"
for f in scripts/*.py tests/*.py; do
  python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$f" \
    || fail "syntax error in $f"
done
echo "  all python files parse"

step "Unit tests"
python3 -m pytest tests/test_router.py tests/test_catalog_match.py tests/test_hooks.py tests/test_learn.py -q \
  || fail "unit tests"

if [ "$QUICK" = "0" ]; then
  step "Routing accuracy (109 curated prompts)"
  python3 tests/calibration.py --min-accuracy 95 || fail "accuracy gate"
fi

step "Health"
python3 scripts/doctor.py || fail "doctor — routing is degraded"

printf '\n\033[32mAll checks passed.\033[0m\n'
