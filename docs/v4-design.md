# skill-router v4 — design

Date: 2026-09-07. Status: implemented 2026-09-07 (see CHANGELOG v4.0). Evidence gathered from `~/.claude/skill_usage.log`
(Apr 18 → Sep 7, 570 invocations), `~/.claude/skill_router_log.jsonl` (2,022 events),
`~/.claude/skill_router_learned.json`, ten realistic probe prompts, and the code.

## 1. What is wrong today

| # | Finding | Evidence |
|---|---|---|
| 1 | **Routing is silent on most real prompts.** Triage is regex and defaults to SKIP. | 4 of 10 realistic probes produced nothing ("linkedin post", "macbook restarted", "review the design of…", "vitest suite fails"). |
| 2 | **When it speaks, it names process skills, not your skills.** The routing table knows ~20 superpowers/process skills. Your 77 domain skills reach the model only through one BM25 "advisory" line. | Top routed skills ever: refactor 173, systematic-debugging 101, writing-plans 57. Specialist line fired 2/10 probes, one of them wrong (`vercel:release` for a DeenUnlock crash). |
| 3 | **The model ignores the announcement.** Hard-blocking Edit/Write on a process skill it does not value produces overrides in spirit (work around it), not compliance. | `writing-plans`: announced 6, followed 1 (follow_rate 0.167). 0 formal overrides logged ever. |
| 4 | **Project routes are static substrings and drift.** | `briefing` routed a Supabase RLS task to `claude-author`. Routes `seo-work`, `paid-ads`, `marketing`, `youtube-thumbnail` point at archived skills (doctor FAIL). |
| 5 | **Native discovery is weak too.** Claude picks skills from descriptions; most describe *what* not *when*. | e.g. `ads`: "Multi-platform paid advertising audit…"; superpowers guidance says descriptions must be "Use when…" triggers. |
| 6 | **The router was dead for two months and nobody noticed.** | Log: 0 events Jul–Aug 2026. |
| 7 | **SKILL.md is heavy.** 2,388 words loaded on every `Skill(skill-router)` call, 80 times in 5 months. | `wc -w` |
| 8 | **Sub-agent briefs are half-wired.** Pairings point at archived skills; derivation is BM25 on the agent description; the parent's route never reaches the child. | `agent_skills.json`: cmo→market, qa-lead→qa, optimizer→core-web-vitals, tech-lead→plan-eng-review, product-manager→spec, integration-specialist→connect-apps (all archived now). |
| 9 | **The learner has nothing to learn from.** | `triggers: 0`, `online: 0`; follow rates null for 44/47 skills. |

## 2. Design goals

1. Every non-trivial prompt gets a route, and the route names **your domain skill first**, the process skill second.
2. Routing recall comes from an **enriched skill index** (triggers, keywords, project names, synonyms), not hand-written regex.
3. Enforcement is proportional: **soft nudge by default, hard gate only where being wrong is expensive** (BROKEN path, project routes with completion gates).
4. **Sub-agents inherit the route.** The parent's route card reaches every dispatched agent, and agents get skills matched to their job from the same index.
5. The loop closes: skills used without an announcement become trigger candidates; announced-and-ignored skills get demoted.
6. Cheap to run: lexical stage < 10 ms; optional LLM stage only on low confidence, cached, Haiku, ~1–2 s, < $0.001.

## 3. Architecture

```
prompt ──► [A] project routes (routes.yaml, generated + curated)
             │ miss
             ▼
           [B] lexical rank over skill_index.json (BM25 + field weights + synonyms)
             │ confident?  ──yes──► route card
             │ no
             ▼
           [C] Haiku classifier (optional, cached): picks among top-5 + path
             ▼
           route card ──► announce (UserPromptSubmit)
                      ├─► pending state (soft/hard) ──► PreToolUse / Stop
                      └─► session route file ──► SubagentStart brief
                                                 PreToolUse(Task) updatedInput
```

### A. `skill_index.json` — the enriched index (new)

Built at SessionStart (incremental, content-hash cached). One entry per invokable skill:

```json
{"name":"mac-doctor","kind":"domain","owner":"user",
 "use_when":["mac restarts","kernel panic","disk full","slow boot","launchd audit"],
 "keywords":["macos","pmset","panic","diagnose","restart","reboot","disk"],
 "projects":[], "pairs_with":["superpowers:systematic-debugging"],
 "agents":[], "gates":[], "source_hash":"…"}
```

