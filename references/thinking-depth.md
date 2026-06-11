# Thinking Depth — Runtime Reference

> Loaded by `SKILL.md` when dispatching a routed step.
> User-facing context: see [`../docs/how-it-works.md`](../docs/how-it-works.md).

## Four levels

| Value | Pre-pend | Approx token budget | Use for |
|-------|----------|---------------------|---------|
| `none` | (nothing) | 0 thinking tokens | trivial, mechanical, single-file work |
| `think` | `think.` | small | multi-file refactor, schema, perf investigation |
| `think-hard` | `think hard.` | medium | security review, AI/RAG design, ambiguous bug |
| `ultrathink` | `ultrathink.` | large | production incident, auth design, architecture call |

## Activation

Pre-pend the keyword as the **literal first word** of the dispatch prompt:

```
ultrathink.

Use Skill: superpowers:systematic-debugging
Task: production /api/checkout 500s, last 10min, payment failures spiking
Context: <recent error logs, deploy history, etc.>
```

Claude's harness parses the leading keyword to allocate thinking budget.
Paraphrases ("really think", "deeply analyze") don't work — they read as
regular instructions.

## Community-skill alternative

If a more specific skill exists (auto-discovered via the catalog check), use
it INSTEAD of the keyword. Known options:

| Skill | Source | Replaces |
|-------|--------|----------|
| `ultrathink` | [intellectronica/agent-skills](https://github.com/intellectronica/agent-skills/blob/main/skills/ultrathink/SKILL.md) | `ultrathink.` keyword |
| `ultrathink` | [wasabeef/claude-code-cookbook](https://github.com/wasabeef/claude-code-cookbook) | `ultrathink.` keyword |
| `think-hard` | [IDev4life/debug2ai](https://github.com/IDev4life/debug2ai) | `think hard.` keyword |
| `deep-research` | (already in `~/.agent/skills/`) | research-heavy ultrathink tasks |

Skills layer in extra structure (e.g. break the problem down, list
3 alternatives, predict failure modes) that the bare keyword doesn't.

## Cost guardrail

Thinking tokens cost real money. Don't ultrathink everything:

- `none` → default
- escalate only on rows the routing table marks
- saved chains (`SKILL.personal.md`) can override per chain via `thinking:`

Bad pattern: `ultrathink` on a one-line bug fix. Good pattern: `ultrathink`
on the auth-rewrite scope decision before you spend 4 hours implementing
the wrong thing.

## Statusline indicator

When a step with `thinking != none` is dispatching, the statusline shows:

```
… · 🧠 ultra · …    (ultrathink in flight)
… · 🧠 hard · …     (think-hard in flight)
… · 🧠 think · …    (think in flight)
```

Source: the router writes a `thinking-active` event to
`~/.claude/skill_router_log.jsonl` when it dispatches and clears it on step
completion.
