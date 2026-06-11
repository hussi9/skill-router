# Named Chains — Runtime Reference

> Loaded by `SKILL.md` when checking `SKILL.personal.md` for saved chains.
> User-facing guide: see [`../docs/customizing.md`](../docs/customizing.md).

## Schema

In `SKILL.personal.md`:

```yaml
chains:
  - name: <short-label>           # required
    when:                          # required, list of substrings
      - "<keyword phrase 1>"
      - "<keyword phrase 2>"
    chain: <step1> → <step2> + <step3>   # required
    model: <haiku | sonnet | opus>       # optional, overrides per-step model
    agent: <agent-id>                    # optional, overrides per-step agent
```

## Match algorithm

```
1. Lowercase normalize the user prompt and every `when:` entry.
2. For each chain in file order:
     for each `when:` substring:
       if substring appears in prompt → MATCH, use this chain
3. No match → fall through to the routing table.
```

**First match wins.** File order is priority order.
**Substring, not regex.** Predictable, no edge cases.

## Per-step model + thinking resolution

A saved chain only declares the **skill sequence**:

```yaml
chain: writing-plans → frontend-design + db-expert → superpowers:code-reviewer
```

It does *not* declare per-step models or thinking depth. Resolution:

```
For each step in the saved chain:
  1. Look up the skill in the routing tables (BROKEN / BUILD / OPERATE)
  2. Use the Model + Thinking from that row
  3. If chain.model is set globally → override every step's model
  4. If chain.thinking is set globally → override every step's thinking
  5. If chain.steps[].model is set per-step → that wins (most specific)
```

So the resolved chain above (assuming defaults from the routing tables) becomes:

| Step | Skill | Model | Thinking |
|------|-------|-------|----------|
| 1 | `writing-plans` | sonnet | none |
| 2 | `frontend-design` (parallel with 3) | sonnet | none |
| 3 | `db-expert` (parallel with 2) | sonnet | think |
| 4 | `superpowers:code-reviewer` | sonnet | think-hard |

If you want to force everything to opus:

```yaml
chains:
  - name: critical-auth
    when: ["auth migration"]
    chain: brainstorming → security
    model: opus
    thinking: ultrathink   # applies to every step
```

If you want different per-step:

```yaml
chains:
  - name: ship-with-review
    when: ["lets ship"]
    steps:
      - skill: writing-plans
        model: sonnet
      - skill: frontend-design + db-expert   # parallel, both sonnet from table
      - skill: superpowers:code-reviewer
        model: opus
        thinking: ultrathink
```

The compact `chain: a → b + c` form is preferred when defaults are fine; the
explicit `steps:` form is for when a saved chain needs non-default models or
thinking.

## Conflict rules

| Conflict | Resolution |
|---|---|
| Saved chain matches AND routing table has a row | Saved chain wins |
| Two saved chains match | First in file order wins |
| Saved chain references a skill that isn't installed | Catalog check runs to install or fall back |
| Saved chain has `model: opus` but task is trivial | Saved chain's model wins (you opted in) |

## Announcement

When a saved chain wins, output the saved-chain announcement format from
`SKILL.md` → "ANNOUNCEMENT FORMAT". The `[skill-router]` prefix is mandatory.

```
[skill-router] Using your saved chain `<name>`: <s1> → <s2> + <s3>
[skill-router] Models: <m1> · <m2>+<m3>  ·  Thinking: <max-thinking>
[skill-router] Dispatching step 1/<N>...

▶ <s1>  (<m1>, <in-session | via Agent>)
▶ <s2> + <s3>  (<m2>, parallel via Agent)
```

Same shape as a computed chain — just with `Using your saved chain \`<name>\``
in place of the domain-count line, so the saved-vs-computed provenance is
greppable.

## Logging hook (for future learned-chains)

The hook in `~/.claude/settings.json` writes every `Skill` invocation to `~/.claude/skill_usage.log`. Extending it to also capture *announced chains* + *prompt prefix* gives the corpus for auto-suggesting chains the user repeats. Not shipped yet — manual layer first.