`use_when`/`keywords` come from a one-time Haiku pass over each SKILL.md (first 3 KB), cached by hash so the bill grows only when a skill changes. Third-party SKILL.md files are never edited; the index is the router's private view. User-owned skills get a report of weak descriptions and, on approval, a rewritten `description:` in the "Use when…" form so Claude's own discovery improves too.

### B. Lexical ranker (rewrite of `catalog_match.py`)

BM25 over `name`, `use_when`, `keywords`, `projects`, `description`, with synonym expansion (crash≈panic≈restart, ship≈deploy≈release, post≈article≈linkedin). Confidence = margin between top-1 and top-2 plus absolute score. Returns `{path, primary[], process, agent, confidence}`.

### C. Haiku classifier (new, optional)

Fires only when confidence is low and the prompt is > 5 words. Prompt: the user text + the top-5 candidates' `use_when` lines. Returns JSON `{path, skills[≤2], reason}`. Cached in `~/.claude/skill_router_cache/<sha1>.json`. Uses `ANTHROPIC_API_KEY` from Doppler (already present) with `claude-haiku-4-5`; falls back to lexical if the key or network is missing. Hook timeout raised 5 → 12 s for UserPromptSubmit only.

### D. Route card (replaces the current announcement)

```
[skill-router] mac-doctor  (your skill · BROKEN · think)
[skill-router] then: superpowers:systematic-debugging  ·  agent: none
[skill-router] gates: none   ·   memory: airbook_crash_root_cause
[skill-router] Invoke: Skill(skill="mac-doctor")   — soft: finish without it and the Stop hook will ask why
```

Domain skill first, process skill second, memory file named when one matches the project (from `MEMORY.md` index). Under 6 lines.

### E. Enforcement tiers

| Tier | When | Behaviour |
|---|---|---|
| hard | project route with `gates:`; BROKEN path | PreToolUse denies Edit/Write until the skill loads (today's behaviour) |
| soft | everything else | No PreToolUse block. Stop hook blocks once with "you never loaded X — load it or say why", which also records the override reason to the learner |
| silent | questions, discussion, harness noise | nothing |

### F. Sub-agent extension

1. `SubagentStart`: brief = pairing from `agent_skills.json` (validated at build time against the index) or index-derived match on the agent description **plus** the parent's current route card (read from `~/.claude/skill_router_session/<session_id>.json`). ≤ 4 lines.
2. `PreToolUse` on `Task`/`Agent`: append one line to the dispatched prompt via `hookSpecificOutput.updatedInput` — "Load Skill(skill=…) before editing" — so even generic agents get the route. Falls back to no-op if `updatedInput` is unsupported by the running Claude Code build.
3. `agent_skills.json` regenerated: every entry checked against the index, archived targets dropped, a suggestion emitted for each agent with no valid pairing.

### G. Learning loop

- Skill invoked with no announcement in that turn → prompt keywords become a trigger candidate; 3 occurrences promote to `routes.yaml` (marked `learned:`).
- Announced and not invoked twice in 14 days → demoted for that path; never for hard-tier.
- Weekly `learn.py` also emits "skills never used in 90 days" for the next archive pass.

### H. Slim `SKILL.md`

< 400 words: what the announcement means, the escape hatch, the override, where the tables live (`references/`). The routing tables move out of the hot path because the announcement now carries them.

## 4. Deliverables and order

1. Repair: drop routes/pairings to archived skills, doctor green, `ads`/`market` archived. (done in this session)
2. `build_index.py` + `skill_index.json` (lexical fields only) and new ranker; probes pass ≥ 8/10.
3. Route card + enforcement tiers + session route file.
4. Sub-agent brief v2 + Task `updatedInput` injection.
5. Optional Haiku stage behind `SKILL_ROUTER_LLM=1`.
6. Index enrichment pass (Haiku) and description report for user-owned skills.
7. Learner changes, slim SKILL.md, tests, docs, `check.sh` green, commit.

Each stage has a failing test first (`tests/test_router.py` probe set = the ten prompts above plus five from `skill_usage.log`).
