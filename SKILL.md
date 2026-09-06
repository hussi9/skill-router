---
name: skill-router
description: INVOKE BEFORE EVERY NON-TRIVIAL TASK — before writing code, before using any tool, before answering. Do not skip. Produces the required Skill + Agent + Model for the task. Routing engine for 400+ installed skills.
---

# Skill Router

**Output always:** `Skill + Agent + Model`

Routing runs automatically. A `UserPromptSubmit` hook classifies every prompt
and injects a `[skill-router]` announcement before your first action, so most
of the time your job is to *follow* an announcement, not compute one. This file
is the contract that announcement is written against, and what you fall back on
when the hook is silent.

**First, is it healthy?** If you have not seen a `[skill-router]` line in a
while, routing may be dead rather than quiet:

```bash
python3 ~/.claude/skills/skill-router/scripts/doctor.py
```

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

Before the table, two layers get first refusal:

1. **Project routes** — `SKILL.personal.md` maps names that mean exactly one
   project (`@economicalai`, `testflight`, `jobhunt`) straight to that
   project's skill. Generic triage cannot tell one project from another; this
   is the only layer that can.
2. **The routing table** below.

---

## BROKEN PATH

| Signal | Skill | Agent | Thinking |
|--------|-------|-------|----------|
| Error / crash / exception | `superpowers:systematic-debugging` | general-purpose | think |
| Test failing | `superpowers:systematic-debugging` | test-runner | think |
| Performance regression | `perf` | optimizer | think |
| Security issue found | `security` | security-auditor | think-hard |
| Deploy / build failed | `superpowers:systematic-debugging` | general-purpose | think |
| User says "no" / "wrong" | STOP → `superpowers:systematic-debugging` | general-purpose | think |
| Production incident | `superpowers:systematic-debugging` | general-purpose | **ultrathink** |

---

## BUILD PATH

**Multi-file / new feature:** `writing-plans` → domain skill
**Single file / trivial add:** go directly to the domain skill

| What | Skill | Agent | Thinking |
|------|-------|-------|----------|
| UI component / page | `frontend-design:frontend-design` | feature-dev:code-architect | none |
| API endpoint | `feature-dev:feature-dev` | feature-dev:code-architect | think |
| Database schema | `supabase:supabase` | db-expert | think |
| Auth / permissions | `security` | security-auditor | **ultrathink** |
| AI / RAG / agent feature | `superpowers:writing-plans` | feature-dev:code-architect | think-hard |
| 3rd-party integration | `connect-apps` | integration-specialist | none |
| Mobile screen | `frontend-design:frontend-design` | feature-dev:code-architect | none |
| CLI / automation script | `superpowers:writing-plans` | general-purpose | think |
| Skill / Claude skill file | `superpowers:writing-skills` | general-purpose | think |

---

## OPERATE PATH

| Signal | Skill | Agent | Thinking |
|--------|-------|-------|----------|
| Refactor / clean up | `refactor` | code-simplifier:code-simplifier | none |
| Add tests / coverage | `superpowers:test-driven-development` | test-runner | none |
| Code review | `superpowers:requesting-code-review` | code-reviewer | think-hard |
| Got review feedback | `superpowers:receiving-code-review` | general-purpose | think |
| Deploy | `superpowers:verification-before-completion` → `vercel:deploy` | general-purpose | none |
| Merge / PR / push | `superpowers:finishing-a-development-branch` | general-purpose | none |
| 2+ independent tasks | `superpowers:dispatching-parallel-agents` | general-purpose | none |
| Resume previous work | `superpowers:executing-plans` | general-purpose | none |
| Architecture / scope call | `superpowers:writing-plans` | general-purpose | **ultrathink** |

---

## THE MODEL COLUMN IS `inherit`

Steps run **in this session, at this session's model**, unless there is a
reason to do otherwise. There are two:

| Value | Meaning | When |
|---|---|---|
| `inherit` | run in-session at the parent's model | the default, nearly always |
| `haiku` | dispatch to a sub-agent on the cheap model | bulk read-only scans whose output is a list of paths, not a judgment |

