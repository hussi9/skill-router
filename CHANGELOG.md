# Changelog

All notable changes to skill-router. Newest first.

## Unreleased

### Added
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
