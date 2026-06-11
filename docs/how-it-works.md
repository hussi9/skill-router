[← back to skill-router](../README.md) · [Customizing →](./customizing.md) · [Proof →](./proof.md)

# How It Works

> **TL;DR:** Triage → check for saved chain → look up routing-table row → upgrade via catalog check → announce → dispatch with per-step model + thinking depth. ~5 seconds of routing thought before any tool fires.

Every non-trivial task runs through 4 steps before any tool fires.

```
You type a task
     │
     ▼
[1] Triage         BROKEN / BUILD / OPERATE
     │
     ▼
[2] Named chain?   if SKILL.personal.md has a saved chain matching this task → use it
     │ no
     ▼
[3] Routing table  pick Skill + Agent + Model from the table for this path
     │
     ▼
[4] Catalog check  any installed skill more specific than the generic one?
     │
     ▼
Announce → dispatch
```

## 1. Triage

Three questions, in order. The first one that hits picks the path.

| Question | Path | Examples |
|---|---|---|
| Something **broken / wrong / failing**? | BROKEN | error, crash, test fail, "this is wrong" |
| **Create / build / add** something new? | BUILD | new component, new endpoint, new integration |
| Everything else (improve, ship, configure, automate, research) | OPERATE | refactor, deploy, code review, docs |

Ambiguous? Default to the higher-complexity path. Over-routing is cheaper than under-routing — running `systematic-debugging` on a non-bug costs 30 seconds; skipping it on a real bug costs hours.

## 2. Named chain check

If `SKILL.personal.md` declares a `chains:` block, the router checks it before computing fresh. Saved chains exist for two reasons:

- you've typed the same multi-step request 3+ times and want to skip rederivation
- your project has a non-obvious skill order (e.g. `db-expert` before `frontend-design` because schema drives types)

Match logic: substring search, case-insensitive, first match wins. See [customizing.md](./customizing.md).

## 3. Routing table

Each path has a table mapping signals to a `Skill + Agent + Model` triple:

```
| Signal              | Skill                  | Agent           | Model |
| Production incident | systematic-debugging   | general-purpose | opus  |
| Test failing        | test-runner            | test-runner     | sonnet|
| UI component        | frontend-design        | code-architect  | sonnet|
```

Model selection is *part of routing*, not a separate decision. `haiku` for trivial reads, `opus` for production incidents, `sonnet` for everything else.

### How model selection is actually enforced

The parent Claude Code session can't hot-swap models mid-turn — there's no API for "now run this turn at sonnet." So the router uses a simple rule:

- **Step's model matches parent session model** → invoke the Skill in-session (cheaper, same context).
- **Step's model differs from parent** → dispatch via the `Agent` tool with `subagent_type` and `model` set explicitly. The subagent runs at the right model independent of the parent.

For a multi-domain chain, every step that needs a non-parent model goes through `Agent`. The parent does light orchestration only; the heavy work happens at the right model. This typically nets a 30-50% cost reduction on chains with mixed complexity (e.g. `haiku` reads + `sonnet` writes + `opus` review).

Full protocol: [`SKILL.md`](../SKILL.md) "DISPATCH PROTOCOL" section.

## 4. Catalog check

If the table returns a generic skill (e.g. `integration-specialist`), the router searches local + remote catalogs for a more specific match. If you have `stripe-automation` installed, it wins over the generic.

```
~/.claude/skills/         → your custom + installed
~/.agent/skills/          → 1,400+ Antigravity skills
~/.composio-skills/       → 940+ Composio integrations
remote known repos        → 4 curated GitHub catalogs (see references/known-skill-repos.md)
```

This is why people install once and keep it: new skills published tomorrow get used tomorrow, no manual table edits.

## What gets announced

Two shapes — same testable contract. The exact format is mandated in `SKILL.md` → "ANNOUNCEMENT FORMAT". Every line starts with `[skill-router]` so the announcement is greppable from the transcript.

**Single-domain** (one skill, no chain):
```
[skill-router] This is an OPERATE task → superpowers:requesting-code-review → superpowers:code-reviewer.
[skill-router] Model: sonnet  ·  Thinking: think-hard
[skill-router] Invoke now:

▶ superpowers:requesting-code-review  (sonnet, in-session)
```

**Multi-domain** (chain across domains):
```
[skill-router] This touches 3 domains: UI/Frontend, DB, Edge function.
[skill-router] Chain: writing-plans → frontend-design + db-expert → vercel:deploy
[skill-router] Models: sonnet · sonnet+sonnet · sonnet  ·  Thinking: think
[skill-router] Invoke step 1/3 now:

▶ writing-plans  (sonnet, in-session)
▶ frontend-design + db-expert  (sonnet, parallel via Agent)
▶ vercel:deploy  (sonnet, in-session)
```

Operators in chains: `→` sequential (B depends on A), `+` parallel (no shared state).

The announcement fires *before* any tool call. You can grep your transcript for `[skill-router]` and verify what fired matches what was announced. The `▶` lines are the dispatch-mode proof — they tell you which steps ran in-session and which were dispatched via `Agent` with a different model. That's the whole testability story.

## Statusline integration

If you install [`statusline.sh`](../statusline.sh) plus the hook in [`settings-hooks.json`](../settings-hooks.json), the Claude Code status bar surfaces router activity in real time:

```
◆ sonnet · ~/myproject · ⎇ main · 🔀 router · ▶ ship-feature 2/4 · ⚙ frontend-design ✓ · ▓▓░░░░ 18% · $0.04
```

| Segment | Meaning |
|---|---|
| `🔀 router` | skill-router fired in the last 30s (currently routing) |
| `🔀 R5` | skill-router has fired 5 times in this session |
| `▶ ship-feature 2/4` | a chain is mid-flight, on step 2 of 4 |
| `⚙ frontend-design ✓` | last skill that fired; `✓` = upgraded via catalog check |

Source data: `~/.claude/skill_usage.log` (per-skill firings) + `~/.claude/skill_router_log.jsonl` (chain announcements + step progress, written by SKILL.md's dispatch protocol).

## Five design principles

1. **Zero UX.** You never invoke skill-router. It runs as a pre-step.
2. **Deterministic.** Same input → same output. No vibes.
3. **Fail-safe.** Ambiguous → higher-complexity path.
4. **Living.** Catalog check picks up newly-installed skills automatically.
5. **One file.** ~265 lines of routing logic, no build step, no dependencies.

## What it doesn't do

| Doesn't | Why |
|---|---|
| Manage skill lifecycles (create/improve) | That's [zysilm/skill-master](https://github.com/zysilm/skill-master)'s job — different product, complementary |
| Learn from past sessions automatically | Substrate exists (`~/.claude/skill_usage.log`); shipping manual named chains first |
| Provide a UI / dashboard | The statusline integration is the UI |
| Enforce policy across a team | This is a power-user tool, not enterprise governance |
