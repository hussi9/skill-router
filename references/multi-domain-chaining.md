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

Single-domain:
```
This is a [BROKEN | BUILD | OPERATE] task → <skill> → <agent>.
```

Multi-domain (computed):
```
This touches [N] domains: <d1>, <d2>. Chain: <step1> → <step2> + <step3>.
```

Multi-domain (saved):
```
Using your saved chain `<name>`: <step1> → <step2> + <step3>.
```

## Failure modes the router avoids

| Mistake | Why wrong | Router does instead |
|---|---|---|
| Chain every task | Wastes tokens on simple tasks | Single-domain → one skill, no ceremony |
| Run domain skills in series | Most don't depend on each other | `+` for parallel where possible |
| Skip `writing-plans` on multi-domain BUILD | Parallel agents diverge without a plan | Always prepend on multi-domain BUILD |
| Use `opus` for every chain step | Burns budget on simple steps | Each step gets its own model from the routing triple |
