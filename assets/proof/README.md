# Proof in the Wild — skill-router Multi-Domain Chaining

These are unedited screenshots from real Claude Code sessions, captured 2026-04-27.
They show the routing engine doing the one thing the README claims it does:

> When a request touches multiple domains, the router announces a chain and runs the
> skills in the right order. When a request fits a single path, it picks the one skill
> and goes.

No retries. No prompt scaffolding. Just typing into Claude Code with `skill-router`
loaded as a global skill.

---

## Proof 1 — Multi-domain BUILD task → 4-skill chain

![chain-screenshot](./chain-multi-domain.png)

**Prompt typed:** `lets start teh implementation`

**Router output (verbatim):**

> ● `Skill(skill-router)`
> &nbsp;&nbsp;&nbsp;&nbsp;⎿ Successfully loaded skill
>
> ● This touches **3 domains**: UI/Frontend, DB schema, and a new Edge function.
> Chain: `writing-plans` (we have it) → `superpowers:dispatching-parallel-agents`
> → `frontend-design` + `db-expert`.
>
> Let me read the full current implementation first so agents have complete context.

**Why this matters:**

- The user gave a 4-word, mostly-typo prompt. The router still extracted that the work
  spanned UI + DB + Edge function and announced the exact chain *before* touching code.
- `writing-plans` runs first because the router knows multi-domain BUILD tasks need a
  plan before parallel agents fan out.
- `superpowers:dispatching-parallel-agents` is the orchestration step — not a domain
  skill, a process skill — and the router placed it correctly between planning and
  implementation.
- The two domain skills (`frontend-design`, `db-expert`) are dispatched in parallel,
  not sequentially. That's the dispatch-triple-per-domain pattern, working.

This is what the README's "Multi-domain → announces chain, runs each skill in order"
row in the routing table actually looks like in production.

---

## Proof 2 — Single-domain OPERATE task → direct skill + agent

![review-screenshot](./chain-code-review.png)

**Prompt typed:** `please revewi the entier system again and make sure we have not keps any gaps or issues .. code reveiw`

**Router output (verbatim):**

> ● `Skill(skill-router)`
> &nbsp;&nbsp;&nbsp;&nbsp;⎿ Successfully loaded skill
>
> ● This is an **OPERATE** task → `superpowers:requesting-code-review` →
> `superpowers:code-reviewer` agent.
>
> ● `Skill(superpowers:requesting-code-review)`

**Why this matters:**

- Same router, same install — but for a single-domain ask, no chain. Just the right
  skill and the right agent.
- The router doesn't over-route. It doesn't drag the user through `brainstorming`
  for a code review. It picks `superpowers:requesting-code-review` (the *process*
  skill that defines what good review looks like), then hands off to
  `superpowers:code-reviewer` (the *agent* that actually does it).
- This is the OPERATE path from the routing table — zero ceremony.

---

## How to read the chain output

Every chain follows the same shape:

```
This touches [N] domains: [domain1], [domain2], ...
Chain: [step1] → [step2] → [step3]
```

Or for single-domain:

```
This is a [BROKEN | BUILD | OPERATE] task → [skill] → [agent].
```

That deterministic output is the whole point. No vibes. No "let me think about this."
The router runs, produces a triple (or chain), and execution starts.

---

## Reproduce it

The two screenshots above are not curated. They were the next two times the user
typed something non-trivial into a Claude Code session with `skill-router` installed.
You can see the same behavior in your own session:

```bash
mkdir -p ~/.claude/skills/skill-router
curl -sL https://raw.githubusercontent.com/hussi9/skill-router/main/SKILL.md \
  > ~/.claude/skills/skill-router/SKILL.md
```

Then ask Claude Code to do anything multi-domain ("add a settings page that writes
to the DB and emails the user on save"). The chain announcement is the proof.
