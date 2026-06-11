# Routing Contract

Use this output shape for every non-trivial recommendation.

## Required Fields

- `lane`
- `capabilities`
- `reasoning_tier`
- `verification`
- `notes`

## Lane Values

- `direct`
- `broken`
- `build`
- `operate`
- `review`
- `plan`

## Capability Entry

Each capability should include:

- `name`
- `type`: `skill`, `plugin`, `mcp`, or `none`
- `why`

## Reasoning Tier

Use:

- `fast`
- `standard`
- `frontier`

Avoid hardcoding vendor-model family names in the recommendation.

## Recommendation Rules

- prefer local capabilities first
- prefer the most specific strong match
- avoid long chains when one capability is enough
- no internet discovery in the default path
- verification is required for non-trivial work
