# Work tier eval — 2026-09-22

Question: can Jev's `tier` Choice (light / standard / heavy, see `jev_choose.TIER_CRITERIA`)
decide which model a piece of work gets, at the same 0.8 gate the skill choice uses?
Read-only run against `tier_only()` with a throwaway cache, live `jev-1.13.0`.

## Population 1 — 150 real user prompts (UserPromptSubmit)

Last 150 user turns across the 40 most recent transcripts, harness lines included.

| tier / band | count |
|---|---|
| heavy ≥ 0.8 | 33 |
| heavy 0.5–0.8 | 37 |
| heavy < 0.5 | 29 |
| light 0.5–0.8 | 27 |
| light < 0.5 | 14 |
| light ≥ 0.8 | **3** |
| standard ≥ 0.8 | **0** |
| standard 0.5–0.8 | 3 |
| standard < 0.5 | 4 |

Median 467 ms. Light ≥ 0.8 were `run ponytail-audit on scrollbook`, `verify it's live on
scrollbook.io`, and one `[Image: …]` caption. The 0.5–0.8 light band is mostly harness
noise (image captions, Stop-hook feedback) plus `push it to a preview branch`, `commit the
skill-router changes`, `untrack .archive and remove the dead deps`.

Reading: user prompts are short, misspelled and open-ended, and Jev correctly refuses to call
them mechanical. **The card's `Work:` line will print on ~2 % of turns.** That is the right
number — a user prompt is rarely the thing that should be downgraded.

## Population 2 — 93 real sub-agent dispatches (PreToolUse Task|Agent)

Every `Agent(...)` / `Task(...)` tool_use in the 120 most recent transcripts, with the prompt
Claude wrote for the sub-agent. 38 of 93 carried an explicit `model=` already.

| tier / band | count |
|---|---|
| heavy ≥ 0.8 | 43 |
| light ≥ 0.8 | **11** |
| heavy 0.5–0.8 | 8 |
| heavy < 0.5 | 9 |
| light 0.5–0.8 | 6 |
| light < 0.5 | 4 |
| standard 0.5–0.8 | 5 |
| standard < 0.5 | 4 |
| standard ≥ 0.8 | **3** |

Median 327 ms. The hook would set a model on **10 of the 55 dispatches that had none** (18 %).

Every light ≥ 0.8 (0.83–0.97) was a read-only inventory: "Read `BookHomePage.tsx` in full and
produce an exhaustive, read-only inventory", "Find and read the `ChapterList` component…",
"READ-ONLY. I am writing a polish list… need each of these facts verified", "Read-only research in
the Scrollbook web app… Do NOT edit". All Explore or general-purpose. Haiku is the right model for
every one of them.

Every standard ≥ 0.8 (0.80–0.89) was "You are implementing one task of a 15-task plan… This is
Task N" — where Claude had itself chosen `sonnet` (twice) or `haiku` (once).

Heavy ≥ 0.8 (0.82–1.00): code reviews, UX audits, scoped re-reviews, whole-branch review, analytics
instrumentation. None should move.

### Agreement with the model Claude picked itself (38 dispatches)

| Claude chose | Jev said |
|---|---|
| `opus` (2) | heavy 1.00, heavy 0.98 |
| `sonnet` for a review (15) | heavy, 0.74–1.00 (14); one 0.31 |
| `sonnet` for a plan task (12) | standard 0.32–0.89 (8), heavy 0.82 (1), light 0.83 (1, a read-only research task), light 0.16 (1, "run the final task") |
| `haiku` (3) | light 0.59 (×2, "Phase 0 housekeeping"), standard 0.85 (a plan task) |

Direction agrees everywhere it is confident. The one place Jev is more careful than Claude is
the `haiku` plan task (standard 0.85 → the hook would have said sonnet). Explicit `model=` wins
in the hook, so none of these 38 would actually be touched.

## Decisions taken from this

- Dispatch model: light → haiku, standard → sonnet, **≥ 0.8 only**, explicit `model=` untouched.
- `Work:` card line: ≥ 0.8 only. ~2 % of user turns.
- Kimi nudge: ≥ 0.8 when a quota window is "high" (≥ 80 %); **≥ 0.5 when "critical" (≥ 95 %)**,
  because the 0.5–0.8 band on user prompts is small commits/pushes/untracks — the work Kimi should
  absorb once the subscription is nearly gone — and the nudge is advisory, one line, at most
  once per half hour per session.
- Heavy never goes to Kimi and never leaves the session model, whatever the quota.

Scripts: the population extraction and the two runs are inline in the session that produced
this file; `tier_only()` with `SKILL_ROUTER_CACHE_DIR=/tmp/jev-eval-cache` and a
`TYPESAFE_API_KEY` in the environment reproduces it against any list of prompts.
