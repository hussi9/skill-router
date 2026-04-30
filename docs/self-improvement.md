[← README](../README.md) · [How it works](./how-it-works.md) · [Customizing](./customizing.md) · [Proof](./proof.md)

# Self-Improvement — How skill-router Gets Better Over Time

skill-router ships with three analysis scripts and a weekly orchestrator. Run them
regularly and the router learns your actual workflow patterns, promotes repeated
chains to named (zero-LLM-cost) chains, and tells you exactly which routing rows
to fix.

**You don't have to do this manually.** Set up the weekly cron once (60 seconds)
and it runs every Monday at 9am.

---

## The feedback loop

Every session, two log files grow:

| File | Written by | Contains |
|---|---|---|
| `~/.claude/skill_router_log.jsonl` | router (via hooks) | chain-start, chain-step, chain-end events |
| `~/.claude/skill_usage.log` | PostToolUse hook | every `Skill()` tool invocation |

The three analysis scripts read these logs and surface three things:

1. **Are announced skills actually being invoked?** (gap = wrong skill, or iron rule failing)
2. **Are chain steps being logged?** (missing steps = model going off-script after announcement)
3. **Which chains have you run 3+ times?** (those should be promoted to named chains)

Promoting to a named chain means the router matches it without LLM triage — faster,
cheaper, and 100% consistent.

---

## The three scripts

### 1. `scripts/learn-from-history.py` — announcement → invocation gap

Joins announced skills (from `skill_router_log.jsonl`) with actual `Skill()` calls
(from `skill_usage.log`) by timestamp proximity.

```
python3 scripts/learn-from-history.py --days 7
python3 scripts/learn-from-history.py --days 30 --verbose
```

Output:
- **Follow rate** — % of announcements that led to a matching Skill call within 2 min
- **Surprise calls** — skills invoked with no prior announcement (router missed those prompts)
- **Tuning suggestions** — which skills to tighten (too many false positives) or broaden
  (router is missing valid triggers)

**If follow rate < 70%:** The router is announcing skills that aren't being used.
Check which skills appear in the "TIGHTEN" list and look at their routing-table rows —
the trigger patterns are probably too broad.

**If surprise calls are high:** The router is missing prompts you actually care about.
The "BROADEN" list names the skills that fired without announcements — add signal
patterns for those to `SKILL.md` or your `SKILL.personal.md`.

---

### 2. `scripts/audit-dispatch.py` — dispatch compliance

Of every announced chain, were all steps logged?

```
python3 scripts/audit-dispatch.py --days 7
python3 scripts/audit-dispatch.py --days 7 --verbose   # per-chain breakdown
```

Output:
- **Compliance score** — 0–100. 80+ is healthy.
- **Full / Partial / Skipped** counts
- Verdict: healthy / partial / broken

**If compliance < 80%:** The model is announcing chains but not following the dispatch
protocol — running steps in-session instead of via `Agent()` as specified. This means
per-step model enforcement is silently broken.

Fix: re-read `references/dispatch-protocol.md`. The most common cause is the model
treating the `▶` lines as decorative rather than as dispatch instructions.

---

### 3. `scripts/learn-chains.py` — repeated chain candidates

Which chains have you run 3+ times? Those are your workflow patterns. Promote them
to named chains and they'll bypass LLM triage entirely on future matches.

```
python3 scripts/learn-chains.py --min 3 --days 30        # dry-run, shows proposals
python3 scripts/learn-chains.py --min 3 --days 30 --apply  # writes to SKILL.personal.md
```

With `--apply`, the script appends proposals to your `SKILL.personal.md` with placeholder
`when:` keywords. **You must edit the `when:` keywords** before they'll fire — the script
can't know what phrase you'll use to trigger the chain. Everything else (steps, models)
is filled in automatically.

Example output (dry-run):
```
Found 2 chain(s) repeated 3+ times in 30 days:

  [5× repeats]  superpowers:brainstorming → superpowers:writing-plans → product-manager + tech-lead
  [3× repeats]  superpowers:writing-plans → frontend-design:frontend-design + db-expert
```

After `--apply`, open `SKILL.personal.md` and fill in the `when:` keywords:
```yaml
chains:
  - name: auto-brainstorming-to-tech-lead
    when: ["research the idea", "is this worth building", "plan it out"]
    chain: superpowers:brainstorming → superpowers:writing-plans → product-manager + tech-lead
```

Once `when:` is filled, the next time you type a prompt containing any of those phrases,
the router skips LLM triage and uses this chain directly. Faster, cheaper, consistent.

---

## Run all three at once