The table used to name `sonnet` on most rows and `opus` on hard ones. That was
written when the parent was always Sonnet, so `sonnet` silently meant
`inherit`. It stopped meaning that — and because the dispatch protocol reads
"step model ≠ parent model" as "fan out to a sub-agent", every routed step on a
Fable or Opus session was being shipped to a *weaker* model than the user chose.

Depth now comes from `Thinking`, which composes with any model. Never
reintroduce a frontier model name into the table.

**The same rule applies to agent frontmatter.** `model: sonnet` in an agent
file wins over the session, so dispatching to it from an Opus or Fable session
buys sub-agent overhead at lower capability. Measured on 2.1.263 from an Opus
parent: a pinned dispatch used `claude-opus-5` *and* `claude-sonnet-5`; an
`inherit` dispatch used only `claude-opus-5`. `doctor.py` checks this;
`scripts/fix_agent_models.py` fixes it. `haiku` is exempt — the one deliberate
downgrade.

---

## SPECIALIST LAYER — 400 skills, not 20

The tables above name ~20 process skills. This machine has ~395 invokable ones,
and the rest are the domain specialists that make a task go faster. They cannot
be enumerated in a table — new skills land weekly — so the router ranks them at
query time and appends one advisory line:

```
[skill-router] Specialist available: seo-technical — Technical SEO audit across 9 categories…
```

Advisory, never enforced. Load it alongside the announced step when it fits;
ignore it when it doesn't. When no specialist clears the confidence bar, the
line is absent — which is the common case and the correct one.

```bash
# what would match, and why
python3 ~/.claude/skills/skill-router/scripts/catalog_match.py "your prompt"
```

The catalog rebuilds at every `SessionStart`, so a skill you install today is
routable today. Non-invokable skills (`~/.agent/skills`, `~/.composio-skills`)
are catalogued but never announced — they can only be suggested for install.

---

## SUB-AGENTS GET ROUTED TOO

A `SubagentStart` hook injects the same kind of brief into every dispatched
sub-agent, naming the skills that fit that agent's job:

```
[skill-router] Skills for seo-technical: Skill(skill="seo-technical"), Skill(skill="seo")
```

Pairings come from `agent_skills.json` when hand-set, and are otherwise derived
by ranking the agent's own description against the catalog — so a new agent
gets sensible skills with no registration step. Generic agents
(`general-purpose`, `Explore`, `researcher`) get nothing, because they have no
domain and any suggestion would be noise.

The hook is the mechanism, deliberately. Agent frontmatter also documents a
`skills:` field that preloads full skill text at startup; on Claude Code
2.1.263 it was tested here and the content did not arrive in the sub-agent's
context, while the hook brief did — verified by asking a dispatched agent what
it could see. Revisit `skills:` when a release notes a fix; until then the hook
is the only path that demonstrably works.

**The IRON RULE does not apply inside sub-agents.** The parent's pending skill
belongs to the parent's turn and a sub-agent cannot satisfy it, so enforcement
stands down at the boundary. Sub-agents are told what to use; they are never
blocked.

When you dispatch an agent yourself, name the skill in the prompt:

```
Agent(subagent_type="db-expert",
      prompt="think. Use Skill: supabase:supabase. Task: … Context: …")
```

---

## WHEN NO SKILL IS NEEDED

Single-line fix · reading code · one factual question · one command · under 3
trivial steps.

**Silence is an answer.** When no triage signal matches, the router emits
nothing. A missing `[skill-router]` line means the prompt was conversational,
exploratory, or trivial — not that you should invent a route.

**Explicit slash-commands stand down.** If the prompt is itself an invocation
(`/gstack`, `/ship prod`), the user already chose. Never reclassify it.

---

## IRON RULE

When the router announces, a `PreToolUse` hook denies `Edit`/`Write`/`Task`/
`NotebookEdit`/`MultiEdit` until that skill is invoked, and a `Stop` hook blocks
turn end if it never was. `Read`/`Glob`/`Grep`/`Bash`/`TodoWrite`/`Skill` stay
allowed, so context-gathering, shell work, and the override below keep working.

**Two escape hatches:**

1. **User opt-out** — the *user* includes `[no-router]` in their next message.
   Writing it in your own response does nothing.
