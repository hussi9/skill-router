# skill-router

[![GitHub stars](https://img.shields.io/github/stars/hussi9/skill-router?style=social)](https://github.com/hussi9/skill-router/stargazers) [![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![CI](https://github.com/hussi9/skill-router/workflows/lint/badge.svg)](https://github.com/hussi9/skill-router/actions)

**Your skill first, the process skill second, gates and memory on the card — before any tool fires.**

A hook-driven router for Claude Code. It indexes every skill you have installed by *when it applies*, ranks them against each prompt in ~80 ms, asks a small model only when the ranking is unsure, and injects a route card before Claude's first action. Enforcement is tiered: hard where a wrong skill is expensive, soft everywhere else. The route follows every sub-agent you dispatch.

```
> my macbook restarted again last night, can you check why
[skill-router] This is a BROKEN task — 2-step chain.
[skill-router] Chain: mac-doctor → superpowers:systematic-debugging
[skill-router] Invoke step 1/2 now:
▶ mac-doctor  (inherit, in-session)
▶ superpowers:systematic-debugging  (inherit, in-session)
[skill-router] Memory: airbook_crash_root_cause  (read from ~/.claude/projects/.../memory/)
[skill-router] IRON RULE: call Skill(skill="mac-doctor") before any Edit/Write/Task.
```

Every line is `[skill-router]`-prefixed, so `grep '\[skill-router\]'` on a transcript shows exactly what was routed.

## Why (v4, September 2026)

An audit of this machine after five months of use:

| Finding | Number |
|---|---|
| Skills installed / ever invoked / invoked last month | 291 / 80 / 32 |
| Tokens the skill listing cost every session start | ~22,600 |
| Skills the v3 routing table could name | ~20 (all process skills) |
| Real prompts the v3 router answered with silence | 4 of 10 |
| `writing-plans` announced / followed | 6 / 1 |
| Months routing was dead without anyone noticing | 2 |

The routing table could never enumerate the domain skills — they change weekly — and hard-blocking edits on a process skill the model does not value produced workarounds, not compliance. v4 replaces the table with an index, and replaces uniform enforcement with tiers. Full evidence and design: [docs/v4-design.md](./docs/v4-design.md).

## How it decides

```
prompt ──► project route (SKILL.personal.md)          deterministic, wins outright
       ──► Jev over the whole index (jev_choose.py)    ~0.4 s, two Choice questions, no pre-filter;
                                                      ≥ 0.8 route · below: silent (0.5–0.8 suggestion line is opt-in)
       ──► on Jev failure / 1.2 s timeout:
             enriched index rank (skill_index.json)     ~80 ms, name · use_when · keywords · project aliases
             small-model tie-break (Gemini Flash-Lite)  only below 35 % margin, ~1 s, cached
       ──► path (BROKEN / BUILD / OPERATE) + process leg from the table
       ──► route card: domain skill · process skill · gates · memory · tier
```

- **Index.** `build_index.py` reads every invokable SKILL.md, extracts "use when" triggers and keywords, and a one-time small-model pass adds more — cached per file hash, so the bill grows only when a skill changes. Third-party skill files are never edited.
- **Projects.** A `projects:` block maps names to skills, memory files and completion gates. "push deenunlock to testflight" no longer lands on the scrollbook deploy skill because both ship to TestFlight.
- **Precision.** A winner needs real evidence: a project hit, or a distinctive non-generic token in its name, triggers or keywords. "flow" alone never picks `ux-flow`.
- **Tiers.** Hard (edit block until the skill loads) on the BROKEN path and on project routes with gates. Soft elsewhere: the Stop hook asks once, and the answer teaches the router.
- **Sub-agents.** A `PreToolUse` hook on `Task` appends the parent's route to every dispatched prompt; `SubagentStart` briefs the agent with its paired skills.

Pipeline detail: [docs/how-it-works.md](./docs/how-it-works.md).

## Install

```bash
git clone https://github.com/hussi9/skill-router ~/devpro/skill-router
ln -s ~/devpro/skill-router ~/.claude/skills/skill-router
python3 ~/.claude/skills/skill-router/scripts/install_hooks.py     # wires 6 hook events into ~/.claude/settings.json
python3 ~/.claude/skills/skill-router/scripts/build_catalog.py
python3 ~/.claude/skills/skill-router/scripts/build_index.py --enrich
```

Optional, for the small-model stage: put `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY`) in the environment, or in Doppler (`shared/prd`) and let `scripts/refresh_env.py` cache it. Without a key the router is lexical-only and still routes.

Restart Claude Code. The catalog, index and learner rebuild in the background at every session start.

## Verify

```bash
python3 ~/.claude/skills/skill-router/scripts/doctor.py
```

Twelve checks: hooks wired, scripts present, no route names an uninstalled skill, catalog and index fresh, model stage status, sub-agent hand-off hook, no expired demotions, agents follow the session model, learned overlay fresh, smoke prompts routed. Then type any non-trivial task in a new session and look for `[skill-router]` before the first tool call.

A route looked wrong? See the ranking and why:

```bash
python3 ~/.claude/skills/skill-router/scripts/index_match.py --all "the prompt"
```

## Measured

`tests/calibration.py`, 109 curated prompts, lexical stage only (model stage off for determinism):

| | v3 (2026-09-07 morning) | v4 |
|---|---|---|
| Path accuracy | 95.4 % | **99.1 %** |
| Skill accuracy (when path correct) | 64.2 % | **100 %** |
| Silent on 10 real prompts from one week | 4 | **0** |
| Lexical stage, end to end | — | 80 ms |
| `SKILL.md` loaded per invocation | 2,388 words | 483 words |

The calibration set is curated and encodes the author's expectations; read 99.1 % as "the regressions are gone", not as a field rate. The field number is the follow rate the learner computes over live sessions, baseline 17 % on `writing-plans` under v3.

## Customize

- **Project routes and projects** — `SKILL.personal.md`: `routes:` (name → skill, tier, gates) and `projects:` (aliases, skills, memory files, gates). Prove a route fires: `python3 scripts/router.py <<< "your prompt"`.
- **Agent pairings** — `agent_skills.json`, validated against the index at brief time.
- **Synonyms and phrases** — `scripts/index_match.py` (`SYNONYMS`, `PHRASES`, `GENERIC`).
- **Escape hatches** — the user writes `[no-router]`; the model runs `scripts/router_override.py "<reason>"`.

[docs/customizing.md](./docs/customizing.md) has the long form.

## It learns

`scripts/learn.py` regenerates `~/.claude/skill_router_learned.json` every session start — never the repo, never your personal file:

| Learned | Surfaces as |
|---|---|
| Follow rates (announced vs invoked, joined by prompt id) | skills you routinely ignore stop leading routes |
| Keyword → skill triggers | `Learned from your history: prompts with testflight usually use scrollbook-deploy` |
| Handovers and chains | `After writing-plans you usually run test-driven-development` |
| Soft-skips per skill (v4) | demotion signal for soft routes |
| Skills unused for 90 days (v4) | the next archive pass |

Prompt *keywords* are logged, never prompt text. `SKILL_ROUTER_NO_LEARN=1` logs nothing. Details: [docs/self-improvement.md](./docs/self-improvement.md).

## Common questions

**Will this slow Claude Code down?** The lexical stage is ~80 ms. The model stage fires on low-confidence prompts only, ~1 s, cached by prompt hash. Hook timeout is 12 s so a slow network degrades to lexical rather than to a blocked prompt.

**Why Gemini inside a Claude tool?** `claude -p` inside a hook re-enters every hook and MCP server on the machine — measured at 3.5 minutes. The classification is ~60 tokens. Provider order is Anthropic Haiku → Gemini Flash-Lite; whichever key works first answers.

**Can I turn it off?** For one message, write `[no-router]`. For good, `python3 scripts/install_hooks.py --remove`.

**What about my custom skills?** They are exactly what v4 is for. Anything under `~/.claude/skills/`, `~/.claude/commands/`, project `.claude/skills/`, and every installed plugin is indexed. Non-invokable catalogs (`~/.agent/skills`, `~/.composio-skills`) are install candidates only.

**Where does it log?** `~/.claude/skill_usage.log` (every Skill call) and `~/.claude/skill_router_log.jsonl` (announcements, prompts as keywords, invocations, soft-skips). Both feed the learner; both are hook-mode only, so tests and probes never teach the router.

## Documentation

| Doc | What you'll learn |
|---|---|
| [docs/v4-design.md](./docs/v4-design.md) | The audit that motivated v4, the design, the deliverables |
| [docs/how-it-works.md](./docs/how-it-works.md) | The pipeline, ranking rules, tiers, sub-agent hand-off |
| [docs/customizing.md](./docs/customizing.md) | Personal routes, projects, named chains |
| [docs/self-improvement.md](./docs/self-improvement.md) | What the learner computes and how it surfaces |
| [references/routing-tables.md](./references/routing-tables.md) | The process tables, announcement format, dispatch protocol |
| [docs/proof.md](./docs/proof.md) | Real-session screenshots |

## Works with

| Source | Skills | How the router uses it |
|---|---|---|
| [superpowers](https://github.com/obra/superpowers) | process discipline | the process leg of every route |
| Claude Code plugins (`~/.claude/plugins`) | frontend-design, supabase, vercel, … | indexed, invokable |
| your `~/.claude/skills/` | whatever you build | indexed, invokable, project-aware |
| [Antigravity](https://github.com/sickn33/antigravity-awesome-skills), [Composio](https://github.com/ComposioHQ) | 2,300+ | install candidates only |

## Project

- [CHANGELOG.md](./CHANGELOG.md) — version history
- [CONTRIBUTING.md](./CONTRIBUTING.md) — how to propose changes
- [LICENSE](./LICENSE) — MIT
- `bash scripts/check.sh` — syntax · unit tests · calibration gate (≥ 95 %) · doctor
