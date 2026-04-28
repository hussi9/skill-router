# skill-router

> One file. Routes every Claude Code task to the right Skill, Agent, and Model — before any tool fires.

![chain announcement](assets/proof/chain-multi-domain.png)

```
You type:    lets start teh implementation
Router says: This touches 3 domains: UI/Frontend, DB, Edge function.
             Chain: writing-plans → dispatching-parallel-agents → frontend-design + db-expert
```

## Why

Claude Code can use hundreds of skills. It picks the wrong one ~20% of the time, skips skills it decides are "too simple," and burns `opus` tokens on tasks that need `haiku`. `skill-router` replaces that implicit choice with a deterministic 3-question triage that runs before every non-trivial task.

## Install

```bash
mkdir -p ~/.claude/skills/skill-router
curl -sL https://raw.githubusercontent.com/hussi9/skill-router/main/SKILL.md \
  > ~/.claude/skills/skill-router/SKILL.md
```

That's it. Claude Code auto-loads it on session start. You never invoke it manually.

## Measured

20 real prompts through `claude -p` (test harness in [`run_routing_test.sh`](./run_routing_test.sh)):

| | Score |
|---|---|
| Path + Skill + Model correct | 18/20 (**90%**) |
| Skill tool actually fires correctly | 7/8 (**88%**) |

## Documentation

Start with **[`docs/`](./docs/)** — short, ordered.

- [docs/how-it-works.md](./docs/how-it-works.md) — the routing pipeline end-to-end
- [docs/customizing.md](./docs/customizing.md) — per-project overrides + named chains
- [docs/proof.md](./docs/proof.md) — verbatim chain announcements from real sessions

## Works with

| Source | Skills | How router uses it |
|---|---|---|
| [superpowers](https://github.com/obra/superpowers) | process discipline | routing table |
| [Antigravity](https://github.com/sickn33/antigravity-awesome-skills) | 1,400+ domain skills | catalog check |
| [Composio](https://github.com/ComposioHQ) | 940+ integrations | catalog check |
| [anthropics/skills](https://github.com/anthropics/skills) | official examples | catalog check |
| your custom `~/.claude/skills/` | whatever you install | catalog check |

## Two flavors

| | Where | Status |
|---|---|---|
| Claude Code | [`SKILL.md`](./SKILL.md) | Production |
| Codex | [`codex-skill/skill-router/`](./codex-skill/skill-router/) | Working draft |

## License

MIT.
