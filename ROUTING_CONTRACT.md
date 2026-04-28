# skill-router Routing Contract

## Purpose

A router recommendation must be short, explicit, and executable.
It should help an operator start correctly, not generate a long essay.

## Output Shape

Every recommendation should provide:

1. `lane`
2. `capabilities`
3. `reasoning_tier`
4. `verification`
5. `notes`

## Field Definitions

### `lane`

One of:

- `direct`
- `broken`
- `build`
- `operate`
- `review`
- `plan`

### `capabilities`

Ordered list of local capabilities to use.

Each item should include:

- capability name
- type: `skill`, `plugin`, `mcp`, or `none`
- why it was chosen

### `reasoning_tier`

Use Codex-native abstract tiers:

- `fast`
- `standard`
- `frontier`

Do not hardcode vendor-model family names in the contract.

### `verification`

List the minimum proof required before saying done.

Examples:

- tests pass
- typecheck clean
- output visually verified
- deploy reachable

### `notes`

Optional short notes:

- missing capability
- fallback used
- ambiguous request
- repo policy override

## Example

```json
{
  "lane": "broken",
  "capabilities": [
    {
      "name": "bug-hunter",
      "type": "skill",
      "why": "Task is a defect report with likely root-cause analysis"
    },
    {
      "name": "playwright",
      "type": "skill",
      "why": "UI repro and verification are likely needed"
    }
  ],
  "reasoning_tier": "standard",
  "verification": [
    "repro before fix",
    "tests or manual repro pass after fix"
  ],
  "notes": [
    "No internet discovery used",
    "Local inventory only"
  ]
}
```

## Rules

- prefer local capabilities first
- prefer one good capability over a long chain
- avoid planning overhead for trivial work
- no internet discovery in the default path
- verification is mandatory for non-trivial work
