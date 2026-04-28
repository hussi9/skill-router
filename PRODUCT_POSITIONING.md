# skill-router Product Positioning

## One Sentence

`skill-router` is the routing and orchestration layer for individual AI agent operators who want the right capability, workflow, and execution lane chosen faster.

## Audience

### Primary

- solo developers
- power users with large skill catalogs
- agent-heavy builders
- workflow-oriented users who care about speed and leverage

### Secondary

- small technical teams using the same personal setup
- users experimenting with multi-skill orchestration before they need governance

### Not the target

- security or compliance buyers
- platform teams buying agent governance
- enterprises looking for policy enforcement, audit, or proof-of-value reporting

## Core Promise

- choose the right lane
- choose the right capability
- reduce routing mistakes
- reduce wasted effort from vague execution starts

## Product Boundary

`skill-router` should own:

- task triage
- lane selection
- local capability lookup
- workflow chaining
- operator-facing execution guidance

`skill-router` should not own:

- org-wide policy enforcement
- governance dashboards
- judgment scoring
- proof-of-value reporting
- enterprise audit surfaces
- silent internet installs as part of the default routing loop

## Relationship to Sentigent

The overlap is real but intentional.

- `skill-router` answers: "What should I run next?"
- `Sentigent` answers: "Was that the right judgment, and can we prove it?"

Shared mechanics are acceptable:

- clarity scoring
- intent extraction
- routing hints
- prompt shaping
- local capability awareness

But the products should remain separate because:

- the buyer is different
- the value story is different
- the trust model is different
- the success metrics are different

## Success Metrics

`skill-router` wins when it improves:

- correct lane selection
- relevant capability usage
- time-to-first-correct-action
- reduced workflow thrash
- reduced unnecessary planning on simple tasks

It does not need to prove:

- enterprise safety
- org governance ROI
- calibrated decision quality

## Product Principles

- local-first
- operator-first
- minimal ceremony
- deterministic enough to trust
- explicit over magical
- curated discovery later, not auto-install by default
