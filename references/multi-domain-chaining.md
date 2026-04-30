# Multi-Domain Chaining — Runtime Reference

> Loaded by `SKILL.md` when the router needs to compute a chain.
> User-facing explanation: see [`../docs/how-it-works.md`](../docs/how-it-works.md).

## Chain syntax

| Operator | Meaning |
|---|---|
| `→` | Sequential. B runs after A and may depend on A's output. |
| `+`  | Parallel. Steps run concurrently and don't share state. |

A chain is a one-line dependency graph:

```
writing-plans → dispatching-parallel-agents → frontend-design + db-expert
```

→ plan first, then dispatch, then frontend-design and db-expert run in parallel.

## Multi-domain detection

Treat a task as multi-domain when it spans **two or more** of:

| Domain | Signals |
|---|---|
| UI / Frontend | component, page, layout, accessibility, mobile screen |
| API / Backend | endpoint, request handler, server logic |
| Database | schema, migration, query, RLS |
| Edge / Serverless | edge function, lambda, cron, webhook |
| Auth / Security | login, permissions, encryption, rate limit |
| Mobile native | iOS, Android, native module, app store |
| DevOps | deploy, CI/CD, infra, env config |
| Data / AI | ML, embeddings, RAG, vector DB, agent design |
| 3rd-party | Stripe, Slack, Twilio, Plaid integration |

## Standard chain shapes

| Path | Standard chain |
|---|---|
| Multi-domain BUILD | `writing-plans → dispatching-parallel-agents → [domain1 + domain2 + ...]` |
| Multi-domain BROKEN | `systematic-debugging → [domain1-expert + domain2-expert]` |
| Multi-domain OPERATE | `brainstorming → writing-plans → dispatching-parallel-agents → [domain1 + ...]` |
| Single-domain (any path) | `[skill] → [agent]` (no chain operators) |

## Named chains override computed chains

If `SKILL.personal.md` declares a `chains:` block and any `when:` keyword matches the prompt, the saved chain wins. See [`named-chains.md`](./named-chains.md).

## Announcement format

The exact format is mandated by `SKILL.md` → "ANNOUNCEMENT FORMAT" section.
Output verbatim, substitute only `<vars>`. The `[skill-router]` prefix on
every line is the testable contract — users grep their transcript for it.

Single-domain:
```
[skill-router] This is a <BROKEN|BUILD|OPERATE> task → <skill> → <agent>.
[skill-router] Model: <model>  ·  Thinking: <thinking>
[skill-router] Invoke now:

▶ <skill>  (<model>, <in-session | via Agent>)
```

Multi-domain (computed):
```
[skill-router] This touches <N> domains: <d1>, <d2>, <d3>.
[skill-router] Chain: <s1> → <s2> + <s3>
[skill-router] Models: <m1> · <m2>+<m3>  ·  Thinking: <max-thinking>
[skill-router] Invoke step 1/<N> now:

▶ <s1>  (<m1>, <in-session | via Agent>)
▶ <s2> + <s3>  (<m2>, parallel via Agent)
```

Multi-domain (saved):
```
[skill-router] Using your saved chain `<name>`: <s1> → <s2> + <s3>
[skill-router] Models: <m1> · <m2>+<m3>  ·  Thinking: <max-thinking>
[skill-router] Invoke step 1/<N> now:

▶ <s1>  (<m1>, <in-session | via Agent>)
▶ <s2> + <s3>  (<m2>, parallel via Agent)
```

Per-step completion:
```
[skill-router] Step <n>/<N> done.
[skill-router] Invoke step <n+1>/<N> now:
```

Chain completion:
```
[skill-router] Chain done.
```

Rules:
- `Models:` separator: `·` between sequential steps, `+` inside one parallel step.
- `Thinking:` is the highest depth across all steps (`none | think | think-hard | ultrathink`). Omit the field entirely when every step is `none`.
- Each `▶` line ends with `in-session`, `via Agent`, or `parallel via Agent` — matching the dispatch decision (see [`dispatch-protocol.md`](./dispatch-protocol.md)).

## Failure modes the router avoids

| Mistake | Why wrong | Router does instead |
|---|---|---|
| Chain every task | Wastes tokens on simple tasks | Single-domain → one skill, no ceremony |
| Run domain skills in series | Most don't depend on each other | `+` for parallel where possible |
| Skip `writing-plans` on multi-domain BUILD | Parallel agents diverge without a plan | Always prepend on multi-domain BUILD |
| Use `opus` for every chain step | Burns budget on simple steps | Each step gets its own model from the routing triple |
