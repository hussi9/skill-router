# Changelog

All notable changes to skill-router. Newest first.

## Unreleased

### Added
- **Self-improvement loop** — `scripts/weekly-analysis.sh` orchestrates all three analysis scripts in one command (`learn-from-history.py` → `audit-dispatch.py` → `learn-chains.py`). Run manually or automate via the included launchd/crontab template. Appends a timestamped digest to `~/.claude/skill_router_weekly.log`.
- **`setup/launchd-weekly.plist`** — macOS launchd template. Substitute `{{SKILL_ROUTER_PATH}}` and `{{HOME}}` with one `sed` command, copy to `~/Library/LaunchAgents/`, load with `launchctl`. Fires every Monday at 9am. Equivalent Linux crontab line documented in `docs/self-improvement.md`.
- **`docs/self-improvement.md`** — complete guide to the feedback loop: what each script measures, what to do when numbers are bad, how named chain auto-promotion works, full macOS + Linux setup instructions, and a 7-question FAQ covering "no log entries", "chain not firing", log file locations, and safe run frequency.
- **`learn-chains.py --apply` clarified** — the `--apply` flag appends proposals with placeholder `when:` keywords to `SKILL.personal.md`. The doc now explicitly states you must edit the `when:` values before they fire — the script fills in steps and models automatically but can't know your phrasing.

### Fixed
- **Ghost-skill deadlock** — routing table entries that referenced non-existent skills (`system-design`, `typescript-expert`, `mobile-developer`, etc.) caused a permanent iron-rule deadlock: the hook blocked all edits until the skill ran, but `Skill()` failed because the skill doesn't exist. Fixed by replacing every ghost entry with the nearest installed skill, and adding belt-and-suspenders: the `PreToolUse` hook now auto-clears state if `remaining[0]` resolves to a ghost, so no deadlock survives even if one slips through.
- **Parallel-chain `Stop` hook deadlock** — multi-domain BUILD chains wrote all steps (including parallel agent-dispatched ones) to `remaining`. Since agent-dispatched steps never call `Skill()` in the parent session, the `Stop` hook blocked turn end forever. Fixed: `remaining` now only tracks in-session Skill() calls; parallel fan-out steps are in `all` but not `remaining`.
- **Framework compliance errors routed as BUILD** — prompts like "Suspense violations" or "static generation failed" were classified BUILD (new feature) instead of BROKEN (fix needed). Added `violations`, `failed`, and `suspense`/`static-render` patterns to `BROKEN_RE` so they route to `superpowers:systematic-debugging`.
- **`feature-dev:feature-dev` flagged as ghost skill** — the `feature-dev` plugin uses a `commands/` layout instead of `skills/`, so the catalog scanner missed it. Extended `_skill_catalog()` to also scan `plugins/cache/.../commands/*.md`.

