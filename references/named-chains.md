# Named Chains

Named chains let you save a preferred dispatch sequence and have the router
use it whenever a matching task comes through. They're the persistence layer
for what the router already does — chains exist either way; named chains just
remember the ones you like.

This doc covers schema, matching, conflict rules, and the design rationale
for *not* shipping a slash command.

---

## Schema

Add a `chains:` block at the top of `SKILL.personal.md`:

```yaml
chains:
  - name: ship-feature
    when:
      - "ship the feature"
      - "lets implement"
      - "start the implementation"
    chain: writing-plans → dispatching-parallel-agents → frontend-design + db-expert

  - name: production-incident
    when:
      - "500 errors in prod"
      - "production is down"
      - "users can't log in"
    chain: superpowers:systematic-debugging → security
    model: opus
    agent: general-purpose

  - name: weekly-cleanup
    when:
      - "weekly cleanup"
      - "tidy up the repo"
    chain: refactor → docs → superpowers:requesting-code-review
```

**Required fields:** `name`, `when`, `chain`.
**Optional fields:** `model` (default: per-step from routing table), `agent`
(default: per-step from routing table).

---

## Matching

The router applies these rules to the user's prompt, in order:

1. **Lowercase normalize** the user's prompt and each `when:` entry.
2. **Substring match** — for each `when:` line, check if it appears as a
   substring of the prompt.
3. **First match wins.** Order in the file is the priority.
4. **No match?** Fall through to the routing table as before.

**Why substring not regex?** Substring is predictable and 99% of the value.
Regex invites debugging and edge-case rabbitholes for a feature whose whole
purpose is to be boring and reliable.

**Why first match?** It mirrors how `case` statements feel in code. If two
chains match, the user wanted the more specific one earlier in the file.

---

## Announcement

A saved chain announces with explicit provenance:

```
Using your saved chain `ship-feature`:
  writing-plans → dispatching-parallel-agents → frontend-design + db-expert
```

Compared to a computed chain:

```
This touches 3 domains: UI/Frontend, DB schema, Edge function.
Chain: writing-plans → dispatching-parallel-agents → frontend-design + db-expert
```

Same shape after the colon. The only difference is the source — and the
source is announced so the user can verify what's happening.

---

## When to add a chain

| Situation | Add a chain? |
|-----------|--------------|
| You've typed the same multi-step request 3+ times | Yes |
| Your project has a non-obvious skill order (e.g. schema-before-UI) | Yes |
| You want a process skill prepended to every BUILD (e.g. always TDD) | Yes |
| The default routing already nails this task | No — duplicate maintenance |
| One-off task that won't repeat | No — table handles it |
| You want to *remove* a step from the default chain | No — use SKILL.personal.md's per-row override instead |

---

## Conflict rules

| Conflict | Resolution |
|----------|------------|
| Saved chain matches AND routing table has a row | Saved chain wins |
| Two saved chains match | First in file order wins |
| Saved chain references a skill that isn't installed | Catalog check runs to install or fall back |
| Saved chain has `model: opus` but task is trivial | Saved chain's model wins (you opted in) |

---

## Why no slash command

`/skill-chain` was considered and rejected. Three reasons:

1. **Zero-UX is a feature.** The router has never required user invocation.
   Adding a slash command for editing chains makes the router something users
   *think about* — that loses the "install once, forget" property.
2. **One file edit beats two slash commands + a state model.** `SKILL.personal.md`
   is already the customization surface. Naming chains there mirrors how
   personal overrides already work.
3. **Storage and runtime are different concerns.** A slash command would
   couple them: invocation by name. Named chains via file decouple them: the
   router still chooses based on the prompt, the file just provides preferred
   answers.

The tradeoff: users have to know to edit the file. That's the same tradeoff
as `SKILL.personal.md` itself, and the README install step covers it.

---

## Future extension — learned chains

The hook in `~/.claude/settings.json` already logs every `Skill` invocation
to `~/.claude/skill_usage.log`. Extending the log to also capture the
*announced chain* + *prompt prefix* gives you a corpus.

A future variant of the router could grep the log for similar prompts and
suggest:

```
This looks like work you've done before. Suggest using the same chain?
  writing-plans → frontend-design + db-expert
[y/n]
```

If the user says yes 3 times in a row, the router could write the chain back
into `SKILL.personal.md` automatically.

That's the bridge from manual named chains to learned chains. The manual
layer is what we ship first — once it's used, the data shape for the learned
layer is obvious.

---

## Examples in the wild

### Example 1 — Web + mobile parity team

```yaml
chains:
  - name: feature-parity
    when:
      - "add this feature"
      - "implement"
    chain: writing-plans → dispatching-parallel-agents → frontend-design + mobile-developer
```

Their team rule: every feature ships web + mobile. The chain enforces it.

### Example 2 — Compliance-driven shop

```yaml
chains:
  - name: any-auth-change
    when:
      - "auth"
      - "login"
      - "permission"
    chain: brainstorming → security → superpowers:requesting-code-review
    model: opus
```

Auth changes always get the security skill *and* mandatory review. The chain
prevents anyone from skipping it.

### Example 3 — Solo founder shipping daily

```yaml
chains:
  - name: daily-ship
    when:
      - "ship it"
      - "deploy"
    chain: superpowers:verification-before-completion → vercel:deploy → superpowers:finishing-a-development-branch
```

One chain captures their ship workflow. Every time they say "ship it," the
same three steps run in the same order.
