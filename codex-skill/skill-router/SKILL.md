---
name: skill-router
description: Route non-trivial Codex tasks to the right local execution lane and capability set. Use when Codex needs to decide between direct execution, a local skill, an enabled plugin, or an MCP/tool surface; when the user asks which skill or workflow to use; when you want to inspect the local Codex inventory; or when a task needs a compact lane + capability + verification recommendation before work begins.
---

# Skill Router

Use this skill to make Codex routing environment-aware instead of guess-based.

## Quick Start

1. Classify the task into a lane:
   - `direct`
   - `broken`
   - `build`
   - `operate`
   - `review`
   - `plan`
2. Run `scripts/scan_codex_inventory.py` to inspect local skills, plugins, and MCP servers.
3. Match the task against the most specific local capability first.
4. Return a compact recommendation using the routing contract in `references/routing-contract.md`.

## Lane Selection

- `direct`: trivial read, one command, one factual answer, or obviously reversible work with no special capability needed
- `broken`: bug, failure, regression, wrong output, crash, failing tests, broken build
- `build`: new feature, component, integration, file, artifact, or workflow addition
- `operate`: refactor, automate, configure, document, deploy, optimize, maintain
- `review`: code review, risk review, architecture review, security review
- `plan`: broad, ambiguous, or multi-stage work where the execution path is not yet solid

If a task spans multiple lanes, choose the first critical lane and note the downstream chain.

## Capability Resolution

Prefer, in order:

1. repo instructions already in force
2. local Codex skills
3. enabled plugins
4. configured MCP/tool surfaces
5. direct execution

Rules:

- prefer one strong local capability over a long chain
- prefer the most specific capability over the most famous one
- fall back to direct execution if no strong local match exists
- do not search the internet for skills by default
- do not auto-install anything silently

## Inventory Scan

Use the bundled script:

```bash
python3 scripts/scan_codex_inventory.py
python3 scripts/scan_codex_inventory.py --query deploy
python3 scripts/scan_codex_inventory.py --json
```

Use the inventory scan when:

- you need to know which local capability is actually present
- multiple possible skills might match
- the task mentions a tool or platform and you want to confirm availability

## Output Contract

Always return:

- lane
- chosen capability or fallback
- reasoning tier: `fast`, `standard`, or `frontier`
- verification plan
- short notes only if needed

See `references/routing-contract.md` for the canonical shape.

## Codex-Specific Notes

- this skill is local-first
- this skill is for routing and orchestration, not governance
- for enterprise governance, policy, and proof of judgment, use Sentigent instead of expanding this skill into a control plane

## Resources

### `scripts/`

- `scan_codex_inventory.py`: inspect installed local Codex skills, enabled plugins, and configured MCP servers

### `references/`

- `routing-contract.md`: required output shape for recommendations
- `product-boundary.md`: product boundary and audience definition for the Codex version
