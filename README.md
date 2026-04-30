# skill-router

[![GitHub stars](https://img.shields.io/github/stars/hussi9/skill-router?style=social)](https://github.com/hussi9/skill-router/stargazers) [![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![CI](https://github.com/hussi9/skill-router/workflows/lint/badge.svg)](https://github.com/hussi9/skill-router/actions)

**Right Skill, right Agent, right Model, right Thinking depth — before any tool fires.**

One SKILL.md (~300 lines). Auto-loaded by Claude Code. Zero UX. **80% composite routing accuracy / 90% path-only** on 20 real prompts (3-run average). Every announcement line is `[skill-router]`-prefixed so `grep` can audit your transcript.

```
$ > add a settings page that writes to the db and emails the user

[skill-router] This touches 3 domains: UI/Frontend, DB schema, Edge function.
[skill-router] Chain: superpowers:writing-plans → frontend-design:frontend-design + db-expert
[skill-router] Models: sonnet · sonnet+sonnet  ·  Thinking: think
[skill-router] Invoke step 1/2 now:

▶ superpowers:writing-plans  (sonnet, in-session)
▶ frontend-design:frontend-design + db-expert  (sonnet, parallel via Agent)
```

The router announces what it will do *before* any tool fires. `[skill-router]` on every line is the testable contract — `grep '\[skill-router\]'` your transcript and verify what fired matches what was announced.

Every step also writes to `~/.claude/skill_router_log.jsonl`. Run `python3 scripts/audit-dispatch.py` after a week to score whether the router is actually following its own protocol.

![chain announcement](assets/proof/chain-multi-domain.png)

## Why you'll want this

Claude Code has a skills ecosystem with 2,700+ skills. There's no built-in routing layer.

Claude guesses which skill to use — or ignores them entirely. On a 20-prompt test harness, it picks the wrong skill ~20% of the time, skips skills it decides are "too simple," and makes no attempt to match model cost to task complexity. `skill-router` replaces that implicit guessing with a deterministic 3-question triage that runs before every non-trivial task.

Three things make the wrong-skill problem worse as you install more skills:

**No routing.** Claude has no deterministic layer that decides: what is this task? what skill fits? what model is right? Without a routing layer, more skills = more ambiguity.

**No catalog maintenance.** Your installed skills drift. Ghost entries pile up. The router validates skill existence before blocking on iron-rule enforcement — deadlocks are caught automatically.

**No chaining.** Multi-domain work needs skills to fan out. "Add Stripe checkout that saves to DB and emails the user" is 4 domains — the router builds and dispatches the chain.

## What it actually outputs

Multi-domain BUILD — skills fan out in parallel:

```
> add stripe checkout — saves order to db, emails confirmation

[skill-router] This touches 4 domains: 3rd-party, DB schema, Edge function, UI/Frontend.
[skill-router] Chain: superpowers:writing-plans → connect-apps + db-expert + frontend-design:frontend-design
[skill-router] Models: sonnet · sonnet+sonnet+sonnet  ·  Thinking: think
[skill-router] Invoke step 1/2 now:

▶ superpowers:writing-plans  (sonnet, in-session)
▶ connect-apps + db-expert + frontend-design:frontend-design  (sonnet, parallel via Agent)
```

Research → plan → steering meeting — the idea-to-feature pipeline:

```
> want to add AI spending coach — weekly check-ins, pattern detection,
> personalized nudges. is this worth building? if yes, plan it out.

[skill-router] This touches 3 domains: Research/Strategy, Product planning, Architecture.
[skill-router] Chain: superpowers:brainstorming → superpowers:writing-plans → product-manager + tech-lead
[skill-router] Models: sonnet · sonnet · opus+opus  ·  Thinking: ultrathink
[skill-router] Invoke step 1/3 now:

▶ superpowers:brainstorming    (sonnet, in-session)
▶ superpowers:writing-plans    (sonnet, in-session)
▶ product-manager + tech-lead  (opus, parallel via Agent)
```

Step 1 researches the idea — prior art, risks, what similar products got wrong.
Step 2 converts that into a structured plan with tasks and open questions.
Step 3 runs a steering meeting: product-manager and tech-lead both read the plan simultaneously on opus — heaviest thinking, before a line of code is touched.

Single-domain OPERATE — safety gate before deploy:

```
> deploy to production

[skill-router] This is a OPERATE task — 2-step chain.
[skill-router] Chain: superpowers:verification-before-completion → vercel:deploy
[skill-router] Models: sonnet · sonnet
[skill-router] Invoke step 1/2 now:

▶ superpowers:verification-before-completion  (sonnet, in-session)
▶ vercel:deploy  (sonnet, in-session)
```

The verification gate runs every time before deploy fires — without you remembering to ask.

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
[skill-router] This touches 2 domains: UI/Frontend, DB schema.
[skill-router] Chain: superpowers:writing-plans → frontend-design:frontend-design + db-expert
[skill-router] Models: sonnet · sonnet+sonnet  ·  Thinking: think
[skill-router] Invoke step 1/2 now:

▶ superpowers:writing-plans  (sonnet, in-session)
▶ frontend-design:frontend-design + db-expert  (sonnet, parallel via Agent)
```

If Claude jumps straight into reading files with no `[skill-router]` lines, the skill didn't load — verify `~/.claude/skills/skill-router/SKILL.md` exists and starts with `name: skill-router`.

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

20 real prompts through `claude -p` (test harness in [`run_routing_test.sh`](./run_routing_test.sh)). 3-run average on Sonnet 4.6, 2026-04-29:

| | Score |
|---|---|
| Path routing only | 18/20 (**90%**) |
| Path + Skill + Model all correct | 16/20 (**80%**) |
| Model selection only | 19–20/20 (**95–100%**) |

Known stable misroute (same case fails every run):
- Ambiguous "fix X AND add Y" → routes to OPERATE instead of defaulting to BUILD per the ambiguity rule

This is a systematic gap in how Claude follows the routing table — not random variance.

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

**Does it get smarter over time?**
Yes — run `bash scripts/weekly-analysis.sh` (or automate it via launchd/crontab) to surface routing gaps and promote repeated chains to named chains. Named chains bypass LLM triage entirely — faster and 100% consistent. See [docs/self-improvement.md](./docs/self-improvement.md).

## Self-improvement — the router learns your patterns

Three analysis scripts ship with the repo. Run them weekly and the router improves automatically:

| Script | What it measures | What to do with it |
|---|---|---|
| `scripts/learn-from-history.py` | Announced skills vs actually invoked | Tighten patterns with false positives; broaden patterns for missed triggers |
| `scripts/audit-dispatch.py` | Chain steps announced vs logged | Fix dispatch protocol gaps |
| `scripts/learn-chains.py --apply` | Chains you've run 3+ times | Promotes them to named chains — no LLM triage, zero variance |

Run all three at once:
```bash
bash scripts/weekly-analysis.sh           # report only
bash scripts/weekly-analysis.sh --apply   # also promote repeated chains
```

Automate it (macOS launchd — runs every Monday at 9am):
```bash
REPO="$HOME/path/to/skill-router"
sed -e "s|{{SKILL_ROUTER_PATH}}|$REPO|g" -e "s|{{HOME}}|$HOME|g" \
    "$REPO/setup/launchd-weekly.plist" \
    > ~/Library/LaunchAgents/com.skill-router.weekly-analysis.plist
launchctl load ~/Library/LaunchAgents/com.skill-router.weekly-analysis.plist
```

Full details, Linux crontab instructions, and what to do when each metric is bad: [docs/self-improvement.md](./docs/self-improvement.md).

## Documentation

| Doc | What you'll learn | Length |
|---|---|---|
| [docs/how-it-works.md](./docs/how-it-works.md) | The 4-step routing pipeline | ~5 min |
| [docs/customizing.md](./docs/customizing.md) | Personal overrides + named chains | ~3 min |
| [docs/self-improvement.md](./docs/self-improvement.md) | Weekly analysis, named chain promotion, cron setup | ~5 min |
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
