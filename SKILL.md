---
name: skill-router
description: INVOKE BEFORE EVERY NON-TRIVIAL TASK — before writing code, before using any tool, before answering. Do not skip. Produces the required Skill + Agent + Model for the task. Routing engine for 2,700+ skills.
---

# Skill Router — Universal Router

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

| Signal | Skill | Agent | Model | Thinking |
|--------|-------|-------|-------|----------|
| Error / crash / exception | `superpowers:systematic-debugging` | general-purpose | sonnet | think |
| Test failing | `test-runner` → `superpowers:systematic-debugging` | test-runner | sonnet | none |
| TypeScript errors | `typescript-expert` | general-purpose | sonnet | none |
| Performance regression | `perf` → `superpowers:systematic-debugging` | optimizer | sonnet | think |
| Security issue found | `security` | security-auditor | sonnet | think-hard |
| Deploy / build failed | `superpowers:systematic-debugging` | general-purpose | sonnet | think |
| User says "no" / "wrong" | STOP → `superpowers:systematic-debugging` | general-purpose | sonnet | think |
| Production incident | `superpowers:systematic-debugging` | general-purpose | **opus** | **ultrathink** |

---

## BUILD PATH

**Multi-file / new feature:** `brainstorming` → `writing-plans` → domain skill
**Single file / trivial add:** go directly to domain skill

| What | Skill | Agent | Model | Thinking |
|------|-------|-------|-------|----------|
| UI component / page | `frontend-design:frontend-design` | feature-dev:code-architect | sonnet | none |
| API endpoint | `system-design` | feature-dev:code-architect | sonnet | think |
| Database schema | `db-expert` | db-expert | sonnet | think |
| Auth / permissions | `brainstorming` → `security` | security-auditor | opus | **ultrathink** |
| AI feature / agent | `langgraph` → `rag-engineer` | feature-dev:code-architect | sonnet | think-hard |
| 3rd-party integration | composio skill for that app | integration-specialist | sonnet | none |
| Mobile screen | `mobile-developer` → `frontend-design:frontend-design` | feature-dev:code-architect | sonnet | none |
| CLI / automation script | `system-design` | general-purpose | sonnet | think |
| Skill / Claude skill file | `superpowers:writing-skills` | general-purpose | sonnet | think |

---

## OPERATE PATH

| Signal | Skill | Agent | Model | Thinking |
|--------|-------|-------|-------|----------|
| Refactor / clean up | `refactor` | code-simplifier:code-simplifier | sonnet | none |
| Add tests / coverage | `superpowers:test-driven-development` | test-runner | sonnet | none |
| Performance optimize | `perf` | optimizer | sonnet | think |
| Write docs | `docs` | general-purpose | sonnet | none |
| Code review | `superpowers:requesting-code-review` | superpowers:code-reviewer | sonnet | think-hard |
| Got review feedback | `superpowers:receiving-code-review` | general-purpose | sonnet | think |
| Deploy | `superpowers:verification-before-completion` → `vercel:deploy` | general-purpose | sonnet | none |
| Merge / PR / push | `superpowers:finishing-a-development-branch` | general-purpose | sonnet | none |
| DB migration | `db-expert` | db-expert | sonnet | think |
| 2+ independent tasks | `superpowers:dispatching-parallel-agents` | general-purpose | sonnet | none |
| Resume previous work | `superpowers:executing-plans` | general-purpose | sonnet | none |
| Research / docs lookup | context7 → `brainstorming` | general-purpose | sonnet | none |
| Architecture / scope decision | `brainstorming` → `system-design` | general-purpose | opus | **ultrathink** |

---

## WHEN NO SKILL IS NEEDED

Single-line fix · reading code · one factual question · one command · under 3 trivial steps

**Router precision contract:** when no triage signal matches (BROKEN/BUILD/OPERATE), the
router emits **nothing** — silence is the correct output. A missing `[skill-router]` line
means the prompt was conversational, exploratory, or trivial. Do not interpret silence as
"OPERATE → refactor" by default; the router only suggests when it is confident.

