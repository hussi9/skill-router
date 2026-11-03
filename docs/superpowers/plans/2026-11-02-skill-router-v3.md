# skill-router v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn skill-router into a Jev-centred quality layer for agent skills that measures outcomes, costs Claude less context, works across agents, and ships tooling for skill users, authors and teams.

**Architecture:** Waves, each a tagged release. Waves 0–1 are specified to step level. Waves 2–7 are specified to task level (files, interfaces, tests, acceptance bars) and are each expanded into their own step-level plan, `docs/superpowers/plans/<date>-wave-N-<name>.md`, when the wave starts, because their details depend on earlier waves' measurements. Every feature has a Jev path and a keyless path.

**Tech Stack:** Python 3.11+ stdlib for hooks and CLI; optional `fastembed` + `numpy` in `.venv` for local embeddings; Jev via `https://api.typesafe.ai/v1/systemone` (`jev-1.13.0`); pytest; MCP Python SDK in Wave 3 only.

**Spec:** `docs/superpowers/specs/2026-11-02-skill-router-v3-vision.md`

## Wave gate (owner instruction, 2026-11-02)

Every wave ends with a retro in `docs/superpowers/retros/<date>-wave-N.md`: what worked, what did not, fixes to make now (run as a `Wave N.x` task list through the same review loop), and changes to the next wave's plan. Only then does the next wave start.

Input rule (Wave 2 retro): before a wave's tasks are dispatched, every input a task reads (file, cache field, hook payload key, API field) is verified to exist with real data — the plan records the command and its output. A task built on an unverified input ships off-by-default.

Feasibility rule (Wave 4 retro): every wave starts with a spike that simulates its headline benefit on real data; machinery is built only if the spike clears the wave's bar.

Order change (Wave 4 retro): a new Wave 5 (archive assistant) runs before Wave 3.

Order change (Wave 2 retro): Wave 4 runs before Wave 3 — the audit measured ~23.5k listing tokens per session, the largest cost the router can remove.

## Global Constraints

- Two modes, always: every feature works with `TYPESAFE_API_KEY` and with no key; benchmarks report both (spec R1).
- Injected context: route card as today; any other line ≤ 200 chars, work turns only, 0 chat false alarms on the 6 controls in `docs/jev-eval-2026-10-22-real/cases.json` (R2).
- Jev routing threshold stays **0.8**; any change requires a re-measurement documented in `docs/` (existing rule).
- Logs hold keywords, hashes and skill names, never prompt text; benchmark case files stay git-ignored (R4).
- Never install, invoke or execute a skill for the user (R5).
- Tests are offline: no network, no provider calls, no reads of the developer's live `~/.claude` unless `SKILL_ROUTER_CHECK_LIVE=1`.
- Gate: `bash scripts/check.sh` passes (unit tests, calibration ≥ 95 %); a fresh-clone run (`HOME=<tmp>` with the repo at `<tmp>/.claude/skills/skill-router`) passes.
- No paid CI. GitHub Actions only on this public repo, unmetered.
- Commits: intent-first message, `Constraint:/Rejected:/Confidence:/Scope-risk:` trailers when they add information, ending with the attribution line.
- New env knobs are named `SKILL_ROUTER_<NAME>`, documented in the doc that owns the feature.

## Review Focus

1. **A prompt that is both chat and work** ("do you think we should migrate to Postgres?"). Expected: the Jev shortlist may appear; a route card must not, unless Jev ≥ 0.8. Test: Task 1.3 `test_question_phrased_work_request_gets_shortlist_not_card`.
2. **Jev slow or failing mid-session.** Expected: keyless path answers within the 1.2 s budget; no line repeats from the previous turn. Test: Task 1.2 `test_holdout_and_timeout_never_both_print`.
3. **A holdout turn on a hard-gated project route.** Expected: hard routes (BROKEN path, gated projects) are never held out; safety beats measurement. Test: Task 1.2 `test_hard_routes_are_never_held_out`.
4. **Skill names containing shell or markdown metacharacters in any printed line.** Expected: printed verbatim only if they match the identifier pattern, else dropped. Test: Task 2.1 `test_report_drops_unsafe_names`.
5. **Huge installs (1,000+ skills).** Expected: index split across Jev Choice questions (exists), doctor and shortlist stay under 200 ms keyless. Test: Task 2.1 `test_audit_runs_on_1200_synthetic_skills_under_2s`.

---

## Wave 0 — Ship what is built (v2.1.0)

### Task 0.1: Stop hook accepts a skip line the model already wrote

The Stop hook asks for `[skill-router] skipped <skill>: <reason>` even when the last assistant message already contains it (observed twice on 2026-11-02).

