---
name: skill-router-personal
description: Personal routing overrides for this machine. Project routes are parsed by scripts/router.py and win over the universal routing table.
---

# Skill Router — Personal Overrides

Two layers live here, and they behave differently:

| Layer | Read by | Enforced? |
|---|---|---|
| `routes:` (below) | `scripts/router.py` at hook time | Yes — becomes the announced step |
| `chains:` (below) | You, when you read this file | No — guidance for multi-step work |

The universal core (`SKILL.md`) handles generic work: is this broken, new, or
maintenance. It cannot know that "ship the next one" means a YouTube short and
"submit the build" means Scrollbook. That is what `routes:` is for — the one
signal the router can be certain about is a name that only ever means one
project.

---

## PROJECT ROUTES — parsed, deterministic, first match wins

Every trigger is a case-insensitive substring of the prompt and must be at
least 4 characters (shorter ones collide with ordinary English). A route whose
skill is not installed is skipped with a warning rather than announced, so
uninstalling a skill degrades gracefully instead of deadlocking the IRON RULE.

Order is priority order. Put the narrow triggers above the broad ones.

```yaml
routes:
  - name: youtube-pipeline
    when: ["@economicalai", "economicalai", "youtube short", "hook-forge", "yt-showrunner", "youtube video", "retention edit"]
    skill: youtube-manager
    agent: general-purpose
    path: OPERATE
    thinking: think

  - name: youtube-thumbnail
    when: ["thumbnail"]
    skill: youtube-thumbnail
    path: BUILD

  - name: scrollbook-ship
    when: ["testflight", "app store connect", "capgo", "play store", "scrollbook deploy", "scrollbook release"]
    skill: scrollbook-deploy
    path: OPERATE
    thinking: think

  - name: scrollbook-qa
    when: ["scrollbook qa", "scrollbook test"]
    skill: scrollbook-qa
    path: OPERATE

  - name: scrollbook-authoring
    when: ["scrollbook chapter", "scrollbook book", "story pool", "briefing"]
    skill: claude-author
    path: BUILD
    thinking: think

  - name: jobhunt
    when: ["jobhunt", "applypilot", "tailored resume", "job pipeline"]
    skill: jobhunt-agent
    path: OPERATE

  - name: lead-gen
    when: ["wseller", "cosmetic dentist", "lead gen", "outreach draft", "prospect list"]
    skill: wseller
    path: OPERATE
    thinking: think

  - name: mac-health
    when: ["kernel panic", "mac keeps restarting", "free disk space", "macbook slow", "startup items"]
    skill: mac-doctor
    path: BROKEN
    thinking: think

  - name: media-generation
    when: ["higgsfield", "kling", "b-roll", "broll", "image-to-video"]
    skill: higgsfield
    path: BUILD

  - name: seo-work
    when: ["seo audit", "core web vitals", "schema markup", "serp", "backlink"]
    skill: seo
    path: OPERATE
    thinking: think

  - name: paid-ads
    when: ["google ads", "meta ads", "ad account", "campaign budget", "ad creative"]
    skill: ads
    path: OPERATE
    thinking: think

  - name: marketing
    when: ["landing page copy", "cold email", "positioning", "go-to-market", "growth loop"]
    skill: market
    path: BUILD
    thinking: think

  - name: skill-system
    when: ["skill-router", "skill router", "routing table", "write a skill", "skill catalog"]
    skill: superpowers:writing-skills
    path: BUILD
    thinking: think
```

**Adding a route:** name it, give it triggers only that project would ever
produce, point it at an installed skill. Then prove it fires:

```bash
python3 ~/.claude/skills/skill-router/scripts/router.py <<< "your test prompt"
```

**When NOT to add one:** the generic table already lands it, or the trigger is
a word you use across projects. A route that fires on the wrong project is
worse than no route — you will start ignoring the announcement.

---

## EXECUTION GUARDRAILS — this machine

```
Autonomous mode is the default (see ~/.claude/CLAUDE.md). Interactive
gate skills — superpowers:brainstorming above all — will usually be the
wrong call, because they stop and ask. Record the design in
docs/superpowers/specs and keep going instead. If the router announces
brainstorming on a directive you have already fully specified, overrule it
with a reason rather than sitting through the gate:
  scripts/router_override.py "user gave full spec; autonomous mode"

Supabase schema touched?   → regenerate types before calling it done
iOS / Capacitor change?    → verify on the simulator, not the diff
Payment or auth flow?      → security-auditor before merge
UI/UX task?                → the CLAUDE.md contract rule applies: appearance
                             only, every control still present afterwards
Private repo?              → no GitHub Actions; local scripts/check.sh only
Deleting anything?         → move to .archive/, never rm
```

---

## COMPLETION GATES — extended

```
DATABASE:  migration applied · types regenerated · RLS reviewed
MOBILE:    simulator screenshot · both platforms where the app has both
CONTENT:   vision audit passed before any upload
DEPLOY:    tests green locally · verification-before-completion invoked
```

---

## NAMED CHAINS — read by you, not by the parser

These are sequences worth remembering. They are documentation: the router does
not execute them. Schema and rationale: [`references/named-chains.md`](./references/named-chains.md).

```yaml
chains:
  - name: ship-feature
    when: ["ship the feature", "start the implementation"]
    chain: superpowers:writing-plans → superpowers:test-driven-development → superpowers:verification-before-completion

  - name: production-incident
    when: ["production is down", "users can't log in"]
    chain: superpowers:systematic-debugging → security
    thinking: ultrathink

  - name: design-pass
    when: ["design pass", "make it look right"]
    chain: frontend-design:frontend-design → design-review → ui-review
```
