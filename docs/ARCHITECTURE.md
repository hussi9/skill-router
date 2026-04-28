# skill-router — Architecture & Value

This is the end-to-end design doc. If you've read the README and want to
understand *why* skill-router is shaped the way it is, this is for you.

```
                   What you type
                        │
                        ▼
              ┌─────────────────────┐
              │  3-question triage  │   BROKEN / BUILD / OPERATE
              └─────────────────────┘
                        │
                        ▼
              ┌─────────────────────┐
              │  Named chain check  │   SKILL.personal.md
              │  (saved sequences)  │
              └─────────────────────┘
                        │
              hit ──────┴────── miss
              │                  │
              ▼                  ▼
      ┌──────────────┐   ┌─────────────────────┐
      │  Use saved   │   │  Compute from table │
      │   chain      │   │  + catalog check    │
      └──────────────┘   └─────────────────────┘
              │                  │
              └────────┬─────────┘
                       ▼
              ┌─────────────────────┐
              │  Announce chain     │   Skill + Agent + Model
              └─────────────────────┘
                       │
                       ▼
              ┌─────────────────────┐
              │  Dispatch           │   Run skills in order
              └─────────────────────┘
```

---

## The problem skill-router solves

Claude Code (and Codex, and any agent harness with skills) lets you install
hundreds of skills. The agent picks one for each task. Two failure modes:

1. **Wrong skill.** It picks `brainstorming` when something is broken.
   It picks `system-design` when there's a specialist skill installed.
   It picks `opus` for a trivial file read.
2. **Skipped skill.** It rationalizes "this is simple enough" and bypasses
   the skill discipline entirely. Hours of debugging follow.

Both come from the same root cause: **the agent is making the routing
decision implicitly, every time, from scratch.** No system, no determinism,
no audit trail.

skill-router replaces the implicit decision with an explicit one.

---

## Five design principles (and what each one buys you)

### 1. Zero UX

The user never invokes skill-router. They type the task they always typed.
The router runs as a pre-step before the agent acts.

**Buys you:** install once, forget. No slash commands to memorize. No new
mental model. The router becomes infrastructure, not a tool.

### 2. Deterministic

Same input → same output. No vibes-based "let me think about this." A
3-question triage maps every task to one of three paths. Tables map paths to
dispatch triples.

**Buys you:** testability. You can run the same prompt twice and get the same
chain. You can grep the transcript and verify what fired matches what was
announced. "Did the agent pick the right tool?" goes from vibe-check to
measurement.

### 3. Fail-safe

Ambiguous tasks default to the higher-complexity path. Over-routing is bad,
but under-routing is worse — skipping `systematic-debugging` on a real bug
costs hours; running it on a non-bug costs 30 seconds.

**Buys you:** safety margin. You're never punished for the router being
*more* careful than needed.

### 4. Living

The catalog check runs on every non-trivial task. Local skills first
(`~/.agent/skills`, `~/.claude/skills`, `~/.composio-skills`), then known
remote repos (Anthropic, K-Dense-AI, ComposioHQ, Antigravity), then broader
GitHub search, then generate via `superpowers:writing-skills`.

**Buys you:** the long tail. Install a new skill today, the router uses it
tomorrow. You never have to manually update routing tables to discover what's
already installed.

### 5. One file (with optional layers)

The core fits in `SKILL.md` — ~80 lines of routing logic. No build step, no
config, no dependencies. Optional layers stack on top:

- `SKILL.personal.md` — your project-specific routing + named chains
- `references/*.md` — deeper protocol docs the router consults
- `statusline.sh` + the hook — visibility into what's running

**Buys you:** auditability and customizability without complexity. You can
read every line of skill-router in 5 minutes. You can override anything in
your personal file. Nothing is magic.

---

## How the pieces fit together

### Layer 1 — Triage (the 3-question decision tree)