**Iron rule enforcement:** when the router *does* announce a chain, the announcement
includes an `IRON RULE` block naming the next required `Skill(skill="<X>")` call. A
`PreToolUse` hook denies state-changing tools (`Bash`, `Edit`, `Write`, `Task`) until that
skill is invoked, and a `Stop` hook blocks turn end if it never was. Read/Glob/Grep/
TodoWrite remain allowed so context-gathering still works.

**Escape hatch:** include `[no-router]` in your prompt to disable enforcement for that
turn. The router stays silent and writes no pending state, so all hooks pass through.

**Calibration:** `tests/calibration.py` runs ~100 curated prompts through the router and
reports precision/recall/F1 by triage path. Run with `--min-accuracy N` for a CI gate.
Current baseline: **100% path accuracy, 95.2% skill accuracy**.

**Learning loop:** `scripts/learn-from-history.py` joins announcement events
(`~/.claude/skill_router_log.jsonl`) with actual Skill invocations (`~/.claude/
skill_usage.log`) and surfaces tuning suggestions — which announced skills are ignored
most often, and which Skill invocations the router missed. Run periodically to keep
patterns calibrated to real usage.

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
1 domain  → 1 skill         → single-domain announcement
2+ domains → announce chain   → multi-domain announcement
```

Operators in the chain:
- `→` sequential (B depends on A)
- `+` parallel (steps don't share state)

Full chain syntax + standard shapes: see [`references/multi-domain-chaining.md`](./references/multi-domain-chaining.md).

---

## ANNOUNCEMENT FORMAT — Output VERBATIM (substitute only `<vars>`)

The announcement is the testable contract. Users will `grep '\[skill-router\]'`
their transcript to verify what fired matches what was announced. Format is
non-negotiable. Output it BEFORE any other tool call (Read, Edit, Bash, Agent).

**Single-domain (one skill, no chain):**
```
[skill-router] This is a <BROKEN|BUILD|OPERATE> task → <skill> → <agent>.
[skill-router] Model: <model>  ·  Thinking: <thinking>
[skill-router] Invoke now:

▶ <skill>  (<model>, <in-session | via Agent>)
```

**Multi-domain (computed chain):**
```
[skill-router] This touches <N> domains: <d1>, <d2>, <d3>.
[skill-router] Chain: <s1> → <s2> + <s3> → <s4>
[skill-router] Models: <m1> · <m2>+<m3> · <m4>  ·  Thinking: <max-thinking>
[skill-router] Invoke step 1/<N> now:

▶ <s1>  (<m1>, <in-session | via Agent>)
▶ <s2> + <s3>  (<m2>, parallel via Agent)
▶ <s4>  (<m4>, <in-session | via Agent>)
```

**Multi-domain (saved chain wins):**
```
[skill-router] Using your saved chain `<name>`: <s1> → <s2> + <s3>
[skill-router] Models: <m1> · <m2>+<m3>  ·  Thinking: <max-thinking>
[skill-router] Invoke step 1/<N> now:

