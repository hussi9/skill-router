# skills-master

![skills-master routing diagram](assets/skills-master-visual.png)

**Consistent skill routing for Claude Code. One file. Zero configuration.**

> If this saves you time, a ⭐ on GitHub helps others find it.

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

### 1. Install the skill (required)

```bash
mkdir -p ~/.claude/skills/skills-master
curl -sL https://raw.githubusercontent.com/hussi9/skills-master/main/SKILL.md \
  > ~/.claude/skills/skills-master/SKILL.md
```

Claude Code loads it automatically. You never invoke it manually — it runs before every non-trivial task.

### 2. Add personal overrides (optional)

```bash
curl -sL https://raw.githubusercontent.com/hussi9/skills-master/main/SKILL.personal.md \
  > ~/.claude/skills/skills-master/SKILL.personal.md
```

Edit it to add project-specific routing. The core runs first, your overrides layer on top (CSS cascade model).

### 3. Status bar with active skill indicator (optional)

See which skill is active directly in your Claude Code status line:

```
◆ sonnet · ~/myproject · ⎇ main · ⚙ systematic-debugging · ▓▓░░░░░░░░ 20% · $0.03
```

**Install the statusline:**

```bash
curl -sL https://raw.githubusercontent.com/hussi9/skills-master/main/statusline.sh \
  > ~/.claude/statusline.sh
chmod +x ~/.claude/statusline.sh
```

**Add to `~/.claude/settings.json`** (merge with your existing hooks):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Skill",
        "hooks": [
          {
            "type": "command",
            "command": "skill=$(echo $CLAUDE_TOOL_INPUT | jq -r '.skill // empty'); if [[ -n \"$skill\" ]]; then echo \"$(date '+%Y-%m-%d %H:%M:%S')\t$skill\" >> ~/.claude/skill_usage.log; fi",
            "async": true
          }
        ]
      }
    ]
  },
  "statusLine": {
    "type": "command",
    "command": "~/.claude/statusline.sh",
    "padding": 2
  }
}
```

The full hook snippet is in `settings-hooks.json` in this repo.

**What each segment means:**

| Segment | Meaning |
|---------|---------|
| `◆` / `◈` / `◉` / `⚡` | mood — context fill / cost level |
| `sonnet` | active model |
| `~/myproject` | working directory |
| `⎇ main✱3` | git branch + dirty file count |
| `⚙ systematic-debugging` | last skill invoked (shown 120s) |
| `▓▓░░░░░░░░ 20%` | context window fill |
| `4t` | turn count |
| `$0.03` | session cost |
| `1:42` | session duration |
| `+47 −12` | lines added / removed |

**View your skill usage log:**

```bash
# All skills used
cat ~/.claude/skill_usage.log

# Most used skills
sort ~/.claude/skill_usage.log | uniq -c -f1 | sort -rn

# skills-master invocations only
grep skills-master ~/.claude/skill_usage.log | wc -l
```

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
- **Antigravity** — 1,400+ domain skills (`react-patterns`, `typescript-expert`, `seo-audit`, `langgraph`, etc.)
- **Composio** — 940+ SaaS integrations (`stripe-automation`, `slack-automation`, etc.)
- **Community** — any GitHub repo with a `SKILL.md` (auto-discovered and installed)

---

## Skill Discovery — The Key Differentiator

New skills are published to GitHub daily. skills-master keeps you current without manual tracking.

**How it works — on every non-trivial task:**

```
1. Route via the 3-question triage (BROKEN / BUILD / OPERATE)
2. Check local catalog for a more specific match:
   ls ~/.agent/skills/ | grep -iE '<keyword>'        ← 1,400+ Antigravity skills
   ls ~/.composio-skills/ | grep -iE '<keyword>'     ← 940+ Composio integrations
   ls ~/.claude/skills/ | grep -iE '<keyword>'       ← your custom skills
3. If a better/more specific skill exists → use it instead
4. If no local match → search GitHub for "SKILL.md" claude <keyword>
5. Auto-clone and install the matching skill
6. Invoke it immediately
```

**Example:** You ask "help me optimize my Kubernetes pod scheduling."
- Table routes to `system-design` (generic)
- Catalog check finds `kubernetes-expert` in Antigravity
- skills-master uses `kubernetes-expert` instead
- You get specialist-level guidance without knowing the skill existed

This is why 48 people installed skills-master in the first week without a star prompt.

---

## Personal Overrides

The CSS cascade model:

```
Universal core rules     (SKILL.md — this repo)
        +
Personal overrides       (your SKILL.personal.md)
        =
Your routing config
```

Override any routing entry. Add project-specific signals. Your rules always win.

See [SKILL.personal.md](./SKILL.personal.md) for the template.

---

## Design Principles

- **Zero UX** — you never invoke skills-master. You type normally, Claude routes correctly.
- **Deterministic** — same input always produces same output. No vibes-based routing.
- **Fail safe** — ambiguous tasks default to the higher-complexity path. Over-routing beats under-routing.
- **Living** — checks local + GitHub catalogs on every task. Install a new skill, it routes to it immediately.
- **One file** — no build step, no config, no dependencies. Drop it in and forget it.

---

## License

MIT
