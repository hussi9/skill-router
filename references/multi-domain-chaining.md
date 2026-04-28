# Multi-Domain Chaining

The headline feature most people miss in their first read of `skill-router`.

When a task touches more than one domain, the router doesn't pick a single
skill and hope. It announces a **chain** — a sequence of skills with explicit
dependency operators — *before* any tool fires.

See [`../assets/proof/`](../assets/proof/) for two real-session screenshots
proving this is what actually happens.

---

## The chain syntax

Every chain announcement is one line:

```
This touches [N] domains: [d1], [d2], [d3]
Chain: [step1] → [step2] → [step3a] + [step3b]
```

Two operators, both load-bearing:

| Operator | Meaning | Example |
|----------|---------|---------|
| `→` (arrow) | **Sequential.** Step B runs after step A finishes. B depends on A's output. | `writing-plans → dispatching-parallel-agents` |
| `+` (plus) | **Parallel.** Steps run concurrently. They don't share state. | `frontend-design + db-expert` |

A chain is therefore a small dependency graph encoded in one line.

---

## Detecting "multi-domain"

A task is multi-domain when it spans **two or more** of these:

- **UI/Frontend** — components, pages, layouts, accessibility
- **API/Backend** — endpoints, request handling, server logic
- **Database** — schema, migrations, queries, RLS
- **Edge/Serverless** — Edge Functions, Lambdas, cron jobs, webhooks
- **Auth/Security** — login, permissions, encryption, rate limiting
- **Mobile** — iOS/Android specific, native modules, app store
- **DevOps** — deploy, CI/CD, infra, environment config
- **Data/AI** — ML, embeddings, RAG, vector DBs, agent design
- **3rd-party** — Stripe, Slack, Twilio, Plaid integration glue

Strong signals that a task is multi-domain:

- The verb "implement" or "build" applied to a feature, not a component
- Mention of two or more layers ("settings page that writes to DB and emails")
- An ambiguous prompt like "let's start the implementation" *after* a plan
  was already discussed (router uses prior context)

Single-domain tasks get a single skill, no chain. **Over-chaining hurts as
much as under-chaining.**

---

## The standard chain shapes

### Multi-domain BUILD

```
writing-plans → dispatching-parallel-agents → [domain1 + domain2 + ...]
```

- `writing-plans` first because parallel agents need a plan to coordinate against.
- `dispatching-parallel-agents` is the orchestration step — it's a *process*
  skill, not a domain skill.
- The leaves run in parallel because domains don't depend on each other once
  the plan is set.

### Multi-domain BROKEN (cross-domain bug hunt)

```
systematic-debugging → [domain1-expert + domain2-expert]
```

- `systematic-debugging` produces a hypothesis first.
- Domain experts validate or refute the hypothesis in their layer.

### Multi-domain OPERATE (cross-cutting refactor / migration)

```
brainstorming → writing-plans → dispatching-parallel-agents → [domain1 + ...]
```

- Brainstorming front-loaded because operate-mode multi-domain almost always
  has a "should we even do this?" question.

---

## Single-domain shapes

Just for contrast — the absence of a chain is itself an announcement.

```
This is a BROKEN task → systematic-debugging → general-purpose agent
This is an OPERATE task → requesting-code-review → code-reviewer agent
```

No chain. No `+`. No fan-out. The shape says "one path, one skill."

---

## Why announce instead of just running?

**Three reasons.**

1. **Testable.** You can grep the transcript and verify the actual `Skill`
   tool calls match the announcement. That moves "agent picked the right tool"
   from vibe-check to measurable.
2. **Interruptible.** The user gets one chance to redirect *before* tokens
   are spent. "No, skip the brainstorming" is far cheaper at announcement time
   than 5 minutes in.
3. **Documentable.** The chain is what you screenshot. The chain is what
   goes in the bug report. The chain is the unit of analysis.

See [`../assets/proof/README.md`](../assets/proof/README.md) for what this
looks like in real Claude Code sessions.

---

## Named chains — saved sequences win over computed ones

When `SKILL.personal.md` declares a `chains:` block, the router checks it
**before** computing a chain from the routing table. The match logic is
substring search across the `when:` keywords (case-insensitive). First match
wins.

```yaml
# in SKILL.personal.md
chains:
  - name: ship-feature
    when: ["ship the feature", "lets implement"]
    chain: writing-plans → dispatching-parallel-agents → frontend-design + db-expert
```

When a saved chain wins, the announcement says so explicitly:

```
Using your saved chain `ship-feature`:
  writing-plans → dispatching-parallel-agents → frontend-design + db-expert
```

That keeps the transparency contract — same announcement shape, just with the
provenance prefix.

**Why have named chains at all?** Three reasons:

1. **Repeat workflows.** If you say "ship the feature" 5 times a week, the
   router can stop re-deriving the same chain.
2. **Project quirks.** Maybe in your stack `db-expert` *must* run before
   `frontend-design` because schema drives types. The default chain doesn't
   know this; a saved chain does.
3. **Personal preference.** You may want `superpowers:test-driven-development`
   prepended to every BUILD chain. One named chain expresses that.

Full schema, examples, and design notes: [`named-chains.md`](./named-chains.md).

---

## Common mistakes the router avoids

| Mistake | Why it's wrong | What router does instead |
|---------|----------------|--------------------------|
| Chain *every* task to be safe | Over-routing wastes tokens and feels heavy | Single-domain → one skill, no ceremony |
| Run domain skills in series | Most domains don't depend on each other | `+` for parallel where possible |
| Skip `writing-plans` on multi-domain | Parallel agents diverge without a plan | Always prepend `writing-plans` for multi-domain BUILD |
| Treat `dispatching-parallel-agents` as optional | Without it, "parallel" agents end up sequential | Required orchestrator step in any `+` chain |
| Choose `opus` for every chain step | Burns budget on simple steps | Each step gets its own model via the routing triple |