▶ <s1>  (<m1>, <in-session | via Agent>)
▶ <s2> + <s3>  (<m2>, parallel via Agent)
```

Rules:
- `Models:` line uses `·` between sequential steps and `+` inside one parallel step.
- `Thinking:` is the highest depth of any step (`none` / `think` / `think-hard` / `ultrathink`). Omit the field when every step is `none`.
- Each `▶` line ends with one of: `in-session`, `via Agent`, or `parallel via Agent` — matching the dispatch protocol decision below.
- After each step completes, output `[skill-router] Step <n>/<N> done.` before dispatching the next.
- On chain end, output `[skill-router] Chain done.`

Two layers of testability — keep them aligned:
- **`[skill-router]` lines + `▶` markers** are the *human-readable* proof in the transcript. `grep '\[skill-router\]'` shows what was announced; the `▶` line shows the dispatch decision the parent made.
- **`~/.claude/skill_router_log.jsonl`** events (`chain-start`, `chain-step`, `thinking-active`, `chain-end`) are the *machine-readable* proof read by `scripts/audit-dispatch.py` and the statusline. The `▶` line does NOT replace the JSONL `chain-step` event — write both. See [`references/dispatch-protocol.md`](./references/dispatch-protocol.md).

Skipping the announcement format = silently breaking the testability claim.

---

## NAMED CHAIN LOOKUP — Run Before Computing Fresh

After triage, BEFORE computing fresh, check `SKILL.personal.md` for a
`chains:` block. If any `when:` substring matches the user's prompt
(case-insensitive, first match wins), use the saved chain instead.

When a saved chain wins, announce it with provenance:
```
Using your saved chain `<name>`: <step1> → <step2> + <step3>
```

Schema, match algorithm, per-step model resolution: [`references/named-chains.md`](./references/named-chains.md).

---

## CATALOG CHECK — Run After Routing-Table Lookup (Key Differentiator)

If the table returned a generic skill (e.g. `integration-specialist`),
search local + remote catalogs for a more specific match. If found, use
the specialist instead.

```
1. Local: ls ~/.agent/skills/, ~/.claude/skills/, ~/.composio-skills/  | grep -iE '<keyword>'
2. Remote: site:github.com "SKILL.md" claude <keyword>  (4 curated repos in ref doc)
3. Generate: superpowers:writing-skills (last resort)
```

Skip when: routing-table answer is already specialist · single-line fix ·
keyword is too generic ("code", "file", "text").

Full validation gates + curated repos: [`references/catalog-check.md`](./references/catalog-check.md), [`references/known-skill-repos.md`](./references/known-skill-repos.md).

---

## PERSONAL OVERRIDES

Add project-specific routing on top of this file:

```bash
curl -sL https://raw.githubusercontent.com/hussi9/skill-router/main/SKILL.personal.md \
  > ~/.claude/skills/skill-router/SKILL.personal.md
```

Edit `SKILL.personal.md` with your project signals. Your rules win over the core (CSS cascade model).

---

## THINKING DEPTH — Pre-pend the Right Keyword

Pre-pend the routing row's `Thinking` value as the literal first word of the
dispatch prompt:

| Value | Pre-pend |
|-------|----------|
| `none` | (nothing) |
| `think` | `think.` |
| `think-hard` | `think hard.` |
| `ultrathink` | `ultrathink.` |

Do not paraphrase. If a community `ultrathink` / `think-hard` skill is
installed, invoke that skill INSTEAD of the bare keyword.

Full rules + community alternatives: [`references/thinking-depth.md`](./references/thinking-depth.md).

---

## DISPATCH PROTOCOL — How Each Step Actually Runs

This is what makes the **Model** column enforced, not advisory.

```
For each step in the announced chain:
  IF step.model == parent_model AND not parallel-fan-out:
      → Skill(<skill>) in-session   (cheaper, same context)
  ELSE:
      → Agent(subagent_type=<agent>, model=<model>,
              prompt="<thinking-keyword>. Use Skill: <skill>. Task: <slice>. Context: <files>")
  Sequential `→`: wait. Parallel `+`: one message, multiple Agent calls.
```

After dispatching, write log lines to `~/.claude/skill_router_log.jsonl`
(`chain-start`, `chain-step`, `thinking-active`, `chain-end`) for the
statusline + the `scripts/audit-dispatch.py` compliance auditor.

Full event schema, common skip patterns, and verification: [`references/dispatch-protocol.md`](./references/dispatch-protocol.md).

---

## RED FLAGS — Signs You're About to Skip This

```
"This is simple"            → Simple things take 5s to route. Skip routing = hours wasted.
"I know what to do"         → Then routing confirms it. 5s cost, 0 downside.
"No match in table"         → Run Catalog Check above before giving up.
"Ambiguous task"            → Default to higher-complexity path (BUILD).
"I already know the skill"  → Still run catalog check — a better one may exist.
"I'll just paraphrase"      → No. Output the [skill-router] format VERBATIM. Greppability is the contract.
"I can skip the ▶ lines"    → No. Per-step ▶ lines are the dispatch-mode proof. Without them the audit script can't score the chain.
```
