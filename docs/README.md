[← back to skill-router](../README.md)

# Documentation

Read in order. Each doc has one job.

| Doc | What you'll learn | Length |
|---|---|---|
| [how-it-works.md](./how-it-works.md) | The 4-step routing pipeline that runs on every task | ~5 min |
| [customizing.md](./customizing.md) | How to add per-project routing + saved chains | ~3 min |
| [proof.md](./proof.md) | What the router actually does in real sessions | ~2 min |

## Reference (router consults these at runtime, not user-facing)

These live in [`../references/`](../references/) and are loaded by `SKILL.md` when it needs the deep protocol:

- [catalog-check.md](../references/catalog-check.md) — long-tail skill discovery
- [known-skill-repos.md](../references/known-skill-repos.md) — curated catalog sources
- [multi-domain-chaining.md](../references/multi-domain-chaining.md) — chain operator semantics
- [named-chains.md](../references/named-chains.md) — saved-chain match rules

## Looking for the router itself?

That's [`../SKILL.md`](../SKILL.md). It's ~265 lines and Claude Code auto-loads it. The docs in this folder explain *why* it's shaped that way.