```
Q1: Is something BROKEN / WRONG / FAILING?  → BROKEN PATH
Q2: Is this CREATE / BUILD / ADD something new? → BUILD PATH
Q3: Everything else (improve, ship, configure, automate, research) → OPERATE PATH
AMBIGUOUS? → default to higher-complexity path
```

These three questions are deliberately coarse. Fine-grained routing happens
in the table; the triage just picks which table to use. Coarse-first means
the path decision rarely changes even when the table changes.

### Layer 2 — Named chain lookup (saved before computed)

The router reads `SKILL.personal.md`'s `chains:` block. If any `when:`
keyword matches the prompt, the saved chain wins. Otherwise, fall through.

This is the layer that lets users encode their preferences without learning
a new tool. See [`references/named-chains.md`](../references/named-chains.md).

### Layer 3 — Routing table (the per-path map)

Each path (BROKEN/BUILD/OPERATE) has a table mapping signals to dispatch
triples:

```
| Signal              | Skill             | Agent           | Model  |
| Production incident | systematic-debugging | general-purpose | opus  |
| Test failing        | test-runner          | test-runner     | sonnet |
```

The table is intentionally human-readable. You can fork the router and edit
this table in 30 seconds.

### Layer 4 — Catalog check (the long-tail layer)

If the routing table returns a generic skill (e.g. `integration-specialist`),
the catalog check looks for a more specific match in installed skill
catalogs. If `stripe-automation` is installed, it wins over the generic.

Full protocol: [`references/catalog-check.md`](../references/catalog-check.md).
Curated repo list: [`references/known-skill-repos.md`](../references/known-skill-repos.md).

### Layer 5 — Multi-domain chaining (when one skill isn't enough)

If a task touches 2+ domains (UI + DB + Edge function), the router announces
a chain rather than picking one skill. Chains use two operators:

- `→` sequential (B depends on A)
- `+` parallel (no shared state)

```
This touches 3 domains: UI/Frontend, DB schema, Edge function.
Chain: writing-plans → dispatching-parallel-agents → frontend-design + db-expert
```

Full chain syntax + standard shapes:
[`references/multi-domain-chaining.md`](../references/multi-domain-chaining.md).

### Layer 6 — Announcement (the testable contract)

Whatever the router decides, it states it out loud *before* any tool fires.
This is the testable artifact — you can grep the transcript and verify the
actual `Skill` calls match the announcement.

The announcement format is one of:

```
This is a [BROKEN | BUILD | OPERATE] task → <skill> → <agent>.
This touches [N] domains: <d1>, <d2>. Chain: <step1> → <step2> + <step3>.
Using your saved chain `<name>`: <chain>.
```

### Layer 7 — Dispatch (run the skills)

The router invokes the first step via the `Skill` tool. Sequential steps
chain naturally; parallel steps go through `superpowers:dispatching-parallel-agents`.

---

## The data flow in a typical session

```
1. User types: "lets ship the feature"
2. SessionStart: skill-router's SKILL.md is loaded (auto)
3. Triage: matches Q2 → BUILD PATH
4. Named chain lookup: matches `ship-feature` in SKILL.personal.md → use saved
5. Announce:
   Using your saved chain `ship-feature`:
     writing-plans → dispatching-parallel-agents → frontend-design + db-expert
6. Invoke: Skill(writing-plans) — produces plan
7. Invoke: Skill(superpowers:dispatching-parallel-agents)
8. The orchestration step launches frontend-design + db-expert in parallel
9. Hook logs each Skill invocation to ~/.claude/skill_usage.log
10. Statusline shows current skill in the Claude Code UI
```

Every step is observable. Every step is testable. Every step happened
because of an explicit rule, not a vibe.

---

## What it's worth — measured

`run_routing_test.sh` runs 20 real-world prompts through the `claude -p` CLI
and scores the routing.

