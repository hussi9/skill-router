---
name: skill-router-personal
description: Personal routing overrides. Layers on top of skill-router-core. Copy this file to ~/.claude/skills/skill-router/SKILL.personal.md and customize.
---

# Skill Router — Personal Overrides Template

**How this works:**
- The universal core (SKILL.md) runs first and handles 90% of tasks
- This file adds project-specific routing that overrides or extends the core
- Your rules always win over the core rules (CSS cascade model)

---

## HOW TO USE

1. Copy to `~/.claude/skills/skill-router/SKILL.personal.md`
2. Replace the example sections below with your own projects
3. Add rows to the routing tables for signals specific to your work
4. The core SKILL.md loads first — only add things the core doesn't cover

---

## NAMED CHAINS — Save Your Preferred Sequences

The router checks this block *before* computing a fresh chain from the routing
table. If a `when:` signal matches your task, the saved chain wins.

**Schema:**

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

  - name: weekly-cleanup
    when:
      - "weekly cleanup"
      - "tidy up the repo"
    chain: refactor → docs → superpowers:requesting-code-review
```

**Operators:** `→` sequential (B depends on A), `+` parallel (no shared state).

**Match rules:** any `when:` line is a substring match (case-insensitive)
against the user's prompt. First match wins. If no match, the router falls
through to the regular routing table.

**When to add a chain:**
- You've typed the same multi-step request 3+ times
- You have a recurring workflow with a specific skill order that beats the default
- A project has a non-obvious sequence (e.g. always run `db-expert` *before* `frontend-design` because schema drives types)

**When NOT to add a chain:**
- The default routing already nails it — adding a duplicate just adds maintenance
- The chain only applies once — the table handles one-offs fine

Full design + examples: see [`references/named-chains.md`](./references/named-chains.md).

---

## PERSONAL OVERRIDES — Add Your Projects Here

### [Your Project Name]

| Signal | Skill | Agent | Model |
|--------|-------|-------|-------|
| Replace with your signal | replace-with-skill | general-purpose | sonnet |

### Multi-Platform Rule (if you work across web + mobile)

```
ANY feature touching [your app]:
  → BOTH platforms must be implemented before "done"
  → Web (apps/web) + Mobile (apps/mobile)
```

---

## EXECUTION GUARDRAILS — Add Your Own

```
[Add rules that apply automatically to your specific stack]

Example:
  Supabase touched?   → run supabase gen types after schema changes
  iOS feature?        → test on simulator before "done"
  Payment flow?       → security-auditor review before merge
```

---

## COMPLETION GATES — Extended

```
[Add domain-specific gates for your stack]

Example:
  DATABASE:
    □ Migration written and applied
    □ Types regenerated

  MOBILE:
    □ Web done
    □ Mobile done
    □ Both tested
```

---

## EXAMPLE — What A Real Personal Override Looks Like

```markdown
### MyApp — Backend (Node/Postgres)

| Signal | Skill | Agent | Model |
|--------|-------|-------|-------|
| gRPC service change | system-design → test | integration-specialist | sonnet |
| Rate limiting / quota | system-design → app-security | security-auditor | sonnet |
| Board metrics review | board-review | general-purpose | opus |

### Guardrails
  Prisma schema touched?    → run prisma generate after changes
  Redis touched?            → check TTL logic before merge
  >10 DB queries in a file? → db-expert review

### Completion Gates
  BACKEND:
    □ Migration file written (never raw ALTER)
    □ Prisma types regenerated
    □ Integration tests pass
```
