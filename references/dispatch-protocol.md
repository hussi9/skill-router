# Dispatch Protocol — Runtime Reference

> Loaded by `SKILL.md` when running a chain. Makes the Model column enforced
> instead of advisory.
> User-facing context: see [`../docs/how-it-works.md`](../docs/how-it-works.md).

## The rule

```
For each step in the announced chain:

  IF step.model == parent_session_model AND step is not parallel-fan-out:
      → Skill(<skill>) in-session  (cheaper, same context)
      → ▶ line says: in-session

  ELSE:
      → Agent(
          subagent_type=<agent from triple>,
          model=<model from triple>,
          description="<step skill name>",
          prompt="""
            <thinking-keyword if not none>.
            Use Skill: <skill from triple>
            Task: <relevant slice of the user's request>
            Context: <files / decisions the step needs>
          """
        )
      → ▶ line says: via Agent (or "parallel via Agent" inside a `+` step)

  Wait for sequential (`→`) steps to return before launching the next.
  Parallel (`+`) steps launch together via a single message with multiple
  Agent tool calls.
```

The `▶` line in the announcement is not decoration — it is the dispatch-mode
proof in the *transcript*. It is text-only: human-readable, greppable, and
co-located with the `[skill-router]` announcement so a reader can verify the
parent picked the right mode at a glance.

The audit script (`scripts/audit-dispatch.py`) reads structured JSONL events
from `~/.claude/skill_router_log.jsonl` (see schema below) — NOT the `▶`
lines. The two layers must agree: if you write `▶ db-expert  (sonnet,
in-session)` then call `Agent(...)`, the announcement is wrong (visible to
humans); if you write a `chain-step` event with `via=table` then run nothing,
the audit fails (visible to the script). Always write both for every step.

## Why subagents not in-session

The parent Claude Code session can't hot-swap models mid-turn. The only way
to enforce a different model on a step is to run that step in a subagent
with `model:` set on the `Agent` call.

For a 5-step chain with mixed complexity, this typically nets a 30-50% cost
reduction vs running everything at the parent's model.

## Logging dispatches (for observability + statusline)

After announcing the chain, append one JSON line to
`~/.claude/skill_router_log.jsonl`:

```bash
echo '{"ts":"<ISO8601>","type":"chain-start","name":"<name-or-computed>","steps":[...],"models":[...],"saved":<true|false>}' \
  >> ~/.claude/skill_router_log.jsonl
```

After each step completes:
```bash
echo '{"ts":"<ISO8601>","type":"chain-step","step":<n>,"of":<total>,"skill":"<skill>","model":"<model>","via":"<table|catalog-upgrade|saved>"}' \
  >> ~/.claude/skill_router_log.jsonl
```

When dispatching a thinking-active step:
```bash
echo '{"ts":"<ISO8601>","type":"thinking-active","level":"<ultra|hard|think>","active":true}' \
  >> ~/.claude/skill_router_log.jsonl
```

When the chain ends:
```bash
echo '{"ts":"<ISO8601>","type":"chain-end","name":"<name>"}' \
  >> ~/.claude/skill_router_log.jsonl
```

The statusline reads this file for live progress; `scripts/audit-dispatch.py`
reads it to verify the protocol was followed.

## Verification — am I actually following this?

Run `python3 scripts/audit-dispatch.py` from the repo. It scores recent
chains: how many had full per-step dispatch logged vs how many were
announced but ran in-session. A low score is the signal that the protocol
instruction is being skipped and needs reinforcement.

## Common skip patterns

| Pattern | Why router does it | Fix |
|---|---|---|
| All steps run in-session despite announcement | Easier short-term, but loses model enforcement | Re-read this protocol; if step.model differs from parent, dispatch via Agent |
| Parallel steps run sequentially | Model launched them one at a time | Send a single message with multiple Agent tool calls |
| Step's thinking keyword forgotten | Just bad habit | The keyword is the literal first word of the dispatch prompt — see [`thinking-depth.md`](./thinking-depth.md) |
