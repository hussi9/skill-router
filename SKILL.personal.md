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
    tier: hard
    gates: ["vision audit passed before upload", "cost-log.md updated"]


  - name: scrollbook-ship
    when: ["capgo", "scrollbook deploy", "scrollbook release", "scrollbook build", "scrollbook testflight"]
    skill: scrollbook-deploy
    path: OPERATE
    thinking: think
    tier: hard
    gates: ["tests green locally", "simulator screenshot"]

  - name: scrollbook-qa
    when: ["scrollbook qa", "scrollbook test"]
    skill: scrollbook-qa
    path: OPERATE

  - name: scrollbook-authoring
    when: ["scrollbook chapter", "scrollbook book", "story pool"]
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




  # Triggers are authoring phrases only. Naming the router ("skill-router",
  # "routing table") is not skill authoring — that carded every status message
  # about the router with writing-skills on 2026-09-21.
  - name: skill-system
    when: ["write a skill", "write a new skill", "create a skill", "create a new skill", "author a skill", "edit the skill.md", "rewrite the skill.md", "skill description"]
    skill: superpowers:writing-skills
    path: BUILD
    thinking: think
```

## PROJECTS — what a name means, which skills and memories belong to it

Parsed by `scripts/build_index.py`. A prompt that names a project boosts that
project's skills and demotes skills that belong to a *different* project, so
"push deenunlock to testflight" can no longer land on `scrollbook-deploy`.
`memory:` names files in `~/.claude/projects/-Users-airbook/memory/`; the route
card names the first one that matches so the session opens with the right
context. `gates:` are completion gates the route card repeats.

```yaml
projects:
  youtube:
    aliases: ["economicalai", "@economicalai", "youtube", "yt", "short", "shorts", "hook-forge", "retention"]
    skills: ["youtube-manager", "yt-to-blog", "youtube-thumbnail", "higgsfield"]
    memory: ["brand_manager_agent"]
    gates: ["vision audit passed before upload", "cost-log.md updated"]
  deenunlock:
    aliases: ["deenunlock", "deen unlock", "prayer app", "muslim app", "salah"]
    skills: []
    memory: ["deenunlock-160-release-state", "deenunlock-150-release-state", "deenunlock-store-stats-runbook", "deenunlock-dua-ilm-uiux-parity", "deenunlock-flat-deed-economy"]
    gates: ["simulator screenshot", "both platforms where the app has both"]
  prayermode:
    aliases: ["prayermode", "prayer mode", "christian app"]
    skills: []
    memory: ["prayermode_project"]
    gates: ["simulator screenshot"]
  scrollbook:
    aliases: ["scrollbook", "linkiz", "story pool", "capgo"]
    skills: ["scrollbook-deploy", "scrollbook-qa", "scrollbook-marketing", "claude-author", "pipeline"]
    memory: ["scrollbook_marketing", "linkiz_design_inspiration_upscaleup"]
    gates: ["tests green locally", "verification-before-completion invoked"]
  aimasterz:
    aliases: ["aimasterz", "theaibill", "the ai bill", "finops"]
    skills: ["theaibill"]
    memory: ["theaibill-practice", "no-premature-product-promo"]
    gates: []
  wseller:
    aliases: ["wseller", "cosmetic dentist", "dentists", "lead gen", "outreach"]
    skills: ["wseller"]
    memory: []
    gates: []
  jobhunt:
    aliases: ["jobhunt", "applypilot", "job pipeline", "resume", "job posting"]
    skills: ["jobhunt-agent", "tailored-resume-generator"]
    memory: ["mac_mini_infrastructure"]
    gates: []
  dealscout:
    aliases: ["dealscout", "deal", "deals", "discount"]
    skills: ["dealscout"]
    memory: []
    gates: []
  mac:
    aliases: ["macbook", "mac mini", "airbook", "kernel panic", "restarts", "restarted"]
    skills: ["mac-doctor"]
    memory: ["airbook_crash_root_cause", "mac_mini_infrastructure", "mac_doctor_skill"]
    gates: []
  linkedin:
    aliases: ["linkedin", "personal brand", "brand manager", "post"]
    skills: ["brand-manager", "yt-to-blog"]
    memory: ["feedback-linkedin-post-style-v4", "linkedin_strategy_2026", "no-premature-product-promo"]
    gates: ["hook carries a fact", "no product CTA"]
  ibtrade:
    aliases: ["ibtrade", "ibkr", "trading desk", "backtest"]
    skills: []
    memory: ["ibtrade-desk", "ibtrade-leverage-verdict"]
    gates: []
  sentigent:
    aliases: ["sentigent", "warden", "control plane", "cockpit"]
    skills: ["sentigent-score", "sentigent-review", "sentigent-learn"]
    memory: ["warden_control_plane", "sentigent_cockpit"]
    gates: []
  marketing-ops:
    aliases: ["marketing-ops", "adops", "ad operator", "meta ads", "google ads"]
    skills: ["adops"]
    memory: ["marketing_ops_platform", "adops_ads_mcp_wiring", "deenunlock_ads_kit"]
    gates: []
  skill-system:
    aliases: ["skill-router", "skill router", "skill catalog"]
    skills: ["skill-router"]
    memory: ["claude-setup-cleanup-2026-09-06"]
    gates: ["scripts/check.sh green"]
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

## EXPLICIT CHAIN INVOCATION — `/skill-router <chain-name> <details>`

Keyword matching is a guess about intent. Sometimes there is no guessing to
do: the user knows which chain they want and says so. Support that.

When this skill is invoked with arguments, read the first token as a chain
name and everything after it as the task:

```
/skill-router product-vision-review a Spotify-style app for Urdu poetry
                                    where users build and share playlists
   ^ chain name                      ^ the task every step receives
```

**Resolution order.** Exact match on a `name:` in the `chains:` block below
wins. If the first token matches no chain, treat the WHOLE argument as an
ordinary prompt and route it normally — a user typing
`/skill-router help me fix this build` should get routing, not an error
about "help" not being a chain.

**Running it.** Announce the chain and its steps, then execute them in
order: `→` is sequential (each step sees the previous step's output), `+`
is parallel (independent lenses, dispatched together, none waits for
another). Pass the task text to every step verbatim — a step that receives
a summary of the task instead of the task tends to answer a slightly
different question, and in a convergence chain those drifts compound.

**Why this is worth having.** Keyword triggers fire on the words a user
happened to use, which means the most deliberate requests — the ones where
someone has thought about what they want — are the ones most likely to miss
a chain, because considered phrasing rarely matches a trigger list. Explicit
invocation also makes chains discoverable: a user who reads the block below
can run any of them without reverse-engineering its triggers.

`/skill-router chains` with no further argument lists the available chains
and their triggers.

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

  # A product VISION is not a build task. Answering it with technical
  # feasibility alone is the failure this chain exists to prevent: on
  # 2026-09-07 a Spotify-for-shayari vision got a "yes, here is the
  # architecture" answer with no moat, no PMF, no pricing, and only a
  # footnote on the thing that was actually blocking it — whether 4,500
  # editorial fields could be authored at all.
  # Run the four independent lenses in parallel (they do not need each
  # other's output), then converge in steering-meeting, which is built to
  # surface where lenses DISAGREE rather than average them into mush.
  - name: product-vision-review
    when: ["product vision", "should we build", "is this worth building",
           "new product idea", "product direction", "pivot to", "what if we built"]
    chain: office-hours + market-competitors + market-pricing + content-feasibility → steering-meeting
    model: opus
    thinking: ultrathink
```