`scripts/weekly-analysis.sh` is the orchestrator. It runs all three scripts in sequence
and appends a one-line summary to `~/.claude/skill_router_weekly.log`.

```bash
# Dry-run (safe, no writes):
bash scripts/weekly-analysis.sh

# Promote repeated chains automatically:
bash scripts/weekly-analysis.sh --apply
```

---

## Automate it — set up the weekly cron

### macOS (launchd) — recommended

One-time setup, runs every Monday at 9am:

```bash
# 1. Substitute your paths into the template
REPO="$HOME/path/to/skill-router"    # wherever you cloned the repo
sed \
  -e "s|{{SKILL_ROUTER_PATH}}|$REPO|g" \
  -e "s|{{HOME}}|$HOME|g" \
  "$REPO/setup/launchd-weekly.plist" \
  > ~/Library/LaunchAgents/com.skill-router.weekly-analysis.plist

# 2. Load it
launchctl load ~/Library/LaunchAgents/com.skill-router.weekly-analysis.plist

# 3. Verify
launchctl list | grep skill-router
# Should print a line with com.skill-router.weekly-analysis
```

To run it right now without waiting for Monday:
```bash
launchctl start com.skill-router.weekly-analysis
```

Output goes to: `~/.claude/skill_router_weekly.log`

To stop / unload:
```bash
launchctl unload ~/Library/LaunchAgents/com.skill-router.weekly-analysis.plist
```

---

### Linux (crontab)

```bash
crontab -e
```

Add this line (runs every Monday at 9am):
```
0 9 * * 1 /bin/bash /path/to/skill-router/scripts/weekly-analysis.sh >> ~/.claude/skill_router_weekly.log 2>&1
```

---

## Reading the weekly log

`~/.claude/skill_router_weekly.log` is append-only. Each run adds:

1. The full output of all three scripts (timestamped header)
2. A one-line summary entry at the end

You can tail it to see the most recent run:
```bash
tail -100 ~/.claude/skill_router_weekly.log
```

---

## What to do when numbers are bad

| Metric | Bad signal | Fix |
|---|---|---|
| Follow rate < 70% | Announced skills ignored | Tighten trigger patterns in routing table for the flagged skills |
| Surprise calls high | Router missing valid prompts | Add signal patterns to `SKILL.md` for the surprise skills |
| Compliance < 80% | Steps announced but not logged | Review `references/dispatch-protocol.md`; check `remaining` list in hooks |
| Ghost-skill guard fires repeatedly | Routing table has dead skills | Run `learn-from-history.py` and remove skills that are never invoked |
| Named chain candidates growing | Same chains recomputed weekly | Run `--apply` and fill in `when:` keywords in `SKILL.personal.md` |

---

## FAQ

**I just installed skill-router — will these scripts work?**
They need data to analyse. Use the router for a few real Claude Code sessions first
(anything that triggers `[skill-router]` output). A week of normal use is enough to
get meaningful signal.

**The scripts say "no log entries" — why?**
Either the hooks aren't writing to the log files, or you haven't run any tasks that
triggered the router. Check that `~/.claude/skill_router_log.jsonl` exists and has
content: `wc -l ~/.claude/skill_router_log.jsonl`. If it's empty, check your hooks
in `~/.claude/settings.json`.

**learn-chains.py says "no chains fired 3+ times" — should I lower --min?**
You can (`--min 2`), but chains that only fired twice are weak signal. Better to wait
until you have more data. Use the router actively for 2–3 weeks before running this.

**learn-chains.py --apply wrote to SKILL.personal.md but the chain isn't firing.**
You need to edit the `when:` keywords the script left as placeholders. Open
`SKILL.personal.md` and replace `<add a keyword that triggers this>` with the
actual phrases you use when you want this chain. Without `when:` keywords, the
named chain never matches.

**Can I run the scripts more often than weekly?**
Yes. They're read-only (except `--apply`). Running them daily is fine. The weekly
schedule is just a sensible default — enough time for signal to accumulate, not
so long that problems sit unfixed.

**What's the difference between the weekly log and the JSONL log?**
`skill_router_log.jsonl` is the raw event stream — every chain start, step, and end.
`skill_router_weekly.log` is a human-readable digest that gets appended each time
`weekly-analysis.sh` runs. The JSONL is the source of truth; the weekly log is your
report.

**Will SKILL.personal.md changes be overwritten when I update skill-router?**
No. `SKILL.personal.md` lives in `~/.claude/skills/skill-router/` on your machine
and is never touched by the repo. `scripts/learn-chains.py --apply` only appends,
never overwrites.
