# Codex Adaptation Plan for skill-router

## Executive Decision

Do not port `skill-router` to Codex as a normal skill.

Build a Codex-native orchestration layer instead:

- `AGENTS.md` as the primary policy surface
- optional local helper skill for plan generation and inventory lookup
- optional plugin/runtime support later for telemetry and registry features

The Codex version should solve:

1. local dispatch quality
2. repo-specific overrides
3. safe escalation and verification
4. inventory awareness across installed skills, plugins, and MCP tools

It should not solve "discover and install random internet skills on every task" in V1.

## Steering Summary

### What is useful in the current repo

- The core insight: agents need help choosing the right execution lane.
- The 3-path triage: `BROKEN / BUILD / OPERATE` is a decent first-pass classifier.
- The idea of project-specific overrides layered on top of a shared core.
- The insistence on a completion gate before claiming done.

### What is not portable

- Claude-only assumptions (`~/.claude`, `claude -p`, `Skill` hook semantics)
- hardcoded model routing (`haiku / sonnet / opus`)
- the claim that a single skill can "run before every non-trivial task"
- auto-installing GitHub skills during normal work

### Product call

The Codex opportunity is real, but the product is different:

- not a "universal router skill"
- a "Codex execution policy and routing framework"

## Problem Statement

Advanced Codex users accumulate:

- many local skills
- multiple plugins
- MCP tools
- repo-specific instructions
- multiple execution styles

The failure mode is not "Codex is dumb."
The failure mode is:

- wrong lane selection
- skipping repo policy
- overusing general-purpose reasoning
- missing verification
- underusing installed capabilities

## Target Users

### Primary

- power users with 30+ installed skills
- teams using `AGENTS.md` seriously
- repos with custom workflows, MCP tools, or subagents

### Secondary

- solo developers with heavy plugin usage
- template maintainers who want a reusable orchestration layer

### Not the target

- casual users with fewer than 10 skills
- users who do not maintain project instructions
- users who want a magic one-file universal router

## Product Shape for Codex

### Layer 1: Core Policy

Implemented in `AGENTS.md`.

Responsibilities:

- choose lane: direct, plan, debug, review, deploy, team
- define escalation rules
- define verification rules
- define repo-specific overrides

### Layer 2: Local Inventory Resolver

Implemented as a small helper surface, not as global auto-behavior.

Responsibilities:

- inspect installed local skills under `~/.codex/skills`
- inspect enabled plugins and marketplace entries
- inspect relevant MCP/tool availability
- map task keywords to likely local capabilities

### Layer 3: Planning Helper

Implemented as a skill or template workflow.

Responsibilities:

- convert a user objective into:
  - execution lane
  - capability chain
  - verification plan
  - fallback path if no good capability exists

### Layer 4: Optional Telemetry

Only later.

Responsibilities:

- log which lane was chosen
- log which skills/plugins were used
- track rework and verification outcomes

This is optional because the product still has value without it.

## Key Product Decisions

### Decision 1: Do not make this a mandatory always-on skill

Reason:

- Codex already has stronger orchestration surfaces than Claude-era skill-router assumed
- forcing a userland skill to run first on every task is brittle and unnatural in Codex

Chosen shape:

- `AGENTS.md` policy first
- helper skill only when needed

### Decision 2: Local discovery is required

Reason:

- local skills/plugins/tools are high-trust and fast
- Codex installs vary heavily
- useful routing must be environment-aware

Chosen shape:

- local inventory scan in V1

### Decision 3: Internet skill discovery is not a V1 default

Reason:

- high trust and provenance risk
- adds latency and noise
- likely to install overlapping or low-quality skills
- can conflict with repo instructions

Chosen shape:

- not automatic
- not "on every task"
- only explicit or curated in V2+

### Decision 4: Model routing should be capability-tier based, not vendor-model-name based

Reason:

- Codex model inventory differs from Claude
- hardcoded model family names will stale quickly

Chosen shape:

- fast / standard / frontier reasoning tiers
- map tiers to current Codex configuration

## Internet Discovery: Included or Desired?

### Short answer

Desired eventually in a constrained form.
Not desirable as a default behavior in the core build.

### V1

No internet discovery.

Only:

- local skills
- enabled plugins
- configured MCP/tools
- project instructions

### V2

Add curated discovery only:

- official OpenAI curated skills/plugins first
- approved internal registries second
- no generic GitHub auto-clone

### V3

Optional web discovery behind an explicit user action such as:

- "find a missing skill"
- "search the marketplace"
- "install a skill for X"

Even then:

- rank by trust source
- show provenance
- ask before install
- default to preview, not install

### Hard no

Do not ship:

- auto-web-search on every non-trivial task
- auto-clone from GitHub during normal routing
- silent installation into `~/.codex/skills`

## Codex-Native Architecture

### Inputs

- user request
- repo `AGENTS.md`
- local installed skills
- enabled plugins
- available tools/MCP surfaces
- optional project memory

### Router output

- lane
- capability chain
- reasoning tier
- verification requirements
- unresolved gaps

### Router algorithm

1. Classify the task:
   - trivial
   - broken
   - build
   - operate
   - review
   - plan
2. Extract key nouns and verbs.
3. Resolve local capability candidates.
4. Score candidates by:
   - specificity
   - trust
   - repo fit
   - execution cost
5. Choose:
   - direct execution
   - capability chain
   - no-skill fallback
6. Attach verification policy.
7. Proceed.

## MVP Scope

### Must have

- Codex-oriented routing policy template
- local inventory scanner
- capability resolver
- repo override support
- verification gate template
- planning document template

### Nice to have

- lane explanation summary
- reusable scoring heuristics
- test harness for routing quality

### Excluded from MVP

- internet discovery
- statusline telemetry
- automatic installation
- marketplace ranking
- autonomous registry sync

## Implementation Plan

### Phase 0: Product framing

Goal:
Define the Codex-native contract before writing code.

Deliverables:

- `AGENTS.md` router template
- routing taxonomy
- capability scoring rules
- list of non-goals

### Phase 1: Local capability inventory

Goal:
Make routing environment-aware.

Deliverables:

- scanner for `~/.codex/skills`
- scanner for enabled plugins from config
- scanner for configured MCP/tool surfaces
- normalized capability manifest

Notes:

- start read-only
- no installs

### Phase 2: Routing engine

Goal:
Produce a deterministic recommendation from a task.

Deliverables:

- task classifier
- keyword extractor
- capability matcher
- ranking heuristics
- human-readable routing summary

### Phase 3: Codex execution integration

Goal:
Use the routing result in a Codex-native way.

Deliverables:

- `AGENTS.md` guidance pattern
- helper skill or command for "route this task"
- repo override file format if needed

### Phase 4: Verification framework

Goal:
Ensure routing improves outcomes, not just labels.

Deliverables:

- benchmark prompt set
- baseline vs routed comparison
- metrics for:
  - task completion
  - wrong-lane rate
  - verification compliance
  - latency overhead

### Phase 5: Curated discovery

Goal:
Add safe gap-filling without turning the router into a package manager.

Deliverables:

- explicit discovery command
- official curated registry support
- preview and install flow
- provenance + trust labels

## Success Metrics

The product is working only if it improves:

- task completion rate
- correct lane selection
- use of relevant local capabilities
- verification compliance
- reduction in rework

It is not enough to improve:

- routing-agreement scores
- self-reported confidence
- pretty dispatch tables

## Main Risks

### Product risk

The user may not have enough skills installed for routing to matter.

Mitigation:

- make the product useful even with few skills by improving lane selection and verification

### Engineering risk

Capability inventories can become stale or inconsistent.

Mitigation:

- read from real local sources on demand
- do not maintain a hand-edited canonical registry

### UX risk

The router may feel like extra ceremony.

Mitigation:

- keep outputs short
- auto-skip trivial tasks
- never require a verbose plan for simple work

### Trust risk

Internet discovery can pull in bad skills.

Mitigation:

- keep it out of V1
- curated only later

## Keep / Delete from Current Repo

### Keep

- triage idea
- layered overrides idea
- completion gate idea
- benchmark mindset

### Rewrite

- all model routing
- all installation instructions
- all runtime assumptions
- all claims about automatic always-on execution

### Delete

- Claude-specific hook/setup material in the Codex version
- GitHub auto-clone as a default routing behavior
- vendor-specific hardcoded paths and assumptions

## Final Recommendation

Build this for Codex only if you narrow the scope.

Good product:

- Codex execution policy + local capability router + verification framework

Bad product:

- universal always-on skill router with internet search and auto-install baked into the critical path
