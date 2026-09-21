[← back to skill-router](../README.md) · [Customizing →](./customizing.md) · [Proof →](./proof.md) · [v4 design →](./v4-design.md)

# How It Works (v4)

> **TL;DR:** project route → enriched-index rank → small-model tie-break → route card (domain skill first, process skill second, gates, memory) → tiered enforcement → the route follows every sub-agent. Runs as a hook on every prompt: ~50 ms lexical, ~1 s when the model stage is needed.

```
You type a task
     │
     ▼
[0] Project route?   SKILL.personal.md `routes:` — a name that means one project
     │ no            ("@economicalai", "capgo") → that project's skill, its tier, its gates
     ▼
[1] Enriched index   ~/.claude/skill_index.json — every invokable skill with
     │               use_when triggers, keywords, project aliases (build_index.py,
     │               enrichment cached per skill version). index_match.py ranks it.
     ▼
[1a] Jev chooses     jev_choose.py sends EVERY routable index entry to Jev (TypeSafe System One,
     │               jev-1.13.0) as two Choice questions — domain skill, process skill — plus a
     │               path question. No lexical pre-filter: on real prompts the right skill was in
     │               the lexical top 30 only 23 times in 66, because typos defeat token matching.
     │               ≥ 0.8 routes · below that, silent. (0.5–0.8 "Possible fit:" is opt-in:
     │               SKILL_ROUTER_JEV_SUGGEST=1.) When Jev answers, the regex table adds nothing,
     │               and a skill already loaded this session is never carded again.
     │               Slash commands, builtins and agents are never offered. Short prompts
     │               (≤ 15 words) also carry the last 300 chars of the previous assistant turn.
     │               Hook turns only; any failure or 1.2 s timeout falls through to [2].
     ▼
[2] Confident?       margin ≥ 35 % and a distinctive token in name/triggers/keywords
     │ no  ──────►   [2b] Gemini Flash-Lite picks among the top 5 (llm_classify.py, cached)
     ▼
[3] Path + process   v3 regex triage still owns the path (BROKEN/BUILD/OPERATE);
     │               the table supplies the process leg; the index supplied the domain leg
     ▼
[4] Route card       ▶ domain skill · ▶ process skill · gates · memory · tier line
     │
     ├──► pending state (tier) → PreToolUse denies edits only on hard tier;
     │                            Stop asks once on soft tier and logs `soft-skip`
     └──► session route file → SubagentStart brief + PreToolUse(Task) updatedInput
```

## Tiers

| Tier | When | PreToolUse | Stop |
|---|---|---|---|
| hard | BROKEN path; project route with `gates:` or `tier: hard` | denies Edit/Write/Task until `Skill()` runs | blocks until it runs |
| soft | everything else | nothing | blocks **once**: load the skill or answer `[skill-router] skipped X: reason` |

The v3 router was hard everywhere and the learner showed `writing-plans` followed 1 time in 6. Hard enforcement on a skill the model does not value produces workarounds, not compliance. Being wrong on a soft route costs one line.

## The index

`build_index.py` reads the catalog and writes one entry per invokable skill:

```json
{"name": "mac-doctor", "kind": "domain", "owner": "user", "hub": true,
 "use_when": ["mac restarts", "kernel panic", "disk full"], "keywords": ["macos", "pmset", ...],
 "projects": ["macbook", "mac mini", "kernel panic"], "gates": [], "memory": ["airbook_crash_root_cause"],
 "source_hash": "3f1a…"}
```

- `use_when` / `keywords` — lexical from the SKILL.md, plus a one-time small-model pass cached under `~/.claude/skill_router_cache/enrich/` keyed by `source_hash`.
- `projects` / `gates` / `memory` / `hub` — from the `projects:` block in `SKILL.personal.md`. A prompt that names project X boosts X's skills, demotes skills that belong only to project Y, and lets the hub (first-listed) skill win ties.
- `kind` — `process` is a name rule (superpowers:*, refactor, …); the model may promote domain → design/meta, never → process.

Third-party SKILL.md files are never edited. The index is the router's private view.

## Ranking rules that matter

- Fields: name 3.0 · use_when 2.5 · keywords 1.5 · description 1.0; project alias is a flat once-per-skill bonus.
- Synonyms (`restarted → panic, reboot`), phrases (`row level security → rls`), half credit for synonyms.
- A partly-claimed name is docked (`scrollbook` matched, `deploy` did not → `scrollbook-deploy` loses).
- **Evidence gate:** a primary needs a project hit, or a distinctive, non-generic token in a strong field. `flow`, `launch`, `page` alone are never evidence.
- On BROKEN, a confident plugin match with no project evidence is handed to the model stage before it can lead a hard chain.
- Statements are not requests: "the recent refactor broke the auth flow" is silent unless a BROKEN signal fires.

## Sub-agents

Two channels, because one was unreliable:

1. `PreToolUse` on `Task|Agent` → `task_brief.py` appends the parent's route (skill, gates, memory) to the dispatched prompt via `updatedInput`.
2. `SubagentStart` → `subagent_brief.py` names the skills paired to the agent type (`agent_skills.json`, validated against the index; else derived) **and** the parent route.

Enforcement stands down inside sub-agents (`agent_id` present).

## Learning

`learn.py` regenerates `~/.claude/skill_router_learned.json` every session start:
follow rates, keyword→skill triggers, handovers, chains, online candidates, plus (v4)
`soft_skips` per skill and `unused_90d` — user skills with no invocation in 90 days,
the next archive pass.

## Health

```bash
python3 scripts/doctor.py     # hooks, index freshness, model stage, hand-off hook, smoke prompts
bash scripts/check.sh         # syntax · unit tests · 109-prompt calibration (≥95 %) · doctor
```