### Added
- **Strict `[skill-router]` announcement format** — `SKILL.md` now mandates a verbatim template: every line is `[skill-router]`-prefixed, includes `Models:` / `Thinking:` summaries, and ends with per-step `▶` markers (`in-session`, `via Agent`, `parallel via Agent`). The format is greppable from the transcript; the audit script reads structured `chain-step` JSONL events (the `▶` lines are human-readable proof, not the script's source).
- **Format docs propagated** to `references/multi-domain-chaining.md`, `references/named-chains.md`, `references/dispatch-protocol.md`, and `docs/how-it-works.md`. Single source of truth lives in `SKILL.md` → "ANNOUNCEMENT FORMAT".
- **Line count update** — `SKILL.md` grew from 218 → 266 lines (+22%) to absorb the verbatim format templates. README, `docs/README.md`, and `docs/how-it-works.md` updated to `~265` to keep the claim honest.
- **`scripts/learn-chains.py`** — addresses the "named-chain-as-corpus" gap. Reads `~/.claude/skill_router_log.jsonl`, finds chains the router has re-derived 3+ times, proposes named-chain entries you can paste (or `--apply`) into `SKILL.personal.md`. The first concrete step toward learned routing.
- **`scripts/audit-dispatch.py`** — addresses the "Dispatch Protocol is instruction not enforcement" gap. Scores recent chains: of every chain that was announced, how many had complete per-step dispatch logged? Compliance score with verdict (healthy / partial / broken).
- **`references/dispatch-protocol.md`** — full event schema + skip-pattern catalog. Detail moved out of SKILL.md.

### Changed
- **SKILL.md trimmed 298 → 218 lines** (-27%). Detail-heavy DISPATCH PROTOCOL, NAMED CHAIN LOOKUP, CATALOG CHECK, THINKING DEPTH sections are now ~10-line summaries that reference the deeper protocol docs in `references/`. Easier for the router itself to follow consistently.

### Added (earlier in this release)
- **README user-friendliness pass:**
  - Stronger one-line benefit at the top (vs feature-first opening)
  - Statusline preview in README so people see the visible value before installing
  - Common Questions section answering 7 first-timer questions (load failures, disable, claude.ai support, custom skills, logging)
  - Project section linking CHANGELOG + CONTRIBUTING + LICENSE
  - Inlined `intellectronica/agent-skills` into the "Works with" table
- **Doc breadcrumbs** — every `docs/*.md` now has a top-line back-to-README link plus sibling navigation
- **`docs/how-it-works.md` TL;DR** — one-sentence summary of the 4-step pipeline at the top
- **CONTRIBUTING.md** with file-map, what-we-want / what-we-don't-want, PR checklist
- **GitHub Actions lint workflow** (`.github/workflows/lint.yml`) — validates SKILL.md frontmatter, statusline runs cleanly, no planning docs at root, known-skill-repos has canonical entries, CHANGELOG has Unreleased section, Claude + Codex flavors have parity on core sections
- **Codex flavor lane table** now includes default Reasoning + Thinking columns matching Claude flavor
- **Statusline `✦saved` badge** when an active chain came from a saved-chain match
- **Thinking-depth column** in routing tables: `none / think / think-hard / ultrathink`. Router pre-pends the keyword to dispatch prompts for steps that need extended thinking.
- **`references/thinking-depth.md`** — full rules + community-skill alternatives (intellectronica/agent-skills `ultrathink`, etc).
- **`intellectronica/agent-skills`** + **`wasabeef/claude-code-cookbook`** added to `references/known-skill-repos.md` so catalog check can find their `ultrathink` and `think-hard` skills.
- **Statusline `🧠 ultra/hard/think`** indicator when an extended-thinking step is in flight.
- **Per-step model + thinking resolution** for saved chains documented in `references/named-chains.md` (lookup-from-routing-table behavior, `chain.model` global override, `steps[].model` per-step override).
- **`AGENTS.md` named-chains support** in the Codex flavor — synced with Claude flavor's named-chain semantics.
- **Codex DISPATCH PROTOCOL** — Codex flavor now also supports per-step model enforcement.

### Fixed
- **Statusline `extra` field mismatch** — removed dead `\textra` parsing on `skill_usage.log` (no hook ever wrote that field). Catalog-upgrade ✓ marker now sourced from `skill_router_log.jsonl` instead.
- **f-string escaping bug** in statusline Python that broke router segments when invoked via bash double-quoted heredoc.
- **`settings-hooks.json` documentation** — hook now has comments explaining which log file is written by the hook vs by the router itself.

## v1.1 (2026-04-28) — `b8d5d93`

### Added
- **DISPATCH PROTOCOL** in `SKILL.md`: chain steps that need a different model than the parent session are now launched via the `Agent` tool with `model:` set explicitly. The Model column is now enforced, not advisory.
- **`~/.claude/skill_router_log.jsonl`** — router writes structured events (chain-start, chain-step, chain-end) for the statusline to consume.
- **Statusline router segments** — `🔀 router` (active in last 30s), `🔀 R<N>` (session count), `▶ <chain> <step>/<of>` (live chain progress).

## v1.0 (2026-04-28) — `9740052`

### Added
- **Doc rewire** matching canonical OSS pattern (`anthropics/skills`-style): tight README, `docs/` for deep content, `references/` for runtime-loaded protocol docs only.
- **`docs/how-it-works.md`** — single end-to-end design + value doc replaces the sprawling earlier ARCHITECTURE.md.
- **`docs/customizing.md`** — overrides + named chains.
- **`docs/proof.md`** — verbatim chain announcements from real sessions.
- **README dropped from 335 → 63 lines.** Total doc surface 1306 → 629 lines, no overlap.

### Removed
- 5 internal planning docs at the repo root (CODEX_ADAPTATION_PLAN, IMPLEMENTATION_PLAN, PRODUCT_POSITIONING, ROUTING_CONTRACT, skill-router-core, the old ARCHITECTURE.md) → moved to `.archive/`.

## Earlier

- **`4486098`** — Real-session proof PNGs added to `assets/proof/`.
- **`4e1ab72`** — Repo renamed `skills-master` → `skill-router`. Named chains feature shipped (saved sequences in `SKILL.personal.md` win over computed). New references docs (catalog-check, multi-domain-chaining, named-chains, known-skill-repos).
- **`e43ee39`** — Go-live hardening, audit blockers fixed.
- **`90cc25a`** — Repo cleanup, archived internals.
- **`fb923dd`** — Initial release.

## v3 — routing that is actually connected (2026-09-05)

The router had been inert for months. Everything around it looked healthy: unit
tests green, docs accurate, calibration claiming 100%. Two independent faults,
either of which alone produces total silence.

### Fixed — why nothing was routing

- **Hooks were absent from `settings.json`.** The engine had no caller. They
  survived only in a `.bak` from June. `scripts/install_hooks.py` now installs
  them by merging beside other tools' hooks (Sentigent, gstack, the formatter)
  rather than replacing arrays, and is idempotent.
- **Demotions never expired.** Two reasoned overrides ever removed a skill from
  routing permanently. `systematic-debugging`, `writing-plans`, `brainstorming`,
  `requesting-code-review` and `frontend-design` had all been silently killed —
  the entire BROKEN path and most of BUILD. Tallies are now timestamped and
  expire after `DEFER_TTL_DAYS`; untimestamped legacy entries are ignored.
- **`test-runner` was announced as a skill.** It is a sub-agent. `Skill(skill=
  "test-runner")` fails, and the IRON RULE then blocks every edit waiting for a
  call that cannot succeed. Sub-agents are no longer in the skill catalog;
  `valid_agent()` checks them separately.
- **The `PostToolUse` chain read stdin four times.** Four shell hooks on the
  same matcher each began with `cat`; only the first saw the payload, so a
  successful invoke never cleared a demotion. One process now does all four
  jobs.
- **The test suite read live user state**, so a demotion recorded during real
  work changed what the tests asserted. Hermetic now — which is how a green
  suite coexisted with a dead router.

### Changed — efficiency

- **Model column is `inherit`.** It used to name `sonnet`/`opus`, written when
  the parent was always Sonnet. On a Fable or Opus session the dispatch
  protocol read that as "fan out to a sub-agent" and shipped every routed step
  to a weaker model. Depth now comes from `thinking`, which composes with any
  model.
- **The IRON RULE block is 4 lines, down from 9.** It is injected on every
  routed turn, so each line is a permanent context tax.

### Added — using all 395 skills instead of 20

- **`build_catalog.py`** inventories every invokable skill: user, project,
  plugin (newest version only), slash-commands, and the 17 built-ins that live
  nowhere on disk and so could never be routed to. Descriptions are synthesized
  for files without frontmatter. Rebuilds every `SessionStart`.
- **`catalog_match.py`** ranks the catalog lexically per prompt and appends one
  advisory `Specialist available:` line. Pure stdlib, ~5ms, no daemon — the
  embedder it replaces had been dead for months, taking the semantic layer with
  it. Three gates keep it silent when unsure.
- **Project routes** in `SKILL.personal.md`, parsed and enforced. Generic triage
  cannot tell "ship the next one" (a YouTube short) from "submit the build"
  (Scrollbook); a name that means exactly one project can.
- **Sub-agent routing.** A `SubagentStart` hook briefs every dispatched agent on
  the skills that fit its job, from `agent_skills.json` or derived from the
  agent's own description. Verified live: a dispatched agent quoted the brief
  back. The documented `skills:` frontmatter preload was tested and did not
  work on 2.1.263.
- **IRON RULE stands down inside sub-agents.** The parent's pending skill
  belongs to the parent's turn; enforcing it inside an agent blocked every edit
  with no way to satisfy it.
- **`doctor.py`** — six checks, end to end. This outage would have shown as one
  failing line.
- **`check.sh`** — the whole gate locally, no metered CI.

### Accuracy

| | before | after |
|---|---|---|
| Path accuracy (109 prompts) | 67.0% | 100% |
| Skill accuracy | 43.3% | 100% |
| Unit + hook tests | 16 failing | 104 passing |

## v3.1 — it learns, and the learning lives outside the repo (2026-09-07)

Routing was generic: it knew what kind of work a prompt was and which
installed skill was about that domain, and nothing about how this user
actually works. Everything personal now accumulates in
`~/.claude/skill_router_learned.json`, regenerated at every session start by
`scripts/learn.py`, never committed, never hand-edited. The earlier path that
appended learned chains into `SKILL.personal.md` is retired.

### Added

- **Structured signals.** The router logs a `prompt` event (keywords only,
  never text) with `session_id` + `prompt_id`; the Skill hook logs an `invoke`
  event with the same ids. The join says "these words led to that skill",
  which is the one thing the router could never learn from its own
  announcements.
- **Triggers** — keyword → skill, advisory, consulted only when the table and
  the matcher are both silent. Support ≥ 3, precision ≥ 0.6.
- **Handovers** — what you run after what, within a session. Delivered as a
  `PostToolUse` nudge after a Skill call. Verified to reach the model on this
  Claude Code version by injecting a token and having the model quote it
  back. Fires only for habits ≥ 50% with n ≥ 3.
- **Chains** — recurring 3–4 step flows, shown at announcement time as "your
  usual flow from here".
- **Discovery, installed** — the catalog is diffed against the last run; a new
  skill is announced at the next session start, once.
- **Discovery, online** — the four known catalogs (now 2,400 entries) are
  refetched weekly in the background and ranked against the user's own recent
  prompt keywords. Nothing is suggested until 20 prompts of signal exist.
- **Session brief** — a foreground `SessionStart` hook prints at most three
  lines about what changed. Silent otherwise; runs on startup/resume only.
- `learn.py --compact` drops the dead embedder's neighbour dumps: 4.0 MB →
  248 KB, 2,474 events that informed nothing.
- `doctor.py` check 8: the overlay is being regenerated.

### Fixed

- **The router was being taught by its own test suite.** Every test run,
  doctor smoke prompt and manual probe wrote a chain-start event no Skill
  call would follow; the learner read those as announcements the user
  ignored and drove systematic-debugging's follow rate to zero. Logging is
  hook-mode only now, and the learner ignores unstamped announcements from
  the structured era.
- Sub-agent briefs filter hand-set pairings against what is installed, so an
  archived skill is never named to an agent that cannot ask what happened.
- A missing integration specialist (`connect-apps` archived) degrades to the
  generic plan step instead of silencing the whole prompt.
- Online suggestions dedupe by bare name, so `superpowers:*` is not offered
  back as an install; packaging words are excluded from the interest profile.
- Tests are hermetic against the overlay. One expected an embedder rescue
  that the user's real follow rates now refuse.
