# skill-router

**Right Skill, right Agent, right Model, right Thinking depth — before any tool fires.**

One SKILL.md (~200 lines). Auto-loaded by Claude Code. Zero UX. **90% routing accuracy** on 20 real prompts. Per-step model enforcement saves **30%+** on multi-domain chains.

![chain announcement](assets/proof/chain-multi-domain.png)

```
You type:    lets start teh implementation
Router says: This touches 3 domains: UI/Frontend, DB, Edge function.
             Chain: writing-plans → dispatching-parallel-agents → frontend-design + db-expert
```

The router announces what it's going to do *before* any code is touched. That announcement is testable — you can grep your transcript later and verify what fired matches what was announced.

## Why you'll want this

Claude Code can use hundreds of skills. It picks the wrong one ~20% of the time, skips skills it decides are "too simple," and burns `opus` tokens on tasks that need `haiku`. `skill-router` replaces that implicit choice with a deterministic 3-question triage that runs before every non-trivial task.

## Install — one curl, 10 seconds

```bash
mkdir -p ~/.claude/skills/skill-router
curl -sL https://raw.githubusercontent.com/hussi9/skill-router/main/SKILL.md \
  > ~/.claude/skills/skill-router/SKILL.md
```

Done. Claude Code auto-loads it on session start. **You never invoke it manually.** Start a new Claude Code session and type any non-trivial task — the chain announcement fires before any tool runs.

## Verify it's working

In a new Claude Code session, type something obviously multi-domain like:

> *"add user profile page that saves to the database"*

You should see Claude announce a chain *before* reading any files:

```
This touches 2 domains: UI/Frontend, DB schema.
Chain: writing-plans → <UI skill> + <DB skill>
```

If Claude jumps straight into reading files with no announcement, the skill didn't load — verify `~/.claude/skills/skill-router/SKILL.md` exists and starts with `name: skill-router`.

## Optional — statusline shows live activity

Add the statusline + hook (5 minutes — see [docs/customizing.md](./docs/customizing.md)) to surface routing in real time:

```
◆ sonnet · ~/myproject · ⎇ main · 🔀 router · ▶ ship-feature 2/4 ✦saved · 🧠 hard · ⚙ frontend-design ✓ · ▓▓░░ 18% · $0.04
```

| Segment | Meaning |
|---|---|
| `🔀 router` | router currently routing your prompt |
| `🔀 R5` | router has fired 5 times this session |
| `▶ ship-feature 2/4` | chain mid-flight, on step 2 of 4 |
| `✦saved` | this chain came from a saved chain in your `SKILL.personal.md` |
| `🧠 hard` | extended-thinking step in flight |
| `⚙ frontend-design ✓` | last skill (✓ = router upgraded it via catalog check) |

## Measured

20 real prompts through `claude -p` (test harness in [`run_routing_test.sh`](./run_routing_test.sh)):

| | Score |
|---|---|
| Path + Skill + Model correct | 18/20 (**90%**) |
| Skill tool actually fires correctly | 7/8 (**88%**) |

## Common questions

**Will this slow Claude Code down?**
~5 seconds of routing thought before tool calls fire. Saves time overall by avoiding wrong-skill rabbit holes.

**Do I need other skills installed first?**
No, but the router is most useful with [superpowers](https://github.com/obra/superpowers) + a catalog like [Antigravity](https://github.com/sickn33/antigravity-awesome-skills) installed. The router still routes correctly on a bare install — it just has fewer specialists to dispatch to.

**Can I turn it off temporarily?**
Yes — `mv ~/.claude/skills/skill-router/SKILL.md ~/.claude/skills/skill-router/SKILL.md.off` and restart Claude Code. Move it back to re-enable. There's no global toggle by design (zero UX).

**Does this work on claude.ai or only Claude Code?**
Claude Code (CLI) is production. The Codex flavor in [`codex-skill/`](./codex-skill/skill-router/) is a working draft. Web claude.ai doesn't load skills the same way — not supported.

**What about my custom skills?**
The router checks `~/.claude/skills/`, `~/.agent/skills/`, and `~/.composio-skills/` on every task — your custom skills get used automatically when their name or description matches the task signature. See [references/catalog-check.md](./references/catalog-check.md).

**How do I add my own routing rules?**
Copy `SKILL.personal.md` to `~/.claude/skills/skill-router/SKILL.personal.md` and edit. Project-specific rules layer on top of the universal core (CSS-cascade model). See [docs/customizing.md](./docs/customizing.md).

**Where does it log activity?**
`~/.claude/skill_usage.log` (every Skill fire) and `~/.claude/skill_router_log.jsonl` (chain announcements + thinking events). Useful for debugging and for the statusline.

## Documentation

| Doc | What you'll learn | Length |
|---|---|---|
| [docs/how-it-works.md](./docs/how-it-works.md) | The 4-step routing pipeline | ~5 min |
| [docs/customizing.md](./docs/customizing.md) | Personal overrides + named chains | ~3 min |
| [docs/proof.md](./docs/proof.md) | Real-session screenshots | ~2 min |

Reference (router consults these at runtime): [`references/`](./references/).

## Works with

| Source | Skills | How router uses it |
|---|---|---|
| [superpowers](https://github.com/obra/superpowers) | process discipline | routing table |
| [Antigravity](https://github.com/sickn33/antigravity-awesome-skills) | 1,400+ domain skills | catalog check |
| [Composio](https://github.com/ComposioHQ) | 940+ integrations | catalog check |
| [anthropics/skills](https://github.com/anthropics/skills) | official examples | catalog check |
| [intellectronica/agent-skills](https://github.com/intellectronica/agent-skills) | `ultrathink`, deep-thinking | catalog check |
| your custom `~/.claude/skills/` | whatever you install | catalog check |

## Two flavors

| | Where | Status |
|---|---|---|
| Claude Code | [`SKILL.md`](./SKILL.md) | Production |
| Codex | [`codex-skill/skill-router/`](./codex-skill/skill-router/) | Working draft |

## Project

- [CHANGELOG.md](./CHANGELOG.md) — version history
- [CONTRIBUTING.md](./CONTRIBUTING.md) — how to propose changes (TL;DR: routing-table corrections welcome, lifecycle features go to a different repo)
- [LICENSE](./LICENSE) — MIT
