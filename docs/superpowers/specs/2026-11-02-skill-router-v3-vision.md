# skill-router v3 — vision and requirements (2026-11-02)

Source: owner conversation of 2026-11-01/02, the v2.0 audit, and the measurements in `docs/no-key-routing.md`.

## Product thesis

skill-router becomes the quality layer for agent skills: it chooses which skill, tool and model each piece of
work gets, shows Claude only what helps, measures whether it helped, and gives skill authors and teams the
tooling the ecosystem lacks (audit, tests, lockfile). Routing is one feature of it, not the whole product.

**Jev is the core.** Jev (TypeSafe System One) is the differentiator: with it, the right skill reaches Claude on
7/27 hard and 38/74 everyday real prompts; the best keyless method reaches 4 and 11. Every wave keeps a
with-Jev and a without-Jev path; neither removes the other. Keyless quality may approach Jev but is never
allowed to degrade the Jev path.

## Requirements

R1. **Two modes, always.** Every feature works with `TYPESAFE_API_KEY` (Jev) and without any key. Jev-mode
    numbers are reported next to keyless numbers in every benchmark.
R2. **No burden on Claude.** Anything injected into context is bounded (a line ≤ 200 chars unless it is a
    route card), shown only on work turns, never repeated within a session window, and measured for chat
    false alarms (must stay 0 on the control set).
R3. **Measure outcomes, not obedience.** The router records whether a routed or shortlisted skill was loaded
    and whether the turn's work succeeded (tests green, no correction next turn, no rework within a day).
    A random holdout (default 10 % of work turns, route suppressed) gives the honest lift.
R4. **Privacy.** No prompt text leaves the machine except to a provider the user configured; logs carry
    keywords and hashes only; anything shared beyond the machine is opt-in and aggregated.
R5. **Never install or execute a skill on the user's behalf.** Suggestions and audits are advisory.
R6. **Cross-agent.** The router is reachable as an MCP server so Codex, Cursor and Gemini CLI can use it;
    Claude Code hooks are one client.
R7. **Fewer tokens.** Show Claude only relevant skills where the harness allows it; measure the listing cost
    before and after.
R8. **Local-first quality.** A local model distilled from Jev's decisions narrows the keyless gap; any
    OpenAI-compatible endpoint (including a local server) can be plugged in as a chooser.
R9. **Ecosystem tooling.** Skill audit for users, skill tests and a badge for authors, `skills.lock` for teams,
    and a periodic public report built from the skills.sh cache.
R10. **Ship continuously.** Each wave ends in a tagged release with its own evidence; a wave that fails its
     measurement bar ships off-by-default rather than not at all.

## Owner decisions (not the implementer's)

- Product name if scope grows beyond routing (e.g. "skillkit").
- Any public announcement, report publication, or partnership outreach (TypeSafe, skills.sh, Anthropic).
- Any hosted service (shared-results backend) and its cost.
- How public users obtain a Jev key.
