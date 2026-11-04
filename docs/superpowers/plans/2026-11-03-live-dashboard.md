# Live dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A local, live "mission-control" web dashboard showing what skill-router and Jev are doing right now, from the logs the installed skill already writes.

**Architecture:** `scripts/live_dashboard.py` — stdlib `http.server` bound to 127.0.0.1 serving one self-contained HTML page (inline CSS/JS/SVG, dark Jarvis style matching the v2.1 promo video) and `GET /api/state` (JSON aggregated from local logs, recomputed at most every 3 s; slow parts cached). The page polls `/api/state` every 3 s. Launched by `python3 scripts/cli.py dashboard [--port 8790] [--days 7]`.

**Spec:** `docs/superpowers/specs/2026-11-02-skill-router-v3-vision.md` (R2 privacy/no burden, R3 measure outcomes).

## Verified inputs (2026-11-03, live machine)

| Input | Lines / state |
|---|---|
| `~/.claude/skill_router_log.jsonl` (prompt, chain-start, invoke, subagent events) | 11,782 |
| `~/.claude/skill_router_cache/jev-decisions.jsonl` (Jev confidence, latency, cache hits; hashes only) | 2,397 |
| `~/.claude/skill_router_outcomes.jsonl` (shown / loaded / tests / correction / held_out) | 244 (recording since the 2026-11-02 sync) |
| `~/.claude/skill_router_cache/jev-features.jsonl` | 98 |
| `~/.claude/skill_router_cache/listing/<key>.json` (measured /context) | 1 project |
| `~/.claude/skill_router_cache/visibility-summary.json` | present |

## Global Constraints

- Read-only: never writes logs, settings or caches other than its own in-memory cache.
- Binds 127.0.0.1 only; refuses other hosts. No external network, no CDN assets, no fonts download.
- No prompt text anywhere (logs carry keywords/hashes; show skill names, counts, confidences, latencies, timestamps only). Escape every string rendered in HTML.
- Never runs Jev, the `/context` probe or the 10 s transcript scan on a request; the audit runs at most once per 10 min in a background thread and is shown with its age.
- Every panel says "no data yet" instead of 0 when its source is empty (outcomes are new).
- Tests offline; suite-wide temp HOME and live guard apply.
- Commits intent-first, ending `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Task D1: live dashboard

**Interfaces:** `aggregate(paths: Paths, days: int, now: float) -> dict` (pure; keys: `header`, `events` (last 40: ts, kind route|shortlist|silent|held_out|loaded|tests_green|tests_red, skills, jev_conf, mode), `jev` (decisions, cache_hit_rate, median_ms, p90_ms, confidence histogram 10 bins, route_rate ≥0.8), `routing` (cards, shortlists, silent, per day), `outcomes` (summarize + lift from scripts/outcomes.py), `listing` (measured line or null), `visibility` (summary or null), `audit` (score, unused, overlaps, thin, age_s or null), `top_skills` (most routed/loaded, 10)); `serve(port, days)`; HTML panels: header, line-sphere of installed skills with recently routed ones lit, Jev confidence histogram with the 0.80 gate, live event stream, routing counters, outcomes + holdout lift ("no data yet" until known > 0), listing budget meter, audit donut, top skills.

- [ ] Tests: `test_aggregate_counts_and_histogram`, `test_empty_sources_say_no_data`, `test_no_prompt_text_in_state`, `test_binds_localhost_only`, `test_html_escapes_skill_names`, `test_state_endpoint_json` (server on an ephemeral port in a thread).
- [ ] Implement, run on the real machine, screenshot it with the Playwright MCP or headless browser if available (else curl /api/state and report key counts), `bash scripts/check.sh`, commit "Show what skill-router and Jev are doing, live".
