# Dispatch Protocol — Runtime Reference

> Loaded by `SKILL.md` when running a chain.
> User-facing context: see [`../docs/how-it-works.md`](../docs/how-it-works.md).

## The rule

```
For each step in the announced chain:

  IF step.model == "inherit" AND step is not a parallel fan-out:
      → Skill(<skill>) in-session
      → ▶ line says: inherit, in-session

  ELSE:
      → Agent(
          subagent_type=<agent from the triple>,
          model=<model from the triple>,      # omit when inherit
          description="<step skill name>",
          prompt="""
            <thinking-keyword if not none>.
            Use Skill: <skill from the triple>
            Task: <relevant slice of the user's request>
            Context: <files / decisions the step needs>
          """
        )
      → ▶ line says: via Agent (or "parallel via Agent" inside a `+` step)

  Sequential `→`: wait for the step to return before launching the next.
  Parallel `+`: launch together, one message with multiple Agent calls.
```

## Why `inherit` is the default

The parent session cannot hot-swap models mid-turn, so the only way to run a
step on a different model is a sub-agent. That made "which model" and "in-session
or dispatched" the same decision — and the routing table used to answer it with
a hard-coded model name.

That answer was written when the parent was always Sonnet, so `sonnet` on a row
silently meant `inherit`. It stopped meaning that. On a Fable or Opus session,
every routed step read as "model differs from parent" and got fanned out to a
sub-agent running a *weaker* model than the user had chosen — paying dispatch
overhead to get worse work. The table now says `inherit`, and depth is carried
by `thinking`, which composes with any model.

Dispatch to a sub-agent for one of two reasons, never for prestige:

| Reason | Example |
|---|---|
| A genuinely cheaper model suffices | `haiku` for a bulk repo scan returning file paths |
| The step needs its own context | parallel fan-out; a long read that would crowd the parent |

## Sub-agents are routed too

A `SubagentStart` hook (`scripts/subagent_brief.py`) injects a skill brief into
every dispatched agent, so the half of the work that actually edits files no
longer runs skill-blind. You still name the skill in the dispatch prompt —
belt and braces, and the prompt is what carries the *task-specific* choice:

```
Agent(subagent_type="seo-technical",
      prompt="think. Use Skill: seo-technical. Task: … Context: …")
```

Two things follow:

- **The IRON RULE stands down inside sub-agents.** The parent's pending skill
  belongs to the parent's turn; a sub-agent cannot satisfy it and would simply
  be blocked from every edit until it gave up. Enforcement checks for
  `agent_id` and returns silently when present.
- **Generic agents get no brief.** `general-purpose`, `Explore` and
  `researcher` have no domain, so any suggestion would be noise on the most
  frequently dispatched agents in the system.

## Logging dispatches (for observability + statusline)

The `▶` lines are the human-readable proof in the transcript. The JSONL events
are the machine-readable proof read by `scripts/audit-dispatch.py` and the
statusline. Write both; if they disagree, one of them is lying.

After announcing the chain:
```bash
echo '{"ts":"<ISO8601>","type":"chain-start","name":"<name>","steps":[...],"models":[...],"saved":<bool>}' \
  >> ~/.claude/skill_router_log.jsonl
```

After each step completes:
```bash
echo '{"ts":"<ISO8601>","type":"chain-step","step":<n>,"of":<total>,"skill":"<skill>","model":"<model>","via":"<table|specialist|project-route>"}' \
  >> ~/.claude/skill_router_log.jsonl
```

Thinking-active step:
```bash
echo '{"ts":"<ISO8601>","type":"thinking-active","level":"<think|think-hard|ultrathink>","active":true}' \
  >> ~/.claude/skill_router_log.jsonl
```

Chain end:
```bash
echo '{"ts":"<ISO8601>","type":"chain-end","name":"<name>"}' \
  >> ~/.claude/skill_router_log.jsonl
```

`subagent-brief` events are written by the hook itself — you do not emit those.

## Verification

```bash
python3 scripts/audit-dispatch.py        # was the protocol followed?
python3 scripts/doctor.py                # is routing wired up at all?
```

`doctor.py` is the one to run first. Protocol compliance is meaningless if the
hooks are not installed, which is exactly the failure that went unnoticed for
months: the audit script scored the chains it could see and never asked why
there were so few of them.

## Common skip patterns

| Pattern | Why it happens | Fix |
|---|---|---|
| Everything dispatched to sub-agents | Reading a stale model name as "not the parent" | The column is `inherit`; run it in-session |
| All steps run in-session despite a parallel `+` | Easier to write sequentially | Parallel steps need separate contexts — one message, multiple Agent calls |
| Thinking keyword forgotten | Habit | It is the literal first word of the dispatch prompt — see [`thinking-depth.md`](./thinking-depth.md) |
| Sub-agent ignores its skills | The brief is advisory | Name the skill in the dispatch prompt too |
