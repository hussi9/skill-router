# Per-project skill visibility (Wave 4)

Claude Code caps the skill listing at 1% of the context window by default. When the listing overflows,
the least-used skills lose their descriptions first and keep only their names. A project can therefore
lose the descriptions of skills it uses to skills it never touches. `visibility` plans how to fix that
for one project and, only on an explicit command, writes the fix.

```
python3 scripts/cli.py visibility plan  [--project DIR] [--no-jev] [--names-free] [--context-tokens N]
python3 scripts/cli.py visibility apply [--project DIR] [--force] [--no-jev] [--names-free] [--context-tokens N]
python3 scripts/cli.py visibility undo  [--project DIR]
```

`plan` is a dry run and the default. It also writes a small cache
(`~/.claude/skill_router_cache/visibility-summary.json`, mode 600; project keys and counts only) that
the doctor line reads, so the doctor never runs the roughly 10 s transcript scan.

## Levers

| Lever | What it does | Status |
|---|---|---|
| `skillOverrides` name-only, per project | Unused user/project skills keep their name but give up their description, freeing listing room for the skills this project uses | Applied by `apply` into `<project>/.claude/settings.local.json` only |
| `enabledPlugins` | Turning off unused plugins may shrink the listing | Suggestion only. The docs do not say whether a disabled plugin's skills leave the listing, and plugin skills cannot take `skillOverrides` |
| `skillListingBudgetFraction` | Raises the 1% listing budget | Reported with its token cost per session, never applied |
| Archiving unused skills | Removes dead weight for every project | The `audit` command lists them; the move is yours |

## How the plan is computed

Usage per project comes from `Skill` tool_use events in the local transcripts (last 90 days), keyed by
a project key (first 12 hex of sha256 of the resolved path). Sub-agent transcripts
(`<session>/subagents/*.jsonl`) count too. Relevance is 3 x project use + 1 x global
use (+ 2 x Jev rank hits unless `--no-jev`). Unused non-plugin skills are set name-only in ascending
relevance until every project-used skill keeps its description or nothing improves; no-op overrides
are pruned. A skill used in the project in the last 90 days is never hidden, `off` is never written,
plugin skills are never touched.

Join caveat: keys recorded by the hooks come from the prompt's cwd, which may be a subdirectory, while
transcript keys come from the session's start directory. The optional Jev-rank join, which matches the
two, can therefore miss for sessions started in a parent directory.

Assumptions, stated in every output:

- Whether skill names count toward the listing budget is **unknown** (docs silent). Both models are
  run: "names count" (the audit's model) and "names free" (`--names-free`).
- The harness orders drops by its own usage statistics, which we cannot see. We approximate them with
  global usage counts, so every before/after number is a simulation.

## Safety

- Dry run by default; `apply` needs an explicit command.
- `apply` writes only the project's `settings.local.json`, refuses if it is tracked by git, refuses
  non-joinable plans, and refuses plans that are not worthwhile unless `--force`.
- A byte-identical backup is made first; `undo` restores it non-destructively (it merges rather than
  clobbering edits made after apply). `apply` reformats the JSON (indent 2); the backup keeps the original.

## Measurement (dry runs, 2026-11-02)

The three most active joinable projects by `Skill` uses, keyed by project key. Counts are
project-used skills that keep their description (kept / total) and descriptions dropped from the whole
listing, before then after the plan. Jev mode, 200k context. Budget lever = the smallest
`skillListingBudgetFraction` (default 0.01) at which every project-used skill is described.

**Names count toward the budget**

| Project key | Used skills kept before | after | Dropped before | after | name-only | Worthwhile | Budget lever |
|---|---|---|---|---|---|---|---|
| c6d4d875799e | 1/56 | 3/56 | 436 | 433 | 1 | no | not reachable (<= 0.10) |
| dcea7aa7d85b | 1/20 | 3/20 | 436 | 433 | 1 | no | not reachable (<= 0.10) |
| dbfd4127bee0 | 0/22 | 1/22 | 436 | 433 | 1 | no | not reachable (<= 0.10) |

Names alone use 7,398 of the 8,000 listing characters, so descriptions have almost no room under this model.

**Names are free**

| Project key | Used skills kept before | after | Dropped before | after | name-only | Worthwhile | Budget lever |
|---|---|---|---|---|---|---|---|
| c6d4d875799e | 26/56 | 30/56 | 404 | 398 | 6 | no | 0.097 (+17,400 tokens/session) |
| dcea7aa7d85b | 9/20 | 19/20 | 404 | 305 | 93 | yes | 0.097 (+17,400 tokens/session) |
| dbfd4127bee0 | 12/22 | 21/22 | 404 | 179 | 217 | no (217 overrides for a gain of 9) | 0.097 (+17,400 tokens/session) |

Unused skills at measurement time: 169 (the same figure `cli.py audit` reports: no logged Skill use in 90 days, plugin and newly installed skills excluded).

### Bar

Plan bar: after the plan, at least 80% of project-used skills keep descriptions, in each project.

- **Names count: not met** in any project (3/56, 3/20, 1/22).
- **Names free: not met overall.** One project passes the gate (19/20 = 95%) but only by turning 93
  skills name-only; a second reaches 21/22 = 95% but needs 217 overrides and fails the gate; the busiest
  project reaches 30/56 = 54%. Hundreds of name-only overrides to recover a handful of descriptions is an
  unattractive trade.

Measured with sub-agent transcripts included (about a quarter of real skill use); the three projects are
the most active by that count.

### Conclusion

The bar is not met, so Wave 4 ships as a dry-run planner. Setting skills name-only cannot make room that
the listing budget does not have: whether it helps depends on the unknown names-in-budget rule, and where
it helps it needs hundreds of overrides. The levers that do work are archiving the unused skills (169 of
them) and the budget fraction (about 0.097, costing about 17,400 tokens per session). The doctor line points
at those, not at `apply`. Whether the real harness behaves like the simulation is not measured here; use
`apply` only when the plan says worthwhile and you accept that, and `undo` reverses it.

## Measured listing

`visibility plan` prints a "measured:" line beside the simulated before/after whenever
`python3 scripts/cli.py listing measure` has run for the project (labelled stale after 7 days).
The simulation is approximate; the measurement is what Claude Code reported in `/context`.

Measured on 2026-11-02: 43 of 764 entries described; Skills ~9800 tokens; context window 1000000.
