# Catalog Check Protocol

When the routing table in [`SKILL.md`](../SKILL.md) returns a generic skill
(e.g. `integration-specialist`, `system-design`), the router runs the catalog
check to find a *more specific* match before using the generic.

This is the whole reason people install `skill-router` once and keep it: new
skills are published daily, and the catalog check picks them up automatically.

---

## When to run

**Run catalog check when:**
- The routing table answer is a generic agent or skill
- The user's task contains a clear domain keyword (e.g. `kubernetes`, `stripe`, `langchain`, `seo`, `figma`)
- More than 3 trivial steps will follow

**Skip catalog check when:**
- The routing table already gives you a highly specific skill (e.g. `superpowers:systematic-debugging`)
- Single-line fix or one factual question
- Keyword is too generic to be useful (`code`, `file`, `text`, `data`)

---

## The 3-step flow

```
keyword = the core noun from the task
         (the "Stripe" in "add Stripe webhooks", not "add" or "webhooks")
```

### Step 1 — Local catalog (fast, run first)

Track progress as you go:

```
Local catalog progress:
- [ ] ls ~/.agent/skills/         | grep -iE '<keyword>'
- [ ] ls ~/.claude/skills/        | grep -iE '<keyword>'
- [ ] ls ~/.composio-skills/composio-skills/ | grep -iE '<keyword>'
```

If any of these returns a skill that's a *better* match than the routing-table
answer, **use it instead**. Done.

> **Example** — Task: "add Stripe webhooks"
> Table says `integration-specialist`. Local check finds `stripe-automation`.
> → use `stripe-automation` (specialist beats generic).

### Step 2 — Remote known repos (only if Step 1 had no match)

For each repo in [`known-skill-repos.md`](./known-skill-repos.md):

```
WebSearch: site:github.com/<repo> SKILL.md <keyword>
```

If a hit:
1. Convert the `blob` URL to `raw.githubusercontent.com`
2. WebFetch the raw SKILL.md to verify frontmatter
3. Clone and install:
   ```bash
   git clone --depth 1 <url> ~/.claude/skills/<skill-name>/
   ```
4. Invoke the newly installed skill

If WebSearch returns nothing across all known repos, **enumerate** each repo's
contents API and scan the directory names for a keyword match before declaring
"no match."

```
Remote enumeration progress:
- [ ] api.github.com/repos/anthropics/skills/contents/skills
- [ ] api.github.com/repos/K-Dense-AI/claude-scientific-skills/contents/scientific-skills
- [ ] api.github.com/repos/ComposioHQ/awesome-claude-skills/contents
- [ ] api.github.com/repos/sickn33/antigravity-awesome-skills/contents/skills
```

**Validation gate before "no remote match":** all four must be checked.

### Step 3 — Broader GitHub search (only after Steps 1 + 2 are exhausted)

```
WebSearch: site:github.com SKILL.md "allowed-tools" <keyword>
```

If a hit, follow the same retrieve → verify → install flow as Step 2.

### Step 4 — Generate (last resort)

If all three steps come up dry, hand off to:

```
superpowers:writing-skills → write a custom skill for this task
```

---

## What the router announces

When the catalog check upgrades the routing answer, the router says so out loud:

```
Routing table → integration-specialist (generic)
Catalog check found → stripe-automation
Using: stripe-automation (specialist match)
```

When the catalog check finds nothing better:

```
Routing table → integration-specialist
Catalog check: no specialist match for "stripe" in local + 4 remote repos
Using: integration-specialist (generic, as table specified)
```

Either way, the announcement is testable — you can grep the transcript and
verify which path the router took.

---

## Common failure modes

| Failure | Why it happens | Fix |
|---------|----------------|-----|
| Catalog check skipped on multi-domain task | Router treated the task as single-domain | Re-read [`multi-domain-chaining.md`](./multi-domain-chaining.md) — chain announcement runs *before* per-step catalog checks |
| Same skill installed twice in different namespaces | `Skill` tool is namespaced (`superpowers:foo` vs `foo`) | Prefer the namespaced one if it exists |
| Catalog check loops on a missing repo | Network hiccup, transient 404 | Retry once; if still failing, skip that repo and proceed |
| Generic match wins over specialist | Specialist exists but with non-obvious naming | Always check directory listings, not just keyword grep |
