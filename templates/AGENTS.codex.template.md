# skill-router Codex Template

## Role
- Use `skill-router` as a lightweight routing policy for non-trivial tasks.
- Optimize for the fastest correct execution path.

## Lane Selection
- `direct`: trivial read, one command, or one factual answer
- `broken`: bug, failure, regression, unexpected output
- `build`: new feature, component, integration, or artifact
- `operate`: refactor, configure, document, deploy, automate
- `review`: code review, risk review, quality review
- `plan`: broad, ambiguous, or multi-stage work

## Capability Resolution
- Check local repo instructions first.
- Prefer local Codex skills, enabled plugins, and configured MCP tools.
- Use the most specific local capability that matches the task.
- Fall back to direct execution if no strong capability exists.

## Discovery Rules
- Local-first always.
- Do not search the internet for skills by default.
- Only use curated or explicit discovery when the user asks for it.
- Never auto-install capabilities silently.

## Reasoning Tiers
- `fast` for trivial or lookup-heavy work
- `standard` for normal execution
- `frontier` for ambiguous, risky, or high-stakes work

## Verification
- Non-trivial work must include proof before completion.
- Pick the lightest verification that justifies the claim.
- If code changed, prefer tests, typecheck, lint, or direct runtime validation.

## Escalation
- Escalate only for destructive, irreversible, or materially branching actions.
- Do not escalate for obvious reversible next steps.

## Output Contract
- Say the lane.
- Say the chosen capability or fallback.
- Say the verification plan.
- Keep it compact.
