# Customizing

Two layers, both in `SKILL.personal.md`. CSS-cascade model: your rules win over the universal core.

## 1. Named chains (saved sequences)

Add a `chains:` block. The router checks it *before* computing a chain from the routing table. If any `when:` keyword matches your prompt, the saved chain wins.

```yaml
chains:
  - name: ship-feature
    when:
      - "ship the feature"
      - "lets implement"
    chain: writing-plans → dispatching-parallel-agents → frontend-design + db-expert

  - name: production-incident
    when:
      - "500 errors in prod"
      - "production is down"
    chain: superpowers:systematic-debugging → security
    model: opus
```

**Match rules:** substring, case-insensitive, first match wins.
**Operators:** `→` sequential, `+` parallel.

When a saved chain wins, the announcement says so:

```
Using your saved chain `ship-feature`:
  writing-plans → dispatching-parallel-agents → frontend-design + db-expert
```

**Add a chain when** you've typed the same multi-step request 3+ times, or your project has a non-obvious skill order. **Skip it when** the default already nails the task.

Full schema and edge cases: [`references/named-chains.md`](../references/named-chains.md).

## 2. Project-specific routing rules

Below the chains, add per-project signals to the routing tables:

```markdown
### MyApp — Backend (Node/Postgres)

| Signal | Skill | Agent | Model |
|---|---|---|---|
| gRPC service change | system-design → test | integration-specialist | sonnet |
| Rate limiting / quota | system-design → app-security | security-auditor | sonnet |

### Guardrails

Prisma schema touched?    → run prisma generate after changes
Redis touched?            → check TTL logic before merge
>10 DB queries in a file? → db-expert review

### Completion gates

BACKEND:
  □ Migration file written (never raw ALTER)
  □ Prisma types regenerated
  □ Integration tests pass
```

These layer on top of the universal rules. The universal core handles 90% of tasks; you only add things it doesn't cover.

## Install the personal layer

```bash
curl -sL https://raw.githubusercontent.com/hussi9/skill-router/main/SKILL.personal.md \
  > ~/.claude/skills/skill-router/SKILL.personal.md
```

Then edit it with your chains and project rules.

## Optional — statusline integration

See which skill is active in the Claude Code status bar:

```
◆ sonnet · ~/myproject · ⎇ main · ⚙ systematic-debugging · ▓▓░░░░░░░░ 20% · $0.03
```

```bash
curl -sL https://raw.githubusercontent.com/hussi9/skill-router/main/statusline.sh \
  > ~/.claude/statusline.sh
chmod +x ~/.claude/statusline.sh
```

Then merge the hook from [`settings-hooks.json`](../settings-hooks.json) into your `~/.claude/settings.json`.

The hook also writes every `Skill` invocation to `~/.claude/skill_usage.log` — useful corpus for adding the right named chains later:

```bash
sort ~/.claude/skill_usage.log | uniq -c -f1 | sort -rn | head
```
