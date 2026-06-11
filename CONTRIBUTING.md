# Contributing

Thanks for considering a contribution. Routing rules are opinionated — most useful PRs are small, evidence-backed, and live in the right file.

## Where things live

| Change | File |
|---|---|
| Add/change a routing-table row (signal → skill mapping) | [`SKILL.md`](./SKILL.md) |
| Project-specific override that doesn't belong in the universal core | example only — users add to their own `SKILL.personal.md` |
| Add a known skill source for catalog discovery | [`references/known-skill-repos.md`](./references/known-skill-repos.md) |
| Refine the catalog-check protocol | [`references/catalog-check.md`](./references/catalog-check.md) |
| Refine multi-domain chain syntax / shapes | [`references/multi-domain-chaining.md`](./references/multi-domain-chaining.md) |
| Refine named-chain schema or matching rules | [`references/named-chains.md`](./references/named-chains.md) |
| Refine thinking-depth rules | [`references/thinking-depth.md`](./references/thinking-depth.md) |
| Statusline visual changes | [`statusline.sh`](./statusline.sh) |
| Test harness | [`run_routing_test.sh`](./run_routing_test.sh) |
| User-facing prose (pitch, install, design, value) | [`README.md`](./README.md) and [`docs/`](./docs/) |
| Codex-flavor parity | [`codex-skill/skill-router/`](./codex-skill/skill-router/) |

## What we want

1. **Routing-table corrections.** If `skill-router` consistently picks the wrong skill for a real signal, open an issue with the prompt + actual + expected, or PR the table fix directly.
2. **New known skill sources.** If you maintain or know of a curated SKILL.md repo with 5+ skills under a stable license, PR an entry to `references/known-skill-repos.md`.
3. **Statusline polish.** Visual segments, terminal compat, performance.
4. **Codex flavor parity.** When the Claude flavor changes, the Codex flavor should track unless there's a reason it shouldn't.

## What we don't want

1. **Lifecycle features** (skill creation, self-improvement loops, fresh-agent review). That's a different product — see [`zysilm/skill-master`](https://github.com/zysilm/skill-master) for that direction.
2. **Slash commands or new user-facing UI surfaces.** Zero-UX is a load-bearing design principle — the router is invoked by Claude Code, never by the user.
3. **Telemetry / observability features** that require a server, account, or data leaving the local machine.
4. **Backwards-compat shims.** Routing rules change. Users who pin to a commit can stay pinned.

## PR checklist

- [ ] If you added a routing-table row, you've also added a test case to `run_routing_test.sh`
- [ ] If you changed routing logic in `SKILL.md`, you've also updated the matching `references/*.md` doc
- [ ] If you changed a public-facing claim (e.g. measured accuracy), you've also updated `README.md`
- [ ] If you changed Claude-flavor routing semantics, you've also updated the Codex flavor (or noted why not)
- [ ] You've appended a line to `CHANGELOG.md` under "Unreleased"
- [ ] Statusline still renders cleanly (`bash statusline.sh < sample-input.json`)

## Decisions and trade-offs

If you want to change a load-bearing design principle (zero-UX, deterministic routing, single-file core, statelessness), open an issue first. These principles are why the project is small and trustable; I'll ask why your change is worth losing one.

## License

All contributions are MIT, same as the project.
