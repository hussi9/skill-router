# skills.sh suggestions

[skills.sh](https://skills.sh) is the public registry behind `npx skills`. skill-router reads its leaderboards
into a local cache and uses it for one thing: when **nothing installed fits** a prompt, it points at a popular,
audit-screened skill that does. It never installs and never adds anything to the routing index.

```
[skill-router] No installed skill matches, but `agent-browser` from online:skills.sh (1,332,513 installs · audit: clean) might fit.
[skill-router] Install: npx skills add vercel-labs/agent-browser --skill agent-browser
[skill-router] (Skipped — soft suggestion only, no enforcement.)
```

## Refresh the cache

```bash
python3 scripts/skills_sh_catalog.py                  # refresh if older than a day
python3 scripts/skills_sh_catalog.py --force --report # rebuild + write docs/skills-sh-top.md
python3 scripts/skills_sh_catalog.py --refine         # let Jev file 'other' skills (needs TYPESAFE_API_KEY)
python3 scripts/skills_sh_catalog.py --offline        # rebuild from cached pages, no network
```

It is **opt-in**: nothing refreshes it automatically. Add the first line to cron or a SessionStart hook if you
want it daily. Output: `~/.claude/skill_router_skills_sh.json` (mode 600).

## What it reads, and how politely

| Page | Gives |
|---|---|
| `/`, `/trending`, `/hot` | up to 150 skills each: installs, 8-week series, official flag |
| `/audits` | Agent Trust Hub, Socket and Snyk verdicts per skill |
| `/<owner>/<repo>/<skill>` | the skill's description (cached 7 days) |

- HTML pages only. `robots.txt` disallows `/api/` for crawlers, so the cache never calls it (the explicit,
  user-triggered `find_skills.py` search is separate and unchanged).
- One request at a time, 0.4 s apart, ≤ 1 MB each, ≤ 400 skill pages per run. A re-run inside the cache window
  makes no skill-page requests. Redirects are followed only within skills.sh.
- No prompt, path or identifier is sent. Only public URLs are requested.

## What gets suggested

1. **Flagged skills are dropped.** One audit partner reporting a high/critical/malicious result removes the skill.
   `clean` means at least one partner passed it and none objected; most skills are `unaudited` and the card says so.
   The build **fails closed**: if the audits page cannot be read, nothing is written and the previous screened
   cache stays. Set `SKILL_ROUTER_REQUIRE_CLEAN=1` to suggest only skills an audit partner explicitly passed
   (a much smaller set; most skills are unaudited).
2. **A description is required.** Name-only matching is noise.
3. **Match, then popularity.** The existing token-overlap gate decides if anything is suggested; installs only break
   ties between equally good matches. A popular skill never outranks a better fit.
4. Skills you already have are excluded by name.

Install counts measure adoption, not quality. Read the source before installing; the card never runs the command.

## Categories and the top-by-category report

`docs/skills-sh-top.md` lists the top five per category, ordered by installs, nudged by 4-week momentum, official
status and a clean audit. Categories come from explicit keyword rules (`CATEGORIES` in the script): whole-word starts,
the skill's name counting most. Skills the keywords can't place go to `other`; `--refine` asks Jev one Choice
question per such skill (public name + description only), accepted at confidence ≥ 0.8 like every Jev decision here.

## Limits

- The leaderboards cover the top few hundred skills; long-tail skills are only reachable through `find_skills.py`.
- Audit coverage is partial. `unaudited` is not `safe`.
- Categories are a heuristic. The report says so.
