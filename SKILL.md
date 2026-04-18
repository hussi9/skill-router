---
name: skills-master
description: INVOKE BEFORE EVERY NON-TRIVIAL TASK — before writing code, before using any tool, before answering. Do not skip. Produces the required Skill + Agent + Model for the task. Routing engine for 2,700+ skills.
---

# Skills Master — Universal Router

**Output always:** `Skill + Agent + Model`

---

## THE 3-QUESTION TRIAGE (run now, takes 5 seconds)

```
Q1: Is something BROKEN / WRONG / FAILING?
    Error, crash, test fail, unexpected output, user correction
    YES → BROKEN PATH

Q2: Is this CREATE / BUILD / ADD something new?
    New feature, file, component, integration, page, script
    YES → BUILD PATH

Q3: Everything else (improve, ship, configure, automate, research)
    → OPERATE PATH

AMBIGUOUS? → Default to HIGHER-COMPLEXITY path
```

---

## BROKEN PATH

| Signal | Skill | Agent | Model |
|--------|-------|-------|-------|
| Error / crash / exception | `superpowers:systematic-debugging` | general-purpose | sonnet |
| Test failing | `test-runner` → `superpowers:systematic-debugging` | test-runner | sonnet |
| TypeScript errors | `typescript-expert` | general-purpose | sonnet |
| Performance regression | `perf` → `superpowers:systematic-debugging` | optimizer | sonnet |
| Security issue found | `security` | security-auditor | sonnet |
| Deploy / build failed | `superpowers:systematic-debugging` | general-purpose | sonnet |
| User says "no" / "wrong" | STOP → `superpowers:systematic-debugging` | general-purpose | sonnet |
| Production incident | `superpowers:systematic-debugging` | general-purpose | **opus** |

---

## BUILD PATH

**Multi-file / new feature:** `brainstorming` → `writing-plans` → domain skill
**Single file / trivial add:** go directly to domain skill

| What | Skill | Agent | Model |
|------|-------|-------|-------|
| UI component / page | `frontend-design:frontend-design` | feature-dev:code-architect | sonnet |
| API endpoint | `system-design` | feature-dev:code-architect | sonnet |
| Database schema | `db-expert` | db-expert | sonnet |
| Auth / permissions | `brainstorming` → `security` | security-auditor | opus |
| AI feature / agent | `langgraph` → `rag-engineer` | feature-dev:code-architect | sonnet |
| 3rd-party integration | composio skill for that app | integration-specialist | sonnet |
| Mobile screen | `mobile-developer` → `frontend-design:frontend-design` | feature-dev:code-architect | sonnet |
| CLI / automation script | `system-design` | general-purpose | sonnet |
| Skill / Claude skill file | `superpowers:writing-skills` | general-purpose | sonnet |

---

## OPERATE PATH

| Signal | Skill | Agent | Model |
|--------|-------|-------|-------|
| Refactor / clean up | `refactor` | code-simplifier:code-simplifier | sonnet |
| Add tests / coverage | `superpowers:test-driven-development` | test-runner | sonnet |
| Performance optimize | `perf` | optimizer | sonnet |
| Write docs | `docs` | general-purpose | sonnet |
| Code review | `superpowers:requesting-code-review` | superpowers:code-reviewer | sonnet |
| Got review feedback | `superpowers:receiving-code-review` | general-purpose | sonnet |
| Deploy | `superpowers:verification-before-completion` → `vercel:deploy` | general-purpose | sonnet |
| Merge / PR / push | `superpowers:finishing-a-development-branch` | general-purpose | sonnet |
| DB migration | `db-expert` | db-expert | sonnet |
| 2+ independent tasks | `superpowers:dispatching-parallel-agents` | general-purpose | sonnet |
| Resume previous work | `superpowers:executing-plans` | general-purpose | sonnet |
| Research / docs lookup | context7 → `brainstorming` | general-purpose | sonnet |

---

## WHEN NO SKILL IS NEEDED

Single-line fix · reading code · one factual question · one command · under 3 trivial steps

---

## COMPLETION GATE

Before any "done" claim → `superpowers:verification-before-completion`

```
□ Code actually runs correctly
□ TypeScript passes (tsc --noEmit)
□ Tests pass
□ Original request fully met (re-read it)
```

---

## COMPLEXITY RULE

```
1 domain  → 1 skill
2+ domains → announce chain, run in order

"This touches [N] domains. Chain: [skill1] → [skill2] → ...
Invoking step 1 now."
```

---

## CATALOG CHECK — Always Run After Triage (Key Differentiator)

**After routing to a skill from the tables above, check for a more specific match:**

```
keyword = core noun from the task (e.g., "kubernetes", "stripe", "threejs", "langchain")

Step 1 — LOCAL CATALOG (fast, run first):
  ls ~/.agent/skills/ | grep -iE '<keyword>'      ← 1,400+ Antigravity skills
  ls ~/.claude/skills/ | grep -iE '<keyword>'     ← your installed custom skills
  ls ~/.composio-skills/composio-skills/ | grep -iE '<keyword>'  ← 940+ integrations

  If a more specific match exists → USE IT instead of the generic routing table entry.
  Example: task is "add Stripe webhooks" → table says "integration-specialist"
           but ls finds "stripe-automation" → use stripe-automation instead.

Step 2 — ONLINE CATALOG (run if Step 1 has no match):
  WebSearch: site:github.com "SKILL.md" claude <keyword>
  → If a repo with SKILL.md exists:
    git clone --depth 1 <url> ~/.claude/skills/<skill-name>/
    Then invoke the newly installed skill.

Step 3 — GENERATE (last resort):
  superpowers:writing-skills → write a custom skill for this task
```

**When to skip the catalog check:**
- The routing table already gives you a highly specific skill (e.g., `systematic-debugging`)
- Single-line fix or trivial command
- The keyword is too generic to produce useful results (e.g., "code", "file", "text")

---

## PERSONAL OVERRIDES

Add project-specific routing on top of this file:

```bash
curl -sL https://raw.githubusercontent.com/hussi9/skills-master/main/SKILL.personal.md \
  > ~/.claude/skills/skills-master/SKILL.personal.md
```

Edit `SKILL.personal.md` with your project signals. Your rules win over the core (CSS cascade model).

---

## RED FLAGS — Signs You're About to Skip This

```
"This is simple"          → Simple things take 5s to route. Skip routing = hours wasted.
"I know what to do"       → Then routing confirms it. 5s cost, 0 downside.
"No match in table"       → Run Catalog Check above before giving up.
"Ambiguous task"          → Default to higher-complexity path (BUILD).
"I already know the skill" → Still run catalog check — a better one may exist.
```