2. **Reasoned override** — if you judge the route wrong, don't fight the rule:

   ```bash
   python3 ~/.claude/skills/skill-router/scripts/router_override.py "<reason>"
   ```

   This clears the rule for the turn, logs your reason, and counts against that
   skill. Past `OVERRIDE_THRESHOLD` the router defers it — **for
   `DEFER_TTL_DAYS`, then it comes back**. Demotions expire on purpose:
   permanent ones once killed five core skills here and left the router
   answering SKIP to everything while looking perfectly healthy.

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

## ANNOUNCEMENT FORMAT — output VERBATIM (substitute only `<vars>`)

The announcement is the testable contract: `grep '\[skill-router\]'` shows what
fired. When the hook already produced one, follow it — do not reprint it.

**Single-domain:**
```
[skill-router] This is a <BROKEN|BUILD|OPERATE> task → <skill> → <agent>.
[skill-router] Model: <in-session|haiku>  ·  Thinking: <thinking>
[skill-router] Invoke now:

▶ <skill>  (<inherit|model>, <in-session | via Agent>)
```

**Multi-domain:**
```
[skill-router] This touches <N> domains: <d1>, <d2>.
[skill-router] Chain: <s1> → <s2> + <s3>
[skill-router] Models: inherit (this session)  ·  Thinking: <max-thinking>
[skill-router] Invoke step 1/<N> now:

▶ <s1>  (inherit, in-session)
▶ <s2> + <s3>  (inherit, parallel via Agent)
```

Rules:
- `Thinking:` is the deepest of any step. Omit when every step is `none`.
- Each `▶` line ends with `in-session`, `via Agent`, or `parallel via Agent`.
- After each step: `[skill-router] Step <n>/<N> done.` On completion:
  `[skill-router] Chain done.`

---

## DISPATCH PROTOCOL

```
For each step:
  IF step.model == "inherit" AND not a parallel fan-out:
      → Skill(<skill>) in-session        (same context, no dispatch cost)
  ELSE:
      → Agent(subagent_type=<agent>, model=<model>,
              prompt="<thinking-keyword>. Use Skill: <skill>. Task: <slice>. Context: <files>")
  Sequential `→`: wait. Parallel `+`: one message, multiple Agent calls.
```

Parallel steps go through `Agent` even at `inherit` — not for the model, but
because they need independent contexts to run at once.

Full event schema and verification: [`references/dispatch-protocol.md`](./references/dispatch-protocol.md).

---

## THINKING DEPTH

Pre-pend the row's `Thinking` value as the literal first word of the dispatch
prompt. Do not paraphrase.

| Value | Pre-pend |
|-------|----------|
| `none` | (nothing) |
| `think` | `think.` |
| `think-hard` | `think hard.` |
| `ultrathink` | `ultrathink.` |

Full rules: [`references/thinking-depth.md`](./references/thinking-depth.md).

---

## COMPLEXITY RULE

```
1 domain   → 1 skill        → single-domain announcement
2+ domains → announce chain → multi-domain announcement
```

`→` sequential (B depends on A) · `+` parallel (no shared state).
Chain shapes: [`references/multi-domain-chaining.md`](./references/multi-domain-chaining.md).

---

## MAINTENANCE

| Command | When |
|---|---|
| `scripts/doctor.py` | routing feels dead, or after any Claude Code upgrade |
| `scripts/install_hooks.py` | doctor reports missing hooks |
| `scripts/build_catalog.py` | you installed a skill and want it routable now |
| `scripts/fix_agent_models.py` | after adding an agent, so it follows your session model |
| `scripts/catalog_match.py "<prompt>"` | a specialist suggestion looked wrong |
| `scripts/learn-from-history.py` | monthly: which announcements get ignored |
| `python3 -m pytest tests/ -q` | after editing any routing logic |
| `tests/calibration.py --min-accuracy 95` | accuracy gate over 109 curated prompts |

Personal routes and project guardrails: [`SKILL.personal.md`](./SKILL.personal.md).

---

## RED FLAGS — signs you're about to skip this

```
"This is simple"            → Simple things take 5s to route.
"I know what to do"         → Then routing confirms it. 5s cost, 0 downside.
"No match in table"         → Check the specialist line before giving up.
"Ambiguous task"            → Default to the higher-complexity path.
"I'll just paraphrase"      → No. The [skill-router] format is the contract.
"The announcement is wrong" → Overrule it with a reason. Don't ignore it.
```
