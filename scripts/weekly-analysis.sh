#!/usr/bin/env bash
# weekly-analysis.sh — runs all three analysis scripts and writes a digest.
#
# Run manually any time, or automate via launchd / crontab (see setup/).
#
# Usage:
#   bash scripts/weekly-analysis.sh            # report only (safe, no writes)
#   bash scripts/weekly-analysis.sh --apply    # also promote repeated chains
#                                              # into SKILL.personal.md

set -euo pipefail

APPLY="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEEKLY_LOG="$HOME/.claude/skill_router_weekly.log"
TIMESTAMP="$(date '+%Y-%m-%dT%H:%M:%S')"

# Resolve python — prefer python3
py() { command -v python3 &>/dev/null && python3 "$@" || python "$@"; }

divider() { printf '\n%s\n' "───────────────────────────────────────────────────────"; }

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  skill-router weekly analysis"
echo "  $TIMESTAMP"
echo "═══════════════════════════════════════════════════════"

# ── 1 / 3  Announcement → invocation gap ──────────────────────────────
# Tells you: are announced skills actually being invoked? High gap = wrong skill
# announced, or iron-rule enforcement failing.
divider
echo "  1/3  Router learning — last 7 days"
echo "       (announced skills vs actually invoked)"
divider
py "$SCRIPT_DIR/learn-from-history.py" --days 7 || true

# ── 2 / 3  Dispatch protocol compliance ───────────────────────────────
# Tells you: of chains that were announced, how many had every step logged?
# Low score = router is going off-script after the announcement.
divider
echo "  2/3  Dispatch compliance — last 7 days"
echo "       (announced chains vs per-step logging)"
divider
py "$SCRIPT_DIR/audit-dispatch.py" --days 7 || true

# ── 3 / 3  Named chain candidates ─────────────────────────────────────
# Tells you: which chains have you run 3+ times? Those should be named so
# they bypass LLM triage entirely on future prompts.
divider
echo "  3/3  Named chain candidates — last 30 days, 3+ repeats"
if [[ "$APPLY" == "--apply" ]]; then
    echo "       (--apply: promoting to SKILL.personal.md)"
    divider
    py "$SCRIPT_DIR/learn-chains.py" --min 3 --days 30 --apply || true
else
    echo "       (dry-run — re-run with --apply to promote)"
    divider
    py "$SCRIPT_DIR/learn-chains.py" --min 3 --days 30 || true
fi

# ── Summary line to persistent log ────────────────────────────────────
APPLY_FLAG="$([[ "$APPLY" == "--apply" ]] && echo yes || echo no)"
echo "$TIMESTAMP  weekly-analysis  apply=$APPLY_FLAG" >> "$WEEKLY_LOG"

divider
echo ""
echo "  Weekly log: $WEEKLY_LOG"
if [[ "$APPLY" != "--apply" ]]; then
    echo "  To promote repeated chains automatically:"
    echo "    bash scripts/weekly-analysis.sh --apply"
fi
echo ""