**Files:**
- Modify: `scripts/iron_rule_hook.py` (soft-tier branch near line 196; add a helper)
- Test: `tests/test_hooks.py`

**Interfaces:**
- Produces: `last_assistant_text(transcript_path: str, max_bytes: int = 200_000) -> str`; `skip_reason(text: str, skill: str) -> str | None` (the reason after the colon, or None).

- [ ] **Step 1: Write the failing tests**

```python
def test_skip_line_in_last_message_is_accepted(self):
    text = "Done.\n\n[skill-router] skipped superpowers:brainstorming: owner asked for a vision, not a dialogue"
    self.assertEqual(self.mod.skip_reason(text, "superpowers:brainstorming"),
                     "owner asked for a vision, not a dialogue")

def test_skip_line_for_another_skill_is_not_accepted(self):
    self.assertIsNone(self.mod.skip_reason("[skill-router] skipped writing-plans: x", "superpowers:brainstorming"))

def test_stop_does_not_block_when_reason_already_given(self):
    # Fixture: transcript.jsonl whose last {"type":"assistant"} message text ends with
    # "[skill-router] skipped superpowers:brainstorming: owner asked for a vision";
    # pending state {"remaining": ["superpowers:brainstorming"], "tier": "soft"}.
    out = run_stop_hook(transcript_path=fixture, session_id="s1")
    self.assertEqual(out.strip(), "")                       # no block emitted
    event = last_log_event()
    self.assertEqual((event["type"], event["reason"]), ("soft-skip", "owner asked for a vision"))
```

