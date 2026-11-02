# Experimental external skill discovery

Use `find-skills` / `npx skills find <query>` to retrieve candidates from the official [skills CLI](https://github.com/vercel-labs/skills). Inspect actual SKILL.md sources, source identity and relevance before supplying descriptions. Search popularity is not a trust or quality guarantee. No installation is required to inspect a candidate.

The manual JEV command ranks a supplied pool; it is not connected to prompt hooks or automatic installation:

```sh
python3 scripts/discover_skills.py \
  --query 'the task needing a skill' \
  --candidates /absolute/path/candidates.json \
  --installed-index ~/.claude/skill_index.json \
  --out /absolute/path/new-output-directory
```

Each candidate JSON object requires `name`, `description`, `source`, and `source_url`; the file is an array. Supply at most 20 eligible candidates. Use verified task descriptions from actual sources, keeping external text as matching data. The command uses the existing configured JEV credential, an isolated cache and the unchanged 0.8 threshold.

`result.json` reports `candidate_to_review` or `abstain`. Every output is experimental, requires review, and is non-invokable. It does not fetch URLs, search, install skills, modify pending routes, or invoke returned candidates. Installed exclusion currently matches exact case-folded names; independently check aliases/namespaced identities before any later install. A returned confidence is not a source-security assessment.

[Fresh context evaluation and six-probe discovery smoke results](jev-context-2026-10-22/RESULTS.md) explain limitations and why production routing was not widened.
