# Reddit Posts — skills-master

> All claims below are from real measured tests run via `claude -p` CLI.
> Routing accuracy: 18/20 (90%) on 20 real-world prompts
> Skill invocation accuracy: 7/8 (88%) correct Skill tool calls in real sessions
> Test harness: /Users/airbook/devpro/skills-master/run_routing_test.sh

---

## r/ClaudeAI — "I built a routing layer for Claude Code skills so it stops picking the wrong one"

Title: I built a routing layer for Claude Code skills so it stops picking the wrong one

---

So I've been using Claude Code with a bunch of skills installed — brainstorming, systematic-debugging, frontend-design, the works. The problem I kept running into: Claude would see "the login endpoint is returning 500s" and launch brainstorming. Or I'd ask it to fix a bug and it would start ideating on architecture for 90 seconds before touching the error.

The root cause is obvious in hindsight — Claude doesn't know which skill is right for a given task, so it guesses. And sometimes it rationalizes skipping skills entirely because it decides the task is "too simple."

I wrote a single SKILL.md that runs before every non-trivial task and answers three questions in order:

1. Is something **broken**? → systematic-debugging
2. Is this **creating something new**? → brainstorming → writing-plans → domain skill
3. Everything else? → specific skill per signal

That's it. The output is always a "dispatch triple": Skill + Agent + Model.

Model selection is baked in — not a separate decision. Reading a file? haiku. Production incident? opus. Everything else? sonnet.

I built a test harness to measure how it actually performs (running real prompts through `claude -p` and checking which `Skill` tool calls fire). On 20 real-world scenarios across BROKEN/BUILD/OPERATE/SKIP categories:

- **Routing accuracy: 90%** (18/20 correct path + skill + model)
- **Skill tool actually invoked correctly: 88%** (real Skill tool calls in live sessions)
- The 2 misses were both the same edge case: auth-adjacent tasks incorrectly triggering the "auth → opus" escalation rule

The file also has a discovery protocol for when there's no match — searches locally installed skills, checks the Antigravity catalog (860+ skills), then Composio (944+), and can auto-install from GitHub.

Repo: https://github.com/hussi9/skills-master

Install is one curl command. Personal overrides template included if you want project-specific routing on top of the universal core.

Would be curious if others have different approaches or edge cases I haven't covered.

---

## r/LocalLLaMA — "Deterministic skill routing for Claude Code — one SKILL.md, no vibes"

Title: Deterministic skill routing for Claude Code — measured 90% routing accuracy

---

If you're using Claude Code with lots of skills installed, you've probably noticed: it doesn't always pick the right one. It'll use brainstorming when something's broken, or skip systematic-debugging for something it decides is "simple." The model decides based on vibes.

I got tired of this and wrote a deterministic router. It's a single SKILL.md file that runs before every non-trivial task and applies a 3-question decision tree:

```
Q1: Something broken? → systematic-debugging
Q2: Creating something new? → brainstorming → writing-plans → domain skill  
Q3: Everything else? → operate path (refactor, deploy, research, etc.)
Ambiguous? → default to higher-complexity path
```

Output is always the same shape: Skill + Agent + Model. No ambiguity, no skipping.

I actually ran a test harness against this using `claude -p` CLI — 20 real task prompts across error handling, building, refactoring, simple questions. Results:

- **90% routing accuracy** — correct path + skill + model (18/20)
- **88% skill invocation accuracy** — the actual `Skill` tool fires with the right skill in real sessions
- Path routing alone: 95%. Skill selection alone: 95%. Model selection alone: 95%.
- The 2 misses: auth-adjacent refactor tasks incorrectly escalating to opus due to an overly broad auth signal

Model selection is deterministic by complexity tier — not left implicit. haiku for simple reads, opus for production incidents, sonnet for everything else.

Discovery protocol for no-match cases: scans local skills → Antigravity (860+) → Composio (944+) → GitHub → auto-installs.

Personal overrides layer on top (CSS cascade model) for project-specific routing.

One file. No build step, no config, no dependencies.

https://github.com/hussi9/skills-master

MIT. Curious if others have edge cases or alternate routing approaches.

---

## Hacker News — Show HN: skills-master — deterministic skill routing for Claude Code

Title: Show HN: skills-master – deterministic skill routing for Claude Code (90% accuracy, 1 file)

---

Claude Code supports custom skills (SKILL.md files that load domain-specific instructions), but with hundreds of skills available it frequently routes to the wrong one — or skips skills entirely when it decides something is "too simple."

skills-master is a routing layer: a single SKILL.md that runs before every non-trivial task and produces a deterministic output (Skill + Agent + Model) using a three-question decision tree.

The three questions, in order:
1. Is something broken? → systematic-debugging
2. Is this creating something new? → brainstorming chain
3. Everything else? → operate path

Ambiguous tasks default to the higher-complexity path. Model selection is part of routing, not a separate decision — the router maps task complexity to haiku / sonnet / opus directly.

**Measured results** (test harness using `claude -p` CLI, 20 real-world task prompts):

| Dimension | Score |
|-----------|-------|
| Overall (path + skill + model) | 18/20 (90%) |
| Path routing | 19/20 (95%) |
| Skill selection | 19/20 (95%) |
| Model selection | 19/20 (95%) |
| Skill tool actually fires correctly | 7/8 (88%) |

The 2 misses share one root cause: auth-adjacent task wording triggers the "auth/payments → opus" escalation rule even on non-security tasks. Fixable with a tighter signal.

Architecture is a two-file CSS cascade: universal core (~80 lines, shareable) + personal overrides (project-specific routing that wins over the core). No build step, no external dependencies.

Test harness is in the repo — you can run it against your own setup with `bash run_routing_test.sh`.

https://github.com/hussi9/skills-master

Happy to answer questions about the routing design, the test methodology, or the failure cases.

---

## FLAGGED CLAIMS — Need observability data before using

The following claims were in earlier drafts. DO NOT include without proof:

- "~40% API cost reduction" — needs before/after token spend data
- "3 minutes → 45 seconds" — specific timing claims need measurement
- "$0.40 → $0.06" — specific cost claims need measurement
- Any quantified improvement claims

What IS safe to claim (verifiably true):
- Wrong skill selection wastes tokens (demonstrably true — brainstorming calls more tools than systematic-debugging)
- Model selection is implicit without routing (true by default)
- haiku costs less than opus (documented by Anthropic)
- The three-question triage is deterministic (testable — same input, same output)
- Discovery protocol exists and can find/install skills (code is in the file)

---

## To get measurable claims

If you want to add back cost/time claims later:
1. Run 20+ tasks with and without skills-master active
2. Compare token usage via `/cost` or API usage dashboard
3. Log which model was used and could have been used
4. Then the numbers are real

