---
name: skill-router
description: Use when a [skill-router] route card appears in the turn, when no card appeared on a non-trivial task, or when a route looks wrong. Routes every prompt to the right installed skill, pairs it with a process skill, tiers enforcement, and briefs sub-agents. 79 local skills + plugin skills indexed.
---

# Skill Router (v4)

Routing runs from hooks. A `UserPromptSubmit` hook classifies every prompt
and injects a **route card** before your first action. Your job is to
*follow the card*, not to compute one.

## The route card

```
[skill-router] This is a BROKEN task — 2-step chain.
[skill-router] Chain: mac-doctor → superpowers:systematic-debugging
▶ mac-doctor  (inherit, in-session)
▶ superpowers:systematic-debugging  (inherit, in-session)
[skill-router] Gates before done: simulator screenshot
[skill-router] Memory: airbook_crash_root_cause  (read from ~/.claude/projects/-Users-airbook/memory/)
[skill-router] IRON RULE: call Skill(skill="mac-doctor") before any Edit/Write/Task.
```

- **First `▶` is the domain skill** — one of yours, or a plugin's. Load it first.
- **Second `▶` is the process skill** (debugging, planning, TDD). Load it next.
- **Gates** are completion conditions from the project. Check every one before "done".
- **Memory** names the memory file for this project. Read it before deciding.

## Two tiers

| Line on the card | Meaning |
|---|---|
| `IRON RULE: …` | **hard** — Edit/Write/Task are denied until the skill is loaded. BROKEN path, and project routes with gates. |
| `Soft route: …` | **soft** — nothing is blocked. If you finish without loading it, the Stop hook asks once; answer by loading it or by one line `[skill-router] skipped <skill>: <reason>`. |

Wrong route either way: `python3 ~/.claude/skills/skill-router/scripts/router_override.py "<reason>"`.
The user can also write `[no-router]` in their message.

## How the card is decided

1. **Project route** in `SKILL.personal.md` (`@economicalai`, `capgo`, …) — deterministic.
2. **Enriched index** of every invokable skill (`~/.claude/skill_index.json`): name, "use when" triggers, keywords, project aliases. Lexical rank, ~50 ms.
3. **Small model** (Gemini Flash-Lite, ~1 s, cached) settles low-confidence ties.
4. **Process table** (`references/routing-tables.md`) supplies the second leg.

Questions, discussion and harness noise get no card. Silence is an answer.

## Sub-agents

Every `Agent(...)` dispatch gets the parent's route appended to its prompt
(skill, gates, memory), and `SubagentStart` briefs the agent with the skills
paired to its type. Enforcement never reaches inside a sub-agent.

## When there is no card

Non-trivial task, no `[skill-router]` line? Run the fallback yourself:
`python3 ~/.claude/skills/skill-router/scripts/router.py <<< "the prompt"`.
If that is silent too, routing may be dead: `python3 ~/.claude/skills/skill-router/scripts/doctor.py`.

## Maintenance

| Command | When |
|---|---|
| `scripts/doctor.py` | routing feels dead, or after a Claude Code upgrade |
| `scripts/build_index.py --enrich` | you installed or edited a skill |
| `scripts/index_match.py --all "<prompt>"` | a route looked wrong — see the ranking |
| `scripts/learn.py --show` | what has been learned |
| `scripts/check.sh` | after editing any routing logic |

Project routes, projects, gates: `SKILL.personal.md`. Full tables and protocols: `references/routing-tables.md`.
