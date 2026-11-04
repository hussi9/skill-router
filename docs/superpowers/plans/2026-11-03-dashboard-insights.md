# Dashboard insights — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans.

**Goal:** Make the live dashboard say what the numbers mean and what to do next, not only show them.

**Architecture:** A pure `scripts/dashboard_insights.py` turns the existing `/api/state` aggregate (plus outcomes events) into `insights` (one sentence per panel), `actions` (ranked to-do list), `ignored` and `missed` skill lists, and `confidence` badges. `live_dashboard.py` adds them to `/api/state`; the page renders a sentence under each panel title, a "Do this next" panel, and an "Ignored / missed" panel. No new logging.

**Spec:** `docs/superpowers/specs/2026-11-02-skill-router-v3-vision.md` (R2, R3); dashboard plan `docs/superpowers/plans/2026-11-03-live-dashboard.md`.

## Verified inputs
Same logs as the dashboard (verified 2026-11-03): outcomes `shown` events carry card + shortlist names and source; `signal` events carry `loaded` skill; router log `invoke` events carry the skill loaded per prompt; audit gives unused/overlaps/thin; listing gives entries/described. Real values today: 57 lines shown, 9% followed (5 from cards, 0 from shortlists), 168 unused, 3 overlaps, 25 thin.

## Global Constraints
- Deterministic templates — no LLM call, no Jev call to write insights.
- Every sentence states its sample size; below the threshold it says so instead of concluding: n < 10 "too early", 10–29 "early signal", ≥ 30 "solid".
- No prompt text; skill names only. Escape in HTML.
- Actions name the exact next step (a command, a file to edit, or "review in `cli.py audit`"); never perform them.
- Keep the one-screen layout at 1280×800 and 1920×1080 (verify with screenshots).
- Tests offline, synthetic data; `bash scripts/check.sh` passes.

## Task I1: insights, actions, ignored/missed, confidence

**Interfaces:**
- `badge(n: int) -> str` → "too early" | "early signal" | "solid".
- `insights(state: dict) -> dict[str, str]` keyed by panel (`jev`, `routing`, `outcomes`, `listing`, `audit`, `visibility`), each one sentence with numbers and the badge where relevant. Example (outcomes): "Claude used 9% of suggested skills (5 of 57 lines): 5 from routes, 0 from shortlists — early signal."
- `ignored(events, days, min_shown=3) -> list[{skill, shown, loaded, avg_jev}]` — skills shown ≥ 3× and loaded 0×, most-shown first, max 8.
- `missed(events, days) -> list[{skill, times}]` — skills loaded on a prompt where skill-router showed nothing or other skills, max 8.
- `actions(state, ignored, missed) -> list[{title, why, how, weight}]` ranked by weight, max 6. Sources: thin descriptions (names listed via the audit, "edit the SKILL.md description"), Jev-confirmed duplicates (pair names), unused (count + "review with `python3 scripts/cli.py audit`"), each ignored skill with high shows ("description may not match — check it, or it's a routing miss"), each missed skill ("Jev never suggested it — improve its description"), holdout/outcome sample too small ("keep using it ~N more days").
- `/api/state` gains `insights`, `actions`, `ignored`, `missed`, `confidence` (per metric n + badge).
- Page: one muted sentence under every panel title; new "DO THIS NEXT" panel (numbered, title + one-line why + how in monospace); new "IGNORED / MISSED" panel (two short lists with counts); holdout/outcome numbers carry their badge. Replace a lower-value panel (e.g. visibility) to keep one screen.

- [ ] Tests: `test_badge_thresholds`, `test_insight_sentences_carry_numbers_and_badges`, `test_ignored_requires_three_shows_and_zero_loads`, `test_missed_counts_loads_without_matching_suggestion`, `test_actions_ranked_and_capped`, `test_state_has_insights`, `test_no_prompt_text`.
- [ ] Implement → run live → Playwright screenshots at both sizes (read them) → `bash scripts/check.sh` → commit "Make the dashboard say what the numbers mean and what to do next".
