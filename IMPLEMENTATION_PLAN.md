# skill-router Implementation Plan

## Steering Decision

Build `skill-router` as a standalone product for individual operators.

Do not turn it into:

- a governance platform
- a silent package installer
- an always-on internet crawler

## Product Goal

Create a Codex-native routing and orchestration layer that:

1. understands the current task
2. detects the best local capability set
3. recommends the right execution lane
4. keeps the workflow lightweight

## MVP

### Must Have

- task triage model
- local inventory resolver for Codex skills, plugins, and MCP surfaces
- routing recommendation format
- repo-friendly `AGENTS.md` policy template
- explicit verification gate

### Should Have

- queryable local inventory scanner
- simple confidence score for recommendations
- benchmark prompts for routing evaluation

### Excluded From MVP

- internet discovery on every task
- auto-install from GitHub
- telemetry dashboards
- enterprise policy features
- multi-user governance

## Build Phases

### Phase 1 — Inventory

Goal:
know what capabilities actually exist locally.

Deliverables:

- `scripts/scan_codex_inventory.py`
- stable inventory schema
- human + JSON output modes

### Phase 2 — Router Core

Goal:
map a task into a lane and candidate capability chain.

Deliverables:

- trivial / broken / build / operate / review / plan classifier
- keyword extraction
- capability ranking heuristics

### Phase 3 — Codex-Native Surface

Goal:
make the product usable inside Codex without pretending a normal skill can run before every task.

Deliverables:

- `AGENTS.md` template
- optional helper skill for route-this-task flows
- concise recommendation contract

### Phase 4 — Validation

Goal:
test whether routing improves execution instead of just sounding smart.

Deliverables:

- benchmark prompt suite
- baseline vs routed comparison
- wrong-lane and unnecessary-overhead measurements

### Phase 5 — Curated Discovery

Goal:
fill real capability gaps safely.

Deliverables:

- explicit discovery command
- official/curated registries first
- preview-before-install flow

## Immediate Tranche

This repo should do these next:

1. ship the Codex local inventory scanner
2. lock product positioning and boundary docs
3. define the routing recommendation schema
4. build the first local-only router prototype

## Hard Rules

- no silent installation
- no GitHub auto-clone in the default path
- no hardcoded vendor-model names in the Codex build
- no enterprise-governance creep
