# Jev: the model that chooses

skill-router uses one hosted model on the hot path: **Jev** (`jev-1.13.0`, TypeSafe System One). It is a
*chooser*, not a chat model: it is handed a set of options and returns which ones fit and how confident it is.
It cannot generate text, so nothing it says is ever shown to you or trusted as a name; every answer is an
opaque option key that the router maps back to a skill it already knows about.

Everything else in the router is lexical, local and deterministic. Jev is optional. Without it the router runs
the local ranking and, if you set a key, a small-model tie-break (see [fallback](#when-jev-is-absent-or-slow)).

## What Jev decides

One request, four independent questions over the same state:

| Question | Options | Used for |
|---|---|---|
| `domain_N` | every non-process skill in your index, plus `none` | the skill whose subject fits (split into several questions past 255 options, never truncated) |
| `process` | every process skill (TDD, debugging, planning), plus `none` | the working method |
| `path` | `BROKEN` / `BUILD` / `OPERATE` / `QUESTION` | which route card to print when the regex triage is silent |
| `tier` | `light` / `standard` / `heavy` | which model should do the work (below) |

There is no retrieval stage in front of it. Measured on 63 real prompt → skill pairs, lexical ranking followed
by a model over the top 8 scored 10/66 because the right skill was often not in the top 30 (typos defeat token
matching); Jev over the whole index scored 39/63. Raw numbers: [`docs/jev-eval-2026-09-21/`](./jev-eval-2026-09-21/RESULTS.md).

## When it acts: the 0.8 gate

| Confidence | Result |
|---|---|
| ≥ 0.8 | routes: the skill goes on the card |
| 0.5 – 0.8 | silent. A `Possible fit:` line exists behind `SKILL_ROUTER_JEV_SUGGEST=1`; it was right 6 of 18 times, so it is off |
| < 0.5 | silent |

At ≥ 0.8 the measured precision was 32 of 34 routed prompts. Silence is the expected outcome for most turns:
questions and chat should not get a card. The threshold is pinned to the model version because it was measured
on that version; a model change means re-measuring, not editing the number.

## Below the gate: Jev's top three

Jev returns a probability for every skill it was offered. When the top pick is below 0.8, the router names Jev's
three most likely skills in one line and lets Claude choose (`SKILL_ROUTER_JEV_SHORTLIST=0` turns it off). Same
request, no extra cost, at most 200 characters (about 185 typical), never enforced, gated to work turns. Measured
in-sample with the work gate: the right skill reaches Claude on 10 of 27 hard real tasks (2 before) and 38 of 74
everyday ones (11 before), and 0 of 6 chat controls got a line.
Details: [no-key-routing.md](./no-key-routing.md).

## Work tier → which model does the work

The `tier` answer is the only place the router ever changes which Claude model runs something.

| Tier | Meaning | Effect |
|---|---|---|
| `light` | mechanical, one right answer: find, list, summarise, rename, run-and-report | sub-agent dispatched on `haiku` |
| `standard` | routine work against a clear spec | sub-agent dispatched on `sonnet` |
| `heavy` | design, root cause, security, migrations, ambiguity | inherits the session model |

Rules that keep this safe:

- **Asymmetric on purpose.** A wrong downgrade is invisible quality loss; a wrong "inherit" only costs money. So
  only a ≥ 0.8 `light`/`standard` answer moves off the session model, and *unsure means heavy*.
- **Dispatch only.** The main session model is never switched. `PreToolUse` on `Task`/`Agent` judges the
  sub-agent's *own* prompt (one small cached call, ~200 input tokens) and sets `model`.
- **You always win.** An explicit `model=` in the call, `[no-router]` in the prompt, or
  `SKILL_ROUTER_SUBAGENT_MODEL=0` leaves the dispatch alone.
- **Observed.** On 93 real dispatches Jev called 11 light (every one a read-only inventory), 3 standard, 43 heavy,
  and agreed in direction with all 38 explicit `model=` choices Claude had made itself.

## What leaves your machine

This is the part to read before enabling it.

| Call | Destination | What is sent | When |
|---|---|---|---|
| Choose | `api.typesafe.ai` | your prompt (≤ 16,000 chars); for prompts of ≤ 15 words, the previous assistant message (≤ 4,000 chars); the routing summary of every indexed skill | each uncached prompt |
| Sub-agent tier | `api.typesafe.ai` | the sub-agent's prompt only, no index | each uncached dispatch |

- **No key, no call.** Nothing is sent unless `TYPESAFE_API_KEY` is set (environment, or `refresh_env.py`'s cache).
- **Your skill descriptions are sent too.** If a private skill's description is sensitive, set
  `SKILL_ROUTER_JEV=0`, or keep it out of the index.
- **Cached by content.** Identical prompt + context + index fingerprint is answered from
  `~/.claude/skill_router_cache/jev/` and never leaves the machine twice.
- **Logs hold keywords and hashes, never prompt text** (`SKILL_ROUTER_NO_LEARN=1` logs nothing).
- Nothing else on the Jev path calls out. Index enrichment (`build_index.py --enrich`) and the fallback
  tie-break use Gemini or Anthropic and send skill files / the prompt respectively; see the table in the README.

## Cost and latency

~12.3k input tokens (~$0.0005) per **uncached** prompt; a repeated prompt costs nothing. Median 327–467 ms.
Latency is bimodal (250–550 ms or 2.6–6.8 s), so the wall-clock limit is **1.2 s**: past it the router stops
waiting and uses the fallback, which means a slow API never blocks a prompt (the hook has a 12 s ceiling on top).

## When Jev is absent or slow

Every failure path returns "no answer", never an exception into a hook:

```
no key · SKILL_ROUTER_JEV=0 · timeout (1.2 s) · HTTP/network error · malformed answer · unknown option key
        └─► enriched local ranking (~80 ms) ──► small-model tie-break (optional, only below 35 % margin)
              └─► regex path triage + process table
```

Without any key you still get lexical routing, project routes, tiers and the iron rule. Without Jev you lose
the typo tolerance, the `tier` question (so sub-agents inherit the session model) and the confidence gate.

## Setup and verification

```bash
export TYPESAFE_API_KEY=...                       # or: python3 scripts/refresh_env.py (Doppler, optional)
python3 scripts/jev_choose.py --status            # key present? index size? cache entries?
python3 scripts/jev_choose.py "fix the failing checkout test"   # one live choice (spends ~$0.0005)
python3 scripts/doctor.py                         # "jev chooser" line
```

## Knobs

| Variable | Default | Effect |
|---|---|---|
| `SKILL_ROUTER_JEV` | `1` | `0` / `off` / `false` disables Jev entirely |
| `SKILL_ROUTER_JEV_TIMEOUT` | `1.2` | wall-clock seconds before falling back |
| `SKILL_ROUTER_JEV_SUGGEST` | off | `1` prints the 0.5–0.8 `Possible fit:` line |
| `SKILL_ROUTER_SUBAGENT_MODEL` | on | `0` never sets a sub-agent model |
| `SKILL_ROUTER_CACHE_DIR` | `~/.claude/skill_router_cache` | relocate the Jev cache |
| `SKILL_ROUTER_OFF` | off | `1` stands every hook down |

## What Jev does not do

- It does not write or rewrite anything, and never installs a skill (`find-skills` returns candidates only).
- It does not see your code, files or tool output: only the prompt, the short-reply context above, and skill summaries.
- It does not override the project routes in `SKILL.personal.md`; those are deterministic and win first.
- It is not evidence of a field accuracy rate. The 63-pair set is the author's; the live number is the follow rate
  the learner computes on *your* sessions.

## Replacing Jev

`jev_choose.py` exposes one function, `choose(prompt, context="") -> Choice | None`, plus `tier_only()`. Any
backend that returns the same `Choice` (domain, process, path, tier, each with a confidence) can replace it;
returning `None` selects the fallback. Keep the 0.8 gate tied to whatever you measured it on.
