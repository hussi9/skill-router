# Outcomes: did the router's lines help?

The router prints a route card and sometimes a shortlist. The outcomes log records what happened next, so
`stats` can say whether those lines were followed and whether routed turns do better than turns where the router
stayed silent on purpose (the holdout).

## What is recorded

One JSON object per line, append-only, mode 600. Keys, hashes and skill names only: never prompt text, commands,
output or file paths. Every string is clipped to 200 characters.

| Event | Fields | When |
| --- | --- | --- |
| `turn` | session_id, prompt_id | each user prompt |
| `shown` | card, shortlist, source (`jev`/`keyless`), chars, held_out? | the router printed (or, held out, would have printed) skill names |
| `signal` | kind, skill? | `loaded` (a Skill call), `tests_green`, `tests_red` (test commands), `correction` (next prompt opens with a negation), `rework` |
| `edits` | files (hash) | hash of a turn's edited file set |

`chars` is the length of the printed text; `stats` turns the median into tokens (chars / 4).

## Where, and the knobs

- `SKILL_ROUTER_OUTCOMES_FILE`: the outcomes log (default `~/.claude/skill_router_outcomes.jsonl`).
- `SKILL_ROUTER_FEATURES_FILE`: Jev feature rows for the distillation dataset (default
  `~/.claude/skill_router_cache/jev-features.jsonl`).
- `SKILL_ROUTER_HOLDOUT`: fraction of eligible turns where the router is silent on purpose, default `0.10`, `0`
  disables. Chosen by a deterministic hash of session and prompt ids. Hard routes (BROKEN, gated project routes)
  and output with no skill name are never held out.
- `SKILL_ROUTER_NO_LEARN=1` stops all writes.

## Success definition

A turn is known when it has any test result (`tests_green` or `tests_red`) or a `correction`; a turn with none
of these is unknown and is counted on neither side. A known turn succeeds when it has `tests_green`, no
`tests_red` and no `correction`. Events with an empty `prompt_id` are skipped. Lift is routed success rate minus held-out success rate, with n for each side.

## Reading `python3 scripts/cli.py stats [--days N]`

- Lines shown / held out: turns with printed lines vs. suppressed by the holdout.
- Followed: shown turns where Claude loaded a skill that was on the card or shortlist, per mode (jev, keyless).
- Success and lift: `routed: 12/20; held out: 3/6` means 12 of 20 known routed turns succeeded. Small n means
  noise: do not read lift until each side has dozens of known turns.
- Tokens injected per shown line: median printed length of one shown line / 4, over events that carry `chars`
  (turns that showed nothing are not in the median).

## Known limits

- Rework is detected from `edits` events but not recorded live (it needs an Edit/Write hook).
- Test pass/fail relies on PostToolUse and PostToolUseFailure delivery for Bash; a missing hook means unknown turns.
- Corrections are a prefix heuristic ("no", "wrong", "undo"...), so it misses polite redirects.
- The numbers need real sessions; a fresh install shows "not enough data".

## Bash hook cost

The PostToolUse and PostToolUseFailure Bash hooks are wrapped in a POSIX `case` pre-filter (patterns built from
`outcomes.TEST_RUNNERS`), so Python starts only when the payload text names a test runner. Measured with a non-test
payload (`ls`), `sh -c`, median of 5 runs: 74.1 ms before, 5.3 ms after.

## Verifying the harness payloads

The hook payload shapes (Bash PostToolUse and PostToolUseFailure) were assumed, not observed. To observe them, start one
session with `SKILL_ROUTER_CAPTURE_SHAPES=1` in the environment. Each hook payload then appends one line to
`~/.claude/skill_router_cache/payload-shapes.jsonl` (override with `SKILL_ROUTER_SHAPES_FILE`; mode 600): the hook event,
the tool name and a map from key path to type name, such as `"tool_response.stdout": "str"`. Key names are recorded;
values never are, so no prompt text or command output. Lists show as `list[<first element type>]`, nesting stops at depth 4, and capture stops at 50
records. Compare the file with what `outcomes.py` reads, then unset the variable. Because of the pre-filter, only test-command payloads reach `outcomes.py`, so capture sees only those.
