[← back to skill-router](../README.md) · [Customizing →](./customizing.md) · [Proof →](./proof.md)

# How It Works

> **TL;DR:** Project route → triage → routing table → rank the full catalog for a specialist → announce → dispatch. Runs as a hook on every prompt, in about 120ms.

```
You type a task
     │
     ▼
[0] Project route?  a trigger in SKILL.personal.md that means exactly one
     │ no           project ("@economicalai", "testflight") → use its skill
     ▼
[1] Triage          BROKEN / BUILD / OPERATE — or SKIP, which is silence
     │
     ▼
[2] Routing table   Skill + Agent + Thinking for this path
     │
     ▼
[3] Specialist      rank all ~395 invokable skills against the prompt;
     │              append one advisory line when one clearly fits
     ▼
Announce → dispatch → (sub-agents get their own brief at SubagentStart)
```

**Before anything else, check it is alive.** Routing runs from hooks in
`settings.json`. If those go missing — a botched upgrade, a hand-edit, a
restored backup — every part of this pipeline still passes its own tests while
routing does nothing at all. That happened here for roughly three months.

```bash
python3 scripts/doctor.py
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

### The model column is `inherit`

The parent session can't hot-swap models mid-turn, so "which model" and
"in-session or dispatched" are the same decision:

- **`inherit`** → invoke the Skill in-session, at whatever model you chose.
- **anything else** → dispatch via `Agent` with `model` set explicitly.

The table used to name `sonnet` on most rows. That was written when the parent
was always Sonnet, so `sonnet` quietly meant `inherit`. It stopped meaning
that: on a Fable or Opus session every routed step read as "different model"
and got fanned out to a sub-agent running something *weaker* than the model you
are paying for — dispatch overhead purchased at a quality discount.

Depth is expressed by `Thinking` instead, which composes with any model.
`haiku` survives as the one deliberate downgrade, for bulk read-only scans
whose output is a list of file paths rather than a judgment.

Full protocol: [`SKILL.md`](../SKILL.md) "DISPATCH PROTOCOL" section.

## 3. Specialist layer

The tables name ~20 process skills. This machine has ~395 invokable ones. The
other ~375 are the domain specialists that make a task go faster, and no table
can enumerate them — so the router ranks the whole catalog against the prompt
and appends at most one advisory line.

```
~/.claude/skills/        invokable — your own
~/.claude/commands/      invokable — slash-commands
plugins/cache/           invokable — newest version of each plugin
(built into the binary)  invokable — code-review, dataviz, security-review, …
~/.agent/skills/         NOT invokable — install candidates only
~/.composio-skills/      NOT invokable — install candidates only
```

That last distinction matters: announcing a skill the `Skill` tool cannot load
deadlocks the IRON RULE on a call that can never succeed, so non-invokable
skills are catalogued but never routed to.

Ranking is BM25-style over names and descriptions, pure stdlib, about 5ms. It
replaced a local embedding daemon that needed fastembed, ONNX, an 80-second
corpus build and a live Unix socket — and that had been dead for months,
taking the entire semantic layer down with it silently.

The catalog rebuilds at every `SessionStart`, so a skill you install today is
routable today.

## 4. Sub-agents

Routing used to stop at the session boundary: the parent got an announcement,
and a dispatched agent got nothing — so the half of the work actually editing
files ran skill-blind. A `SubagentStart` hook now briefs each agent on the
skills that fit its job, hand-paired in `agent_skills.json` or derived by
ranking the agent's own description.

Enforcement deliberately does *not* follow. The parent's pending skill belongs
to the parent's turn and a sub-agent cannot satisfy it, so the IRON RULE stands
down when it sees an `agent_id`.

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
5. **No dependencies.** Pure stdlib, no build step, no daemon. Every added
   moving part is another thing that can die quietly.
6. **Observable.** `doctor.py` answers "is this actually running?" — the
   question every other check assumed.

## What it doesn't do

| Doesn't | Why |
|---|---|
| Manage skill lifecycles (create/improve) | That's [zysilm/skill-master](https://github.com/zysilm/skill-master)'s job — different product, complementary |
| Learn from past sessions automatically | Substrate exists (`~/.claude/skill_usage.log`); `learn-from-history.py` reports, a human decides |
| Provide a UI / dashboard | The statusline integration is the UI |
| Enforce policy across a team | This is a power-user tool, not enterprise governance |