- [ ] **Step 2: Run** `python3 -m pytest tests/test_hooks.py -k skip_line -v` — Expected: FAIL (`skip_reason` not defined).
- [ ] **Step 3: Implement** both helpers; in the soft branch, read `transcript_path` from the Stop payload, and if `skip_reason` returns a reason, call `record_soft_skip(remaining, session_id)` with `"reason"` added to the logged event and return without emitting.
- [ ] **Step 4: Run** `python3 -m pytest tests/test_hooks.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** — "Accept a skip line the model already wrote instead of asking again".

### Task 0.2: Release v2.1.0

**Files:** Modify `VERSION` (`2.1.0`), `CHANGELOG.md` (rename "Unreleased" to `v2.1.0 (public) — <date>`).

- [ ] **Step 1:** `bash scripts/check.sh` and the fresh-clone run — Expected: both pass.
- [ ] **Step 2:** Rebase `feat/no-key-routing` onto `release/v2.0.0-clean`, squash to one commit, confirm no personal identifiers remain in tracked files, using a grep pattern kept in a git-ignored local file.
- [ ] **Step 3:** Owner approval → `git push origin HEAD:main`, tag `v2.1.0`, push the tag.

---

## Wave 1 — Jev at the core, measured (v2.2.0)

Outcome: the router knows whether its lines helped, with an honest holdout, and the keyless work gate stops dropping question-phrased tasks.

### Task 1.1: Outcome events

**Files:**
- Create: `scripts/outcomes.py`
- Modify: `scripts/skill_invoked.py` (record which shown line a load came from), `scripts/router.py` (`log_prompt_event` adds `shown: {"card": [...], "shortlist": [...], "source": "jev"|"keyless"}`)
- Test: `tests/test_outcomes.py`

**Interfaces:**
- Produces: `record_shown(session_id: str, prompt_id: str, card: list[str], shortlist: list[str], source: str) -> None`; `record_signal(session_id: str, prompt_id: str, kind: Literal["loaded","tests_green","tests_red","correction","rework"], skill: str | None = None) -> None`; `summarize(since_days: int = 30) -> dict` returning `{"shown": int, "loaded_from_card": int, "loaded_from_shortlist": int, "followed_rate": float, "tests_green_after": int, "corrections_after": int, "by_mode": {"jev": {...}, "keyless": {...}}}`.
- Storage: append-only `~/.claude/skill_router_outcomes.jsonl` (mode 600), keys only, no prompt text.

Signals:
- `loaded`: a Skill call in the same turn naming a shown skill (from `skill_invoked.py`).
- `tests_green` / `tests_red`: PostToolUse Bash whose command matches `pytest|vitest|jest|npm test|go test|cargo test` and whose exit code is 0 / non-zero, within the turn.
- `correction`: the next user prompt in the session matches `^(no|wrong|not that|undo|revert|that's not|stop)\b` (case-insensitive).
- `rework`: a later turn within 24 h edits the same file set (hash of sorted paths) after a green turn.

- [ ] **Step 1: Write failing tests**: `test_shown_then_loaded_counts_as_followed`, `test_shortlist_load_is_attributed_to_shortlist`, `test_test_command_exit_codes_map_to_signals`, `test_correction_regex_matches_only_leading_negation`, `test_no_prompt_text_is_ever_written` (assert the log has no key named `prompt` and no value longer than 200 chars), `test_summary_splits_jev_and_keyless`.
- [ ] **Step 2: Run** `python3 -m pytest tests/test_outcomes.py -v` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `scripts/outcomes.py`; wire the PostToolUse Bash matcher into `install_hooks.py` (new event entry `PostToolUse` matcher `Bash` → `outcomes.py --post-bash`), keeping `OWNED_SCRIPTS` in sync.
- [ ] **Step 4: Run** the full suite — Expected: PASS.
- [ ] **Step 5: Commit** — "Record whether shown skills were loaded and whether the work then succeeded".

### Task 1.2: Random holdout

**Files:** Modify `scripts/router.py` (`main`), `scripts/outcomes.py`; Test `tests/test_holdout.py`.

**Interfaces:**
- Consumes: `outcomes.record_shown`.
- Produces: `holdout(session_id: str, prompt_id: str, rate: float) -> bool` (deterministic: `sha256(session_id + prompt_id)` mod 10,000 < rate × 10,000); `SKILL_ROUTER_HOLDOUT` (default `0.10`, `0` disables). A held-out turn logs what would have been shown (`held_out: true`) and prints nothing.

- [ ] **Step 1: Failing tests**: `test_holdout_is_deterministic_per_prompt`, `test_rate_zero_never_holds_out`, `test_hard_routes_are_never_held_out` (BROKEN path or project `tier: hard`), `test_held_out_turn_prints_nothing_but_logs_would_show`, `test_holdout_and_timeout_never_both_print`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** in `main` after `route()`: hard tier → never held out; else if `holdout(...)` → log and return 0.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** — "Hold out a tenth of soft-routed turns so lift can be measured honestly".

### Task 1.3: Work gate that understands question-phrased tasks

Keyless retrieval finds the skill in the top three on 11/27 labeled tasks, but the gate shows a line on only 8 of them because they are phrased as questions.

**Files:** Modify `scripts/index_match.py` (`is_request`), `scripts/router.py` (`shortlist_line`, `jev_shortlist_line`); Test `tests/test_no_key_layers.py`.

**Interfaces:**
- Produces: `work_signal(prompt: str) -> float` in `index_match.py`, 0–1: imperative or request marker 1.0; question about a named artifact/system with a deliverable noun (`page|app|site|schema|pipeline|deploy|post|profile|audit|plan|engine|integration|platform`) 0.7; pure concept question (`what is|mean|difference between`) 0.1; greeting/ack ≤ 3 words 0.0.
- Shortlist gate becomes `work_signal(prompt) >= 0.6 or jev path >= 0.8 work`.

- [ ] **Step 1: Failing tests**: `test_question_phrased_work_request_gets_shortlist_not_card` ("could the recipe site turn into a paid app" → shortlist allowed, no card), `test_concept_questions_stay_silent` (all 6 controls → 0.0–0.3), `test_imperatives_are_work`.
- [ ] **Step 2–4:** run (FAIL) → implement → run (PASS).
- [ ] **Step 5: Re-measure**: `python3 scripts/benchmark_nokey.py --labeled <cases> --mined <mined> --floor 4 --jev` and `--embed` variant; record in `docs/no-key-routing.md`. **Bar:** keyless shortlist (with embed) right-in-3 ≥ 9/27 labeled and 0/6 controls → flip `SKILL_ROUTER_SHORTLIST` default to on when the daemon is running; Jev path must not lose any of its 7/27 and 38/74.
- [ ] **Step 6: Commit** — "Let question-phrased work requests reach the shortlist".

### Task 1.4: Distillation dataset

**Files:** Create `scripts/jev_dataset.py`; Modify `scripts/jev_choose.py` (log `ranked` names and `path`, already hashed input); Test `tests/test_jev_dataset.py`.

**Interfaces:**
- Produces: `export(out: Path, since_days: int = 90) -> dict` writing `out/train.jsonl` rows `{"prompt_sha256", "features": {"lex_top10": [...], "sem_top10": [...]}, "label_ranked": [...], "label_loaded": str | None}` — no prompt text; features are computed at log time from local retrieval so training needs no raw prompts.
- Consumes: `outcomes.summarize` data for `label_loaded`.

- [ ] **Steps:** failing tests (`test_rows_have_no_text_fields`, `test_label_prefers_loaded_skill_over_jev_rank`) → implement → pass → commit "Keep Jev's decisions as training data for a local chooser".

### Task 1.5: `skill-router stats`

**Files:** Create `scripts/cli.py` (entry: `python3 scripts/cli.py stats [--days N]`); Test `tests/test_cli.py`.

**Interfaces:** `stats(days: int) -> str` renders: shown lines, follow rate by mode (jev / keyless), holdout lift (success rate routed − held out, with n), tokens injected per turn (median chars / 4).

- [ ] **Steps:** failing test on a fixture outcomes log → implement → pass → commit. Release v2.2.0 after Wave 1 with `docs/outcomes.md`.

---

## Wave 2 — `skill doctor` for everyone (v2.3.0)

Outcome: a 30-second audit any user can run that shows what their skills cost and which are dead weight, with a shareable score.

### Task 2.1: Skill audit engine
- **Files:** Create `scripts/skill_audit.py`; Test `tests/test_skill_audit.py`.
- **Interfaces:** `audit(catalog: list[dict], usage: dict[str, int], skills_sh: dict | None) -> AuditReport` (dataclass: `listing_tokens: int`, `unused_90d: list[str]`, `overlaps: list[tuple[str, str, float]]`, `flagged: list[str]`, `score: int` 0–100, `top_actions: list[str]`).
- **Rules:** `listing_tokens` = sum over skills of `len(name + description) / 4`; overlap = keyword Jaccard ≥ 0.6 keyless, or Jev Noul "do these two skills do the same job?" ≥ 0.8 with a key (batched, ≤ 1 call per 20 pairs); flagged = skills.sh audit `flagged`.
- **Tests:** `test_listing_tokens_counts_descriptions`, `test_unused_uses_learner_window`, `test_overlap_keyless_and_jev_paths`, `test_report_drops_unsafe_names`, `test_audit_runs_on_1200_synthetic_skills_under_2s`.

### Task 2.2: Report renderers
- **Files:** Modify `scripts/cli.py` (`audit` subcommand: `--format text|md|html`); Test `tests/test_cli.py`.
- **Acceptance:** text fits 30 lines; html is a single self-contained file; neither includes paths under `$HOME` beyond `~`.

### Task 2.3: Doctor integration
- **Files:** Modify `scripts/doctor.py` (one advisory line: score and top action).
- **Bar to release:** run on the author's machine and one clean machine; numbers documented in `docs/skill-audit.md`.

---

## Wave 3 — One warm process and an MCP server (v3.0.0)

Outcome: one background service answers hooks in < 20 ms and any MCP-capable agent can call the router.

### Task 3.1: `routerd` service
- **Files:** Create `scripts/routerd.py` (absorbs `embedder_daemon.py`; keeps its socket protocol ops `ping|classify|rank_skills` and adds `route|shortlist|audit`); Modify `scripts/router.py` (`main` tries the socket first, 150 ms budget, falls back to in-process); `setup/launchd-routerd.plist`; Test `tests/test_routerd.py`.
- **Interfaces:** request `{"op": "route", "prompt": str, "session_id": str, "prompt_id": str, "context": str}` → `{"text": str, "chain": [...], "shortlist": [...], "ms": float}`.
- **Tests:** `test_socket_route_matches_in_process_route` (same 109 calibration prompts), `test_absent_service_falls_back_in_process`, `test_p95_under_20ms_warm` (marked slow, opt-in).

### Task 3.2: MCP server
- **Files:** Create `mcp/server.py` (tools: `route(task)`, `explain(task)`, `find_skill(capability)`, `audit_skills()`), `mcp/README.md` with Codex, Cursor and Gemini CLI config snippets; Test `tests/test_mcp_tools.py` (tool functions called directly, no transport).
- **Constraint:** tools return names and reasons only; `find_skill` reads the local skills.sh cache, never the network.

### Task 3.3: Thin hooks
- Hook scripts become < 40-line clients of `routerd`; old logic stays as the fallback module. Bar: fresh-clone suite passes with and without the service running.

---

## Wave 4 — Show Claude only what helps (v3.1.0)

Outcome: measurably fewer listing tokens per session without hiding a skill the user needs.

### Task 4.1: Measure the listing
- **Files:** Create `scripts/listing_cost.py` (reads the session's first system reminder from the transcript; counts skills and tokens); Test with a fixture transcript.

### Task 4.2: Visibility levers (prototype, each behind its own flag)
- `SKILL_ROUTER_SCOPE_PLUGINS=1`: per-project `enabledPlugins` overrides written to `<project>/.claude/settings.local.json` from the project's routing history (never touches user settings).
- `SKILL_ROUTER_SCOPE_SKILLS=1`: per-project `.claude/skills/` symlink set of the top-N relevant skills, with the user-level listing unchanged.
- Description compression: `cli.py descriptions --suggest` proposes shorter descriptions for third-party skills as a patch file for their authors; never edits them.
- **Tests:** each lever is reversible (`--undo` restores byte-identical files); no lever removes a skill the learner saw used in that project in 90 days.
- **Bar:** listing tokens −40 % on the author's top three projects with no drop in follow rate (Wave 1 data) over two weeks; otherwise ship off.

---

## Wave 5 — Local chooser distilled from Jev (v3.2.0)

Outcome: keyless users get most of Jev's judgment; Jev users get a free first pass.

### Task 5.1: Train
- **Files:** Create `train/distill.py` (`.venv`: numpy, scikit-learn), `models/router-local-v1.npz` (≤ 5 MB); Test `tests/test_local_chooser.py`.
- **Method:** features = fused retrieval ranks + bge-small cosine to top-10 candidates; logistic ranking head trained on Task 1.4 rows; label = loaded skill, else Jev's top-1 when ≥ 0.8.
- **Bar:** keyless right-in-3 ≥ 70 % of Jev's on both eval sets; else stays opt-in.

### Task 5.2: Bring-your-own chooser
- **Files:** Create `scripts/choosers/openai_compat.py` (any OpenAI-compatible endpoint, including local llama.cpp with logprobs); Modify `scripts/jev_choose.py` to dispatch by `SKILL_ROUTER_CHOOSER=jev|local|openai`.
- **Interfaces:** every chooser returns the same `Choice` (domain, process, path, tier, ranked).
- **Tests:** `test_all_choosers_return_choice_shape`, `test_unknown_option_names_dropped`.

---

## Wave 6 — Routing through the whole task (v3.3.0)

Outcome: the router reacts when a task changes shape, not only at the first prompt.

### Task 6.1: Lifecycle triggers
- **Files:** Create `scripts/lifecycle.py`; Modify `install_hooks.py`.
- **Triggers:** PostToolUse Bash test failure (2 in a row) → one line naming the debugging skill; ExitPlanMode → TDD skill; Edit/Write diff > 300 lines in a turn → review skill; quota ≥ 80 % (existing feed) → cheaper-model line.
- **Constraints:** each trigger once per session per skill; same R2 burden rules; Jev path confirms the skill (Noul "does this skill apply now?" ≥ 0.8), keyless path uses the fixed mapping.
- **Tests:** one per trigger plus `test_triggers_respect_session_once`.

---

## Wave 7 — Ecosystem (v3.4.0+, owner-gated)

### Task 7.1: Skill tests and badge
- `skill-tests.yml` format (should-fire / should-not-fire prompts per skill); `cli.py test-skill <dir>` runs them against both modes; a GitHub Action for public skill repos; a badge endpoint is **owner decision** (hosting).

### Task 7.2: `skills.lock`
- `cli.py lock` writes name → source → commit SHA → content hash for every installed skill; `cli.py verify` diffs the install against it. Tests: hash stability, symlink handling, drift detection.

### Task 7.3: Auto-drafted skills
- From the learner's chains: a workflow repeated ≥ 5 times in 30 days → a draft `SKILL.md` in `~/.claude/skill-drafts/` for review. Never installed automatically.

### Task 7.4: State of Agent Skills report
- Monthly `skills_sh_catalog.py --report` plus Wave 2 aggregate (opt-in) → `reports/<yyyy-mm>.md`. Publishing is an **owner decision**.

### Task 7.5: Opt-in shared outcomes
- Design doc only until the owner approves a hosted backend: aggregated per-skill lift with differential-privacy noise, no prompts.

---

## Release bars (summary)

| Wave | Release | Ships on if | Otherwise |
|---|---|---|---|
| 0 | v2.1.0 | gate + fresh clone pass | — |
| 1 | v2.2.0 | outcomes recorded on real sessions for 7 days | holdout default 0 |
| 2 | v2.3.0 | audit < 2 s on 1,200 skills | — |
| 3 | v3.0.0 | socket route identical to in-process on calibration | hooks stay in-process |
| 4 | v3.1.0 | −40 % listing tokens, follow rate not lower | levers off |
| 5 | v3.2.0 | local ≥ 70 % of Jev right-in-3 | local opt-in |
| 6 | v3.3.0 | 0 chat false alarms, follow rate ≥ shortlist's | triggers off |
| 7 | v3.4.0+ | owner approvals | design docs only |
