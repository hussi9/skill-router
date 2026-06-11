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
