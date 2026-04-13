# skills-master

**Consistent skill routing for Claude Code. One file. Zero configuration.**

Claude Code has access to hundreds of skills — but it picks the wrong one 20-30% of the time. It rationalizes skipping skills entirely. It burns `opus` tokens on tasks that need `haiku`. It fires `brainstorming` when you just need `systematic-debugging`.

`skills-master` fixes this with a 3-question routing engine that runs before every non-trivial task and always outputs the right **Skill + Agent + Model**.

---

## Before / After

**You type:** *"The login endpoint is returning 500 errors in production"*

**Without skills-master:**
Claude launches `brainstorming`, spends time ideating on architecture, eventually reads the error. Uses `opus` the whole time.

**With skills-master:**
Q1 fires: something is broken → `systematic-debugging` + `sonnet`. Reads the error, traces the stack, applies the fix. Right skill, right model, first time.

## Measured Results

Tested via `claude -p` CLI on 20 real-world task prompts:

| Dimension | Score |
|-----------|-------|
| Overall (path + skill + model) | 18/20 **(90%)** |
| Path routing | 19/20 (95%) |
| Skill selection | 19/20 (95%) |
| Model selection | 19/20 (95%) |
| Skill tool actually fires correctly | 7/8 **(88%)** |

The 2 misses share one root cause: auth-adjacent task wording incorrectly triggering the "auth → opus" escalation rule. Fixable with a tighter signal.

Test harness is in the repo — run `bash run_routing_test.sh` against your own setup.

---

## How It Works

Three questions. Always in this order.

```
Q1: Is something BROKEN / WRONG / FAILING?
    → systematic-debugging

Q2: Is this CREATE / BUILD / ADD something new?
    → brainstorming → writing-plans → domain skill

Q3: Everything else (improve, ship, configure, automate, research)?
    → operate path

AMBIGUOUS? → default to the higher-complexity path
```

Every route outputs a **dispatch triple:**

```
Skill:  superpowers:systematic-debugging
Agent:  general-purpose
Model:  sonnet
```

Model selection is part of routing — not a separate decision. Simple file reads get `haiku`. Complex multi-file debugging gets `opus`. Everything else gets `sonnet`.

---

## Install

```bash
mkdir -p ~/.claude/skills/skills-master
curl -sL https://raw.githubusercontent.com/hussi9/skills-master/main/SKILL.md \
  > ~/.claude/skills/skills-master/SKILL.md
```

That's it. Claude Code loads it automatically. You never invoke it manually — it runs before every non-trivial task.

---

## Add Personal Overrides (Optional)

The core routes 90% of tasks correctly for anyone. For project-specific routing, copy the personal template:

```bash
curl -sL https://raw.githubusercontent.com/hussi9/skills-master/main/SKILL.personal.md \
  > ~/.claude/skills/skills-master/SKILL.personal.md
```

Edit it to add your own project routing. The core runs first, your overrides layer on top — same model as CSS specificity.

---

## What Gets Routed

| Path | Examples | Routing |
|------|----------|---------|
| **BROKEN** | error, crash, test fail, wrong output | `systematic-debugging` → sonnet |
| **BUILD** | new feature, component, integration | `brainstorming` → `writing-plans` → domain skill |
| **OPERATE** | refactor, deploy, configure, research | specific skill per signal |
| **Production incident** | 500 errors in prod, data loss | `systematic-debugging` → **opus** |
| **Multi-domain** | fix + add tests + deploy | announces chain, runs each skill in order |

Full routing table is in [SKILL.md](./SKILL.md).

---

## Works With 2,700+ Skills

- **Superpowers** — process discipline skills (`brainstorming`, `systematic-debugging`, `verification-before-completion`, etc.)
- **Antigravity** — 860+ domain skills (`react-patterns`, `typescript-expert`, `seo-audit`, `langgraph`, etc.)
- **Composio** — 944+ SaaS integrations (`stripe-automation`, `slack-automation`, etc.)
- **Community** — any GitHub repo with a `SKILL.md` (auto-discovered)

New skills installed? The registry check runs each session and incorporates them automatically.

---

## Skill Discovery

No match in the routing table? It walks a discovery protocol:

1. Searches locally installed skills
2. Checks the Antigravity catalog (860+ skills)
3. Searches Composio (944+ integrations)
4. Searches GitHub for repos with `SKILL.md`
5. Auto-downloads and installs matching skill
6. Falls back to writing a custom skill

---

## Personal Overrides

The CSS cascade model:

```
Universal core rules     (this repo)
        +
Personal overrides       (your SKILL.personal.md)
        =
Your routing config
```

Override any routing entry. Add project-specific signals. Your rules always win.

See [SKILL.personal.md](./SKILL.personal.md) for a documented template.

---

## Design Principles

- **Zero UX** — you never invoke skills-master. You type normally, Claude routes correctly.
- **Deterministic** — same input always produces same output. No vibes-based routing.
- **Fail safe** — ambiguous tasks default to the higher-complexity path. Over-routing beats under-routing.
- **Living** — scans for newly installed skills each session. Install a skill, it routes to it.
- **One file** — no build step, no config, no dependencies. Drop it in and forget it.

---

## License

MIT
