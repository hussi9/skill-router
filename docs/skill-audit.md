# skill doctor

`python3 scripts/cli.py audit [--format text|md|html] [--out FILE] [--no-jev] [--context-tokens N] [--project DIR]`

Read-only audit of installed skills: listing size, unused skills, shown-but-never-loaded,
overlaps, flagged names, and a 0-100 score. It only recommends; it never installs, removes,
disables or edits a skill. `python3 scripts/doctor.py` also prints one advisory `skill audit`
line (keyless, never a failure).

- `--format`: `text` (default, at most 30 lines), `md`, or `html` (one self-contained file).
- `--out FILE`: write the report with mode 600 instead of printing.
- `--no-jev`: judge overlaps by keyword only. Without it, Jev is used when `TYPESAFE_API_KEY` is set.
- `--context-tokens N`: the context window assumed for the listing budget (default 200000). It is an
  assumption: the audit cannot see the live window.
- `--project DIR`: project whose `.claude/settings*.json` apply (default: the current directory).

## What each number means

- **Listing tokens (uncapped)**: `sum(len(name) + len(description)) // 4` over routable skills.
  It is the raw sum, informational only; it is not what a session pays and costs no score.
- **Listing that fits**: "~X of ~Y listing tokens fit; N descriptions dropped". Claude Code budgets
  the skill listing: 1% of the context window by default (`skillListingBudgetFraction`, or
  `SLASH_COMMAND_TOOL_CHAR_BUDGET` in characters, integer or decimal, which wins), each description cut to
  `skillListingMaxDescChars` (default 1536), and on overflow the descriptions of the least-used
  skills are dropped first while names are kept. The audit applies the same rule (least-used first
  from the usage counts it already loads, ties by name). These defaults mirror Claude Code's
  published docs (https://code.claude.com/docs/en/skills.md) and may drift from the harness; the
  context size is an assumption (`--context-tokens`). Settings are read from `~/.claude/settings.json`,
  then the project's `.claude/settings.json` and `.claude/settings.local.json`, later wins.
- **Thin descriptions**: routable skills whose description has fewer than 5 tokens are listed as
  "too thin to route". Informational only; it costs no score points.
- **Unused (90 days)**: standalone skills with no logged Skill-tool use, installed at least 90 days
  ago, with at least 30 days of usage history. With less history the report says
  "no usage history yet".
- **Shown, never loaded**: the router showed the skill at least 5 times and it was never loaded
  (needs the outcomes log).
- **Overlaps**: pairs of skills of the same kind that appear to do the same job.
- **Flagged**: an installed skill has the same name as a public skill whose public audit flagged it.
  It is a name match only; check where your copy came from.
- **Score**: 0-100, higher is healthier.

## Score formula

```
Score = 100 - min(30, unused share * 60) - min(25, overlaps * 3)
            - min(20, round(descriptions_dropped / max(1, skills_with_descriptions) * 40))
```

The listing term charges for what the harness actually loses: descriptions dropped from the skill
listing, as a share of routable skills that have a description (half or more dropped = 20 points). The
uncapped listing size is shown for information only and costs nothing. The remedy is fewer
skills (archive unused ones) or name-only entries per project (`skillOverrides`), not just
shorter descriptions.

`flagged` costs no score, because a name collision is not evidence.

## What is NOT tracked

- Slash-command use and use from other clients: only logged Skill-tool calls count, so review
  before archiving anything marked unused.
- Install age comes from the SKILL.md file mtime. Unknown age is excluded from "unused" and counted
  separately.
- Plugin skills cannot be archived one by one, so they are reported per plugin, not as unused.
- Flagged is a name match, not a content check.

## Jev versus keyword overlap judging

- **Keyword (default, no key)**: two skills of the same kind overlap when their description token
  sets have Jaccard >= 0.6 and each has at least 5 tokens.
- **Jev (`TYPESAFE_API_KEY` set, no `--no-jev`)**: candidate pairs with Jaccard >= 0.3 (at most 200,
  batched at most 20 per call) are judged, and count as overlaps at probability >= 0.8. If a call
  fails or the time budget runs out, the remaining pairs fall back to the keyword rule.
- **What Jev receives**: skill descriptions only, each cut to at most 500 characters. No prompts,
  paths, usage counts or file contents.

### Calibration

Calibrated 2026-11-02 with `scripts/calibrate_overlap.py`. The first run found 31 keyword-flagged
pairs at Jaccard >= 0.6; 30 of them had Jaccard >= 0.9, and all 30 were identical boilerplate
descriptions. Most of those reduce to 2 tokens, which score Jaccard 1.0 without being duplicates
(Jev probabilities mostly 0.5-0.6; only 2 of the 31 pairs were confirmed). Keyword overlap now
ignores descriptions with fewer than 5 tokens (`KEYWORD_OVERLAP_MIN_TOKENS`, chosen from that
data: the confirmed pairs had 7 and 10). The same 5-token floor applies to the pairs sent to Jev.

Re-run after the fix: n = 55 pairs judged (55 attempted), 3 positives, 52 negatives. The
F1-maximising threshold was 0.90 (precision 1.0, recall 0.667, F1 0.8); at the current 0.6 it is
precision 0.667, recall 0.667, F1 0.667. The two differ by a single pair among only 3 positives, so
`KEYWORD_OVERLAP_AT` stays at 0.6. On the author's machine the keyword rule now flags 3 pairs
instead of 31, matching Jev's 3.

Keyless overlap is an estimate: treat its list as candidates to review, not confirmed duplicates.
With the token floor it is far less inflated, but descriptions that are short yet distinct can
still be missed or over-flagged. Prefer Jev's verdicts when a key is set. Labels hold real skill
names and stay local, in a file kept outside the repo with mode 600 (`SKILL_ROUTER_OVERLAP_LABELS` overrides the path).

## Privacy

The audit reads names, descriptions, counts and timestamps from files the router already keeps. It
never reads prompt text. Without a key nothing leaves your machine; with one, only the capped
descriptions above are sent. `--out` files are written mode 600.

## How to run

```
python3 scripts/cli.py audit                      # text summary
python3 scripts/cli.py audit --format html --out report.html
python3 scripts/cli.py audit --no-jev             # force keyword judging
python3 scripts/doctor.py                         # includes the one-line advisory
```

Input paths can be overridden with `SKILL_AUDIT_INDEX`, `SKILL_AUDIT_LEARNED`,
`SKILL_AUDIT_USAGE_LOG`, `SKILL_AUDIT_CATALOG`, `SKILL_ROUTER_OUTCOMES_FILE` (outcomes log) and
`SKILL_ROUTER_SKILLS_SH_FILE` (skills.sh cache).

`history_days` is the age of the oldest row in the usage log. It is not proof of continuous
coverage: a log with a gap in the middle still reports the full span.

## Measured listing

The simulated listing is only an estimate (it predicted 154 described skills where Claude Code
described 43). `python3 scripts/cli.py listing measure [--project DIR]` starts one Claude Code
session (about a minute, normal subscription, `SKILL_ROUTER_OFF=1`, 300 s timeout), reads
`/context` and caches the result under `~/.claude/skill_router_cache/listing/` (mode 600, names
stay local). While a measurement for the project is under 7 days old, `audit` shows
"measured: N of M entries described; Skills ~T tokens" and the score's listing term uses the
measured term: `round(20 * actionable_dropped / max(1, actionable_entries) * min(1, (skills_tokens / context_window) / 0.05))`.
Actionable entries have source User, Project or Plugin (claude.ai and Built-in entries cannot be
changed by you); the window factor is 1.0 when the window is unknown. The action reads
"N of M of your skills are listed by name only; the listing uses ~X% of the context window", so the
penalty falls as you archive skills or the listing's share of the window shrinks. `listing show` prints the cached line; the
doctor only reads the cache and never runs the probe.

Measured on 2026-11-02: 43 of 764 entries described; Skills ~9800 tokens; context window 1000000.