| Dimension | Score |
|-----------|-------|
| Overall (path + skill + model) | 90% (18/20) |
| Path routing | 95% (19/20) |
| Skill selection | 95% (19/20) |
| Model selection | 95% (19/20) |
| Skill tool actually fires correctly | 88% (7/8) |

The 2 misses share one root cause: auth-adjacent task wording incorrectly
triggering the "auth → opus" escalation. Fixable with a tighter signal — the
test harness existing means the fix is verifiable.

For proof in real sessions, see [`assets/proof/README.md`](../assets/proof/README.md)
— two unedited screenshots showing the chain announcement firing on
multi-domain BUILD and single-domain OPERATE tasks.

---

## What it costs

| Dimension | Cost |
|-----------|------|
| Install | One curl command |
| Per-task latency | ~5 seconds of routing thought before the agent acts |
| State | None (router is stateless; named chains and the usage log are user-controlled) |
| Lock-in | Zero — delete `SKILL.md` and the agent goes back to baseline behavior |

---

## What it doesn't do (and why)

| Doesn't do | Why |
|------------|-----|
| Manage skill lifecycles (create/improve/review) | That's [zysilm/skill-master](https://github.com/zysilm/skill-master)'s job — different product, complementary |
| Learn from past sessions automatically | Substrate exists (`skill_usage.log`); shipping manual named chains first to avoid premature ML |
| Provide a UI / dashboard | Statusline integration is the UI |
| Enforce policy | This is a power-user tool, not enterprise governance — see [Sentigent](https://github.com/hussi9/sentigent) for that |
| Work without skills installed | Router has nothing to route to. Install superpowers + Antigravity + Composio first |

---

## File map

| Path | What it does |
|------|--------------|
| `SKILL.md` | Routing engine — the universal core. Auto-loaded by Claude Code. |
| `SKILL.personal.md` | Per-user overrides + named chains |
| `statusline.sh` | Status bar showing active skill + cost + context |
| `settings-hooks.json` | Hook snippet for logging skill usage |
| `run_routing_test.sh` | Test harness — runs 20 prompts through `claude -p` |
| `references/known-skill-repos.md` | Curated catalog of skill sources |
| `references/catalog-check.md` | Catalog-check protocol with validation gates |
| `references/multi-domain-chaining.md` | Chain syntax + standard shapes |
| `references/named-chains.md` | Schema, matching, and design rationale for named chains |
| `assets/proof/` | Screenshots of the chain announcement in real sessions |
| `codex-skill/skill-router/` | Codex flavor of the router |
| `templates/AGENTS.codex.template.md` | Drop-in template for Codex repos |
| `scripts/scan_codex_inventory.py` | Inventory tool for Codex environments |

---

## Versioning and stability

The router is intentionally simple enough that there's no version skew problem
— you can fork the SKILL.md, customize it, and never pull updates if you
don't want to. The CSS-cascade design means the personal layer is forward-
compatible with core changes by default.

The reference docs are stable extensions of the routing logic. They don't
add new features the core doesn't enable; they document protocols the core
already follows.

---

## Reading order

If you're new to the repo:

1. [`README.md`](../README.md) — pitch + install
2. This doc — design + value
3. [`SKILL.md`](../SKILL.md) — the actual routing logic
4. [`assets/proof/README.md`](../assets/proof/README.md) — what it looks like in real sessions
5. [`references/multi-domain-chaining.md`](../references/multi-domain-chaining.md) — chain syntax deep-dive
6. [`references/named-chains.md`](../references/named-chains.md) — saved-sequences feature
7. [`references/catalog-check.md`](../references/catalog-check.md) + [`known-skill-repos.md`](../references/known-skill-repos.md) — long-tail discovery

If you want to customize:

1. Read [`SKILL.personal.md`](../SKILL.personal.md)
2. Add a `chains:` block for your top 3 repeated workflows
3. Add a project section for any signal the core doesn't cover
4. That's it — your changes layer on top of the core, no fork needed
