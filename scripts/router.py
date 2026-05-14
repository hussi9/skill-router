#!/usr/bin/env python3
"""
router.py — deterministic routing engine for skill-router.

Reads a user prompt on stdin (or from $CLAUDE_USER_INPUT) and emits the
[skill-router] announcement defined in SKILL.md, plus JSONL log lines to
~/.claude/skill_router_log.jsonl.

Wired as a UserPromptSubmit hook so the announcement is deterministic —
not at the model's discretion. The hook can only inject text into the
model's context; whether the suggested skill actually runs is up to the
model. When no triage signal matches, the router stays SILENT rather
than emit a misleading suggestion. Trust hinges on precision.

Exit codes:
  0  = announcement printed (or intentionally silent)
  1  = parse error
"""
from __future__ import annotations
import functools
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LOG = Path.home() / ".claude" / "skill_router_log.jsonl"
PENDING = Path.home() / ".claude" / "skill_router_pending.json"
SKILLS_DIR = Path.home() / ".claude" / "skills"
PLUGINS_DIR = Path.home() / ".claude" / "plugins" / "cache"
# Commands and agents both surface as valid `Skill(skill="<name>")` targets
# from the model's perspective, so the catalog scan must include them.
COMMANDS_DIR = Path.home() / ".claude" / "commands"
AGENTS_DIR = Path.home() / ".claude" / "agents"

# Words that release the iron-rule enforcement when present in the prompt.
# Documented escape hatch — the router stays silent and writes no pending
# state, so all hooks pass through. Use when the user explicitly wants to
# work outside the routed skill (e.g., to override a wrong route).
ESCAPE_MARKERS = ("[no-router]", "[skip-router]", "[router-off]")

# ---- Triage signals ---------------------------------------------------------

def _re(*patterns: str) -> list[re.Pattern[str]]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]

# 'refactor' imperative patterns. Shared by OPERATE_RE (which decides triage)
# and _REFACTOR_RE (which decides the chain inside build_operate_chain) so the
# two can never disagree. Match imperative usage only — bare \brefactor\b is
# the largest source of false positives (123× announced, 0× invoked in 30-day
# production data) because the word appears constantly in conversational
# context ("the recent refactor broke X", "after that refactor").
_REFACTOR_IMPERATIVE_PATTERNS: tuple[str, ...] = (
    r"^\s*(please\s+)?refactor\b",                                          # sentence-initial
    r"\b(can|could|please|let'?s|help (me|us))\s+(\w+\s+){0,2}refactor\b",  # polite/auxiliary
    r"\brefactor\s+(the|this|that|my|our|all|every|some|each)\b",           # refactor <article>
    r"\brefactor\s+[\w/.-]+\.(ts|tsx|js|jsx|py|rb|go|java|cs|cpp|c|rs|swift|kt|md|sql|sh)\b",  # refactor <file>
)

BROKEN_RE = _re(
    r"\berror\b", r"\bcrash(es|ed|ing)?\b", r"\bexception\b",
    r"\btypeerror\b", r"\breferenceerror\b", r"\bsyntaxerror\b",
    r"\btest(s)? (failing|red|broken)\b", r"\bfailing tests?\b",
    r"\b(our |the )?tests? (are|were|got|just) (broken|failing|red)\b",  # 'our tests are broken'
    r"\bproduction (is )?down\b", r"\busers (are )?losing\b",
    r"\b5\d{2} errors?\b", r"\bcritical\b",
    r"\btypescript (is throwing|errors?)\b", r"\btype errors?\b",
    r"\bdeploy (failed|is failing)\b", r"\bbuild (failed|is failing)\b",
    r"\bci (failed|is failing)\b",
    r"\b(this is|you are) wrong\b", r"\bdoesn'?t work\b",
    r"\bbug\b", r"\bregress(ion|ed)\b",
    # Framework compliance errors — violations are broken states, not new features
    r"\bviolations?\b", r"\bnoncompliant\b",
    r"\bsuspense (boundary|wrapper|error|violations?)\b",
    r"\bstatic (render|generation|export) (fail\w*|error)\b",
    r"\bfailed\b",  # 'X failed' is always a broken state
)

BUILD_RE = _re(
    r"\badd a\b",
    # 'add Twilio SMS to ...' / 'add a button for X' — but NOT 'add tests for X'
    # (that's OPERATE). Negative lookahead excludes test/coverage targets.
    r"\badd (a |an |new )?(?!tests?\b|coverage\b)\S+ (to|into|onto|on|for)\b",
    r"\bbuild (a|an|new|some)\b", r"\bcreate (a|an|new)\b",
    r"\bnew (?:\w+\s+){0,3}(feature|component|page|endpoint|route|integration|schema|migration|table|screen)\b",  # 'new graphql schema'
    r"\bimplement\b", r"\bintegrate\b",
    r"\bconnect \w+ (for|to|with|into)\b",  # 'connect Resend for ...'
    r"\bwrite (a |new |a new )?(claude )?skill( file)?\b",
)

OPERATE_RE = _re(
    # 'refactor' imperatives — see _REFACTOR_IMPERATIVE_PATTERNS for rationale.
    *_REFACTOR_IMPERATIVE_PATTERNS,
    r"\bclean(?:ed)?(?:\s+\w+){0,3}\s+up\b",  # clean up, cleaned up, clean it up, clean the auth service up
    r"\btidy\b", r"\bsimplif(y|ies|ied)\b",
    r"\badd (test|coverage|tests)\b",
    r"\bdeploy\b",
    r"\breview my\s+(?:\w+\s+){0,3}(?:pr|pull request)\b",  # 'review my pr', 'review my refactor PR'
    r"\bcode review\b", r"\bpr review\b",
    # 'merge' / 'ship' — ONLY when used as an imperative verb at the start
    # of the prompt, not when referenced ('ship the pricing change',
    # 'merge conflict in main'). Anchored to start-of-prompt.
    r"^\s*(merge|ship)\b",
    r"\b(merge|ship) (this|that|the|my) (pr|branch|feature|change|release)\b",
)

# SKIP — discussion, clarification, factual lookup, single-line reads.
# No length gate: a long discussion message is still a discussion.
# Default for every prompt that doesn't match BROKEN/BUILD/OPERATE.
SKIP_RE = _re(
    # Anchored short questions (factual lookup / explanation)
    r"^\s*what does\b", r"^\s*what is\b", r"^\s*how does\b", r"^\s*how do i\b",
    r"^\s*explain\b", r"^\s*show me\b", r"^\s*where (is|are)\b",
    r"^\s*is there\b", r"^\s*can you (tell|show)\b",
    # Cost / research / analysis questions (with typo tolerance for 'teh')
    r"^\s*what(?:'?s| is| are| was| were)?\s+(?:the\s+|teh\s+)?(?:cost|price|pricing|trade-?offs?|tradeoffs?)\b",
    r"\bhow much (does|do|will) .{0,40} cost\b",
    r"\b(should|do|would) (i|we|you) (use|pick|choose)\b",  # decision questions
    # Discussion / opinion / feedback (anywhere in the prompt)
    r"\bdo you (agree|think|see|have|know|remember|understand)\b",
    r"\bwhat do you think\b",
    r"\bwhat'?s your (idea|take|opinion|thought|view)\b",
    r"\byour (initial|first|prior|earlier|previous)\b",
    r"\b(let me|please) (know|tell|hear)\b",
    r"\bany (questions?|concerns?|thoughts?|ideas?|feedback)\b",
    r"\bbrainstorm\b",
    r"\b(better|alternative|other) ideas?\b",
    r"\bdiscuss(ion)?\b",
    # Harness-injected meta-text — the router must not re-fire when its own
    # output or hook feedback is relayed back as a follow-up prompt. This is
    # what causes the iron rule to trap itself when keywords like 'refactor'
    # or 'ship' echo back in feedback messages.
    r"^\s*Stop hook (feedback|response)\b",
    r"^\s*\[skill-router\]\b",
    r"^\s*PreToolUse:",
    r"^\s*PostToolUse:",
    r"^\s*Hook (blocking|denied) error",
    r"\bhookSpecificOutput\b",
    r"\bIRON RULE\b",  # any prompt that's quoting the IRON RULE wording
    # Session-continuation summaries auto-injected when context overflows.
    # These often quote prior errors / crashes / refactors and must not fire.
    # (Real-prompt sampler caught this as a false-positive on systematic-
    # debugging — 40% of non-SKIP traffic was session recaps.)
    r"^\s*This session is being continued from a previous conversation",
    r"^\s*<task-notification>",
    r"^\s*<task-id>",
    r"^\s*The user (sent|ran|just)",  # harness-injected user-action narration
    # Sub-agent / plugin bootstrap prompts. The real-prompt sampler shows
    # claude-mem and other plugins inject role-instruction text into the
    # transcript ('Hello memory agent...', 'You are a Claude-Mem...',
    # '--- MODE SWITCH: PROGRESS SUMMARY ---'). These are NOT user prompts
    # and route to BROKEN due to broad keywords like \berror\b in their bodies,
    # which is the dominant source of `systematic-debugging` over-firing.
    r"^\s*Hello\s+(?:memory|claude|chat|router)[\s,-]+(?:agent|bot)\b",
    r"^\s*You are (?:a |the |an )?(?:Claude-?Mem|specialized\s+(?:observer|memory|router))",
    r"^\s*---\s*MODE SWITCH",
    r"^\s*<observed_from_primary_session>",
    r"\bCRITICAL TAG REQUIREMENT\b",
    r"^\s*(?:CRITICAL|IMPORTANT):\s+(?:Record|Observe|Watch|Track)\b",
)

# ---- Domain detection -------------------------------------------------------

DOMAINS: dict[str, list[re.Pattern[str]]] = {
    "UI/Frontend":   _re(r"\bcomponent\b", r"\bpage\b", r"\blayout\b", r"\bbutton\b",
                         r"\btoggle\b", r"\bsettings page\b", r"\bui\b", r"\bmobile screen\b",
                         r"\bdark mode\b", r"\bprofile page\b"),
    "DB schema":     _re(r"\bdatabase\b", r"\bschema\b", r"\bmigration\b", r"\brls\b",
                         r"\btable\b", r"\bquery\b", r"\bsupabase\b", r"\bpostgres\b",
                         r"\bwrites? to (the )?db\b", r"\bsaves? to (the )?database\b"),
    "API/Backend":   _re(r"\bendpoint\b", r"\brest api\b", r"\bgraphql\b",
                         r"\brequest handler\b", r"\bserver logic\b"),
    "Edge function": _re(r"\bedge function\b", r"\blambda\b", r"\bwebhook\b", r"\bcron\b",
                         r"\bemails? (the user|on save)\b", r"\bsend(s)? email\b"),
    "Auth":          _re(r"\bauth\b", r"\blogin\b", r"\boauth\b", r"\bpermissions?\b"),
    "Mobile":        _re(r"\bios\b", r"\bandroid\b", r"\bmobile (app|screen)\b",
                         r"\bnative module\b"),
    "Data/AI":       _re(r"\bml\b", r"\bembedding\b", r"\brag\b", r"\bvector db\b",
                         r"\bagent (design|loop)\b"),
    "3rd-party":     _re(r"\bstripe\b", r"\bslack\b", r"\btwilio\b", r"\bplaid\b",
                         r"\bsendgrid\b", r"\bresend\b"),
    "DevOps":        _re(r"\bci/cd\b", r"\binfra\b", r"\benv config\b"),
}

# ---- Routing tables (mirror SKILL.md) ---------------------------------------

@dataclass
class Step:
    skill: str
    agent: str = "general-purpose"
    model: str = "sonnet"
    thinking: str = "none"


DOMAIN_SKILL: dict[str, Step] = {
    "UI/Frontend":   Step("frontend-design:frontend-design", "feature-dev:code-architect", "sonnet", "none"),
    "DB schema":     Step("db-expert", "db-expert", "sonnet", "think"),
    "API/Backend":   Step("feature-dev:feature-dev", "feature-dev:code-architect", "sonnet", "think"),
    "Edge function": Step("vercel:vercel-functions", "integration-specialist", "sonnet", "none"),
    "Auth":          Step("security", "security-auditor", "opus", "ultrathink"),
    "Mobile":        Step("frontend-design:frontend-design", "feature-dev:code-architect", "sonnet", "none"),
    "Data/AI":       Step("superpowers:brainstorming", "feature-dev:code-architect", "sonnet", "think-hard"),
    "3rd-party":     Step("connect-apps", "integration-specialist", "sonnet", "none"),
    "DevOps":        Step("superpowers:writing-plans", "general-purpose", "sonnet", "think"),
}

# 3rd-party catalog upgrade — all named services route to connect-apps (the
# only installed integration skill). Specialist per-service skills are not
# installed; routing to them would produce ghost-skill deadlocks.
CATALOG: dict[re.Pattern[str], str] = {
    re.compile(r"\bstripe\b|\bslack\b|\btwilio\b|\bplaid\b|\bsendgrid\b|\bresend\b",
               re.IGNORECASE): "connect-apps",
}

# ---- Helpers ----------------------------------------------------------------

def any_match(text: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)


def detect_domains(text: str) -> list[str]:
    return [d for d, pats in DOMAINS.items() if any_match(text, pats)]


_PROD_INCIDENT_RE = re.compile(
    r"\b(production (is )?down|users (are )?losing|critical.*production|database corrupted)\b",
    re.IGNORECASE,
)


def production_incident(text: str) -> bool:
    return bool(_PROD_INCIDENT_RE.search(text))


# Top-level action verbs that signal a distinct work intent.
_AMBIGUITY_RE = re.compile(
    r"\b(fix|add|build|create|refactor|deploy|integrate|write|review|implement|clean)\b"
    r"\s+\S.*?\s+(and|AND)\s+(also\s+)?"
    r"\b(fix|add|build|create|refactor|deploy|integrate|write|review|implement|clean)\b",
    re.IGNORECASE,
)


def has_ambiguity(text: str) -> bool:
    """True for genuine multi-intent prompts where AND connects two distinct
    top-level action verbs (e.g. 'fix bug AND add OAuth'). Does NOT flag
    'page that writes to db and emails' — that has 'and' between gerunds
    inside one feature description, not between competing imperative actions.
    """
    return bool(_AMBIGUITY_RE.search(text))


def names_3rd_party_service(text: str) -> bool:
    """A specific 3rd-party service named in the prompt is a strong BUILD
    signal — these are almost always integration work even when no other
    BUILD verb appears (e.g., 'connect Resend for emails'). Without this
    lift, prompts like 'add Twilio SMS to checkout' fall through to SKIP."""
    return any(p.search(text) for p in CATALOG.keys())


def triage(text: str) -> str:
    """Return BROKEN | BUILD | OPERATE | SKIP.

    Default is SKIP — only fire when a strong signal matches. Otherwise
    stay quiet so we don't poison every prompt with a misleading
    'OPERATE → refactor' suggestion that erodes the user's trust in the
    router. Trust requires precision.
    """
    if any_match(text, SKIP_RE):
        return "SKIP"
    # Ambiguity (X AND Y) — check before BROKEN, since 'fix bug AND add Y'
    # routes to BUILD per SKILL.md higher-complexity rule.
    if has_ambiguity(text):
        return "BUILD"
    if any_match(text, BROKEN_RE):
        return "BROKEN"
    if any_match(text, BUILD_RE):
        return "BUILD"
    if any_match(text, OPERATE_RE):
        return "OPERATE"
    # 3rd-party service named without explicit verb → integration work.
    if names_3rd_party_service(text):
        return "BUILD"
    return "SKIP"


def catalog_upgrade(text: str, default_skill: str) -> str:
    """If prompt names a specific 3rd-party service, prefer the specialist."""
    for pat, specialist in CATALOG.items():
        if pat.search(text):
            return specialist
    return default_skill


# ---- Build chain ------------------------------------------------------------

_TESTS_FAILING_RE = re.compile(
    r"\btest(s)? (failing|red|broken)\b"
    r"|\bfailing tests?\b"
    r"|\b(our |the )?tests? (are|were|got|just) (broken|failing|red)\b",
    re.IGNORECASE,
)
_TYPESCRIPT_RE = re.compile(r"\btypescript|type errors?\b", re.IGNORECASE)
_NEW_SKILL_RE = re.compile(r"\bwrite (?:a |new |a new )?(?:claude )?skill(?: file)?\b", re.IGNORECASE)
# Mirror OPERATE_RE refactor imperatives so build_operate_chain agrees with
# triage. Synonyms (clean up / tidy / simplify) stay broad — they're far less
# ambiguous than bare 'refactor'.
_REFACTOR_RE = re.compile(
    "|".join((
        *_REFACTOR_IMPERATIVE_PATTERNS,
        r"\bclean(?:ed)?(?:\s+\w+){0,3}\s+up\b",
        r"\btidy\b",
        r"\bsimplif(y|ies|ied)\b",
    )),
    re.IGNORECASE,
)
_ADD_TESTS_RE = re.compile(r"\badd (tests?|coverage|test coverage)\b", re.IGNORECASE)
_DEPLOY_RE = re.compile(r"\bdeploy\b", re.IGNORECASE)
_REVIEW_RE = re.compile(
    r"\breview my\s+(?:\w+\s+){0,3}(?:pr|pull request)\b"
    r"|\bcode review\b"
    r"|\bpr review\b",
    re.IGNORECASE,
)
_MERGE_SHIP_RE = re.compile(r"\bmerge\b|\bship\b", re.IGNORECASE)


def build_broken_chain(text: str) -> list[Step]:
    if production_incident(text):
        return [Step("superpowers:systematic-debugging", "general-purpose", "opus", "ultrathink")]
    if _TESTS_FAILING_RE.search(text):
        return [Step("test-runner", "test-runner", "sonnet", "none"),
                Step("superpowers:systematic-debugging", "general-purpose", "sonnet", "think")]
    # typescript-expert is not installed; fall through to systematic-debugging
    return [Step("superpowers:systematic-debugging", "general-purpose", "sonnet", "think")]


def build_build_chain(text: str, domains: list[str]) -> list[Step]:
    if has_ambiguity(text):
        return [Step("superpowers:brainstorming", "general-purpose", "sonnet", "none")]
    if _NEW_SKILL_RE.search(text):
        return [Step("superpowers:writing-skills", "general-purpose", "sonnet", "think")]
    if not domains:
        return [Step("superpowers:writing-plans", "feature-dev:code-architect", "sonnet", "think")]
    if len(domains) == 1:
        s = DOMAIN_SKILL[domains[0]]
        if domains[0] == "3rd-party":
            s = Step(catalog_upgrade(text, s.skill), "integration-specialist", "sonnet", "none")
        return [s]
    # Multi-domain build → writing-plans + parallel domain skills
    chain: list[Step] = [Step("superpowers:writing-plans", "general-purpose", "sonnet", "none")]
    parallel = [DOMAIN_SKILL[d] for d in domains]
    parallel = [Step(catalog_upgrade(text, s.skill), s.agent, s.model, s.thinking)
                if s.skill == "integration-specialist" else s for s in parallel]
    chain.extend(parallel)
    return chain


def build_operate_chain(text: str) -> list[Step]:
    if _REFACTOR_RE.search(text):
        return [Step("refactor", "code-simplifier:code-simplifier", "sonnet", "none")]
    if _ADD_TESTS_RE.search(text):
        return [Step("superpowers:test-driven-development", "test-runner", "sonnet", "none")]
    if _DEPLOY_RE.search(text):
        return [Step("superpowers:verification-before-completion", "general-purpose", "sonnet", "none"),
                Step("vercel:deploy", "general-purpose", "sonnet", "none")]
    if _REVIEW_RE.search(text):
        return [Step("superpowers:requesting-code-review", "superpowers:code-reviewer", "sonnet", "think-hard")]
    if _MERGE_SHIP_RE.search(text):
        return [Step("superpowers:finishing-a-development-branch", "general-purpose", "sonnet", "none")]
    # OPERATE_RE matched but no specific subpath — fall back to refactor.
    return [Step("refactor", "code-simplifier:code-simplifier", "sonnet", "none")]


# ---- Render announcement ----------------------------------------------------

THINK_RANK = {"none": 0, "think": 1, "think-hard": 2, "ultrathink": 3}


def max_thinking(steps: list[Step]) -> str:
    return max((s.thinking for s in steps), key=lambda x: THINK_RANK[x])


def iron_rule_block(chain: list[Step]) -> list[str]:
    """Render the IRON RULE instruction block.

    This is the single highest-leverage instruction we inject — system
    messages have very high salience and arrive *before* the model's
    first action. Combined with the PreToolUse / Stop hooks (which read
    `~/.claude/skill_router_pending.json`), this is what turns the
    advisory into a hard rule.
    """
    if not chain:
        return []
    primary = chain[0].skill
    return [
        "",
        "[skill-router] IRON RULE — your next tool call MUST be:",
        f"[skill-router]   Skill(skill=\"{primary}\")",
        "[skill-router] Other state-changing tools (Bash, Edit, Write, Task) are blocked",
        "[skill-router] until this skill runs. Read/Glob/Grep/TodoWrite stay allowed.",
        "[skill-router] Override: the USER must type [no-router] in their next message.",
        "[skill-router]   Writing [no-router] in your own response does NOT clear pending.",
    ]


def render(path: str, chain: list[Step], domains: list[str]) -> str:
    """Render the [skill-router] announcement. Empty string if SKIP.

    The closing `▶` marker(s) tell the model which skill(s) to invoke.
    The hook only injects text — it can't force a tool call — so the
    announcement reads as an instruction ('Invoke now:') rather than a
    status claim ('Dispatching now...'). The model is the one who
    actually dispatches by calling the Skill tool.
    """
    if path == "SKIP" or not chain:
        return ""

    n = len(chain)
    is_multi = (path == "BUILD" and len(domains) >= 2 and n >= 2
                and chain[0].skill == "superpowers:writing-plans")

    out: list[str] = []

    if is_multi:
        out.append(f"[skill-router] This touches {len(domains)} domains: {', '.join(domains)}.")
        chain_display = f"{chain[0].skill} → {' + '.join(s.skill for s in chain[1:])}"
        out.append(f"[skill-router] Chain: {chain_display}")
        parallel_models = "+".join(s.model for s in chain[1:])
        models_display = f"{chain[0].model} · {parallel_models}"
        thinking = max_thinking(chain)
        if thinking != "none":
            out.append(f"[skill-router] Models: {models_display}  ·  Thinking: {thinking}")
        else:
            out.append(f"[skill-router] Models: {models_display}")
        out.append(f"[skill-router] Invoke step 1/2 now:")
        out.append("")
        out.append(f"▶ {chain[0].skill}  ({chain[0].model}, in-session)")
        domain_skills = " + ".join(s.skill for s in chain[1:])
        out.append(f"▶ {domain_skills}  ({chain[1].model}, parallel via Agent)")
        out.extend(iron_rule_block(chain))
        return "\n".join(out)

    if n == 1:
        s = chain[0]
        out.append(f"[skill-router] This is a {path} task → {s.skill} → {s.agent}.")
        if s.thinking != "none":
            out.append(f"[skill-router] Model: {s.model}  ·  Thinking: {s.thinking}")
        else:
            out.append(f"[skill-router] Model: {s.model}")
        out.append(f"[skill-router] Invoke now:")
        out.append("")
        out.append(f"▶ {s.skill}  ({s.model}, in-session)")
        out.extend(iron_rule_block(chain))
        return "\n".join(out)

    # Sequential N-step (e.g. test-runner → systematic-debugging, verify → deploy)
    out.append(f"[skill-router] This is a {path} task — {n}-step chain.")
    out.append("[skill-router] Chain: " + " → ".join(s.skill for s in chain))
    models_display = " · ".join(s.model for s in chain)
    thinking = max_thinking(chain)
    if thinking != "none":
        out.append(f"[skill-router] Models: {models_display}  ·  Thinking: {thinking}")
    else:
        out.append(f"[skill-router] Models: {models_display}")
    out.append(f"[skill-router] Invoke step 1/{n} now:")
    out.append("")
    for s in chain:
        out.append(f"▶ {s.skill}  ({s.model}, in-session)")
    out.extend(iron_rule_block(chain))
    return "\n".join(out)


# ---- Iron-rule pending state -----------------------------------------------

def escape_active(prompt: str) -> bool:
    """True if the prompt contains an escape marker that disables the iron rule."""
    lower = prompt.lower()
    return any(m in lower for m in ESCAPE_MARKERS)


def write_pending(chain: list[Step], path: str, domains: list[str]) -> None:
    """Persist the announced skill chain so PreToolUse / Stop hooks can enforce it.

    State file shape:
      {
        "ts": "...",
        "primary": "<first announced skill>",
        "remaining": ["<in-session steps only>"],
        "all": ["<all announced steps>"]
      }

    Only in-session Skill() calls are tracked in .remaining. Parallel
    agent-dispatched steps (the domain skills in multi-domain BUILD chains)
    are excluded — they run inside sub-agents and never call Skill() in the
    parent session, so leaving them in .remaining would deadlock the Stop hook.
    """
    if not chain:
        return
    # Multi-domain BUILD: chain[0] is in-session (writing-plans), chain[1:]
    # are parallel fan-outs dispatched via Agent(). Only track chain[0].
    is_multi = (path == "BUILD" and len(domains) >= 2 and len(chain) >= 2
                and chain[0].skill == "superpowers:writing-plans")
    in_session = chain[:1] if is_multi else chain
    PENDING.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "primary": in_session[0].skill,
        "remaining": [s.skill for s in in_session],
        "all": [s.skill for s in chain],
    }
    PENDING.write_text(json.dumps(payload) + "\n")


def clear_pending() -> None:
    """Clear the pending-state file. Called at the start of each user turn so
    nothing carries over across turns and a misroute cannot deadlock."""
    if PENDING.is_file():
        PENDING.write_text("{}\n")


# ---- Skill catalog (ghost-skill guard) -------------------------------------

# Skills that are valid Skill() targets but live outside the standard on-disk
# layouts (e.g. vercel:deploy ships via the vercel plugin). Add entries here
# ONLY after confirming Skill(skill="<name>") actually succeeds in practice.
ROUTED_SKILL_ALIASES: frozenset[str] = frozenset({
    "vercel:deploy",
})


@functools.lru_cache(maxsize=1)
def _skill_catalog() -> Optional[set[str]]:
    """Return the set of skill names that exist on disk, or None if the
    catalog can't be loaded (so callers fail open).

    Layouts scanned:
      1. ~/.claude/skills/<name>/        → bare name (e.g., 'refactor')
      2. ~/.claude/commands/<name>.md    → bare name (slash-command)
      3. ~/.claude/agents/<name>.md      → bare name (subagent)
      4. ~/.claude/plugins/cache/<repo>/<plugin>/<version>/skills/<skill>/SKILL.md
         → namespaced as '<plugin>:<skill>' AND bare '<skill>'

    All of these surface as valid `Skill(skill="<name>")` targets in the
    Claude Code harness. Plus a static ROUTED_SKILL_ALIASES whitelist for
    routing-table entries that resolve via marketplace / model-side aliases.

    The result is cached for the lifetime of the process — the catalog
    doesn't change between hook invocations within a single turn, and the
    hook is short-lived enough that staleness doesn't matter.
    """
    catalog: set[str] = set(ROUTED_SKILL_ALIASES)
    found_any = False

    # Bare skills under ~/.claude/skills/
    if SKILLS_DIR.is_dir():
        try:
            for entry in SKILLS_DIR.iterdir():
                if entry.is_dir() and not entry.name.startswith("."):
                    catalog.add(entry.name)
                    found_any = True
        except OSError:
            pass

    # Slash-commands under ~/.claude/commands/<name>.md
    if COMMANDS_DIR.is_dir():
        try:
            for entry in COMMANDS_DIR.iterdir():
                if entry.is_file() and entry.suffix == ".md":
                    catalog.add(entry.stem)
                    found_any = True
        except OSError:
            pass

    # Subagents under ~/.claude/agents/<name>.md
    if AGENTS_DIR.is_dir():
        try:
            for entry in AGENTS_DIR.iterdir():
                if entry.is_file() and entry.suffix == ".md":
                    catalog.add(entry.stem)
                    found_any = True
        except OSError:
            pass

    # Plugin skills under ~/.claude/plugins/cache/*/<plugin>/*/skills/<skill>/SKILL.md
    # and plugin commands under ~/.claude/plugins/cache/*/<plugin>/*/commands/<cmd>.md
    if PLUGINS_DIR.is_dir():
        try:
            for repo_dir in PLUGINS_DIR.iterdir():
                if not repo_dir.is_dir():
                    continue
                for plugin_dir in repo_dir.iterdir():
                    if not plugin_dir.is_dir():
                        continue
                    plugin_name = plugin_dir.name
                    for version_dir in plugin_dir.iterdir():
                        if not version_dir.is_dir():
                            continue
                        # skills/<skill>/SKILL.md
                        skills_root = version_dir / "skills"
                        if skills_root.is_dir():
                            for skill_dir in skills_root.iterdir():
                                if not skill_dir.is_dir():
                                    continue
                                if (skill_dir / "SKILL.md").is_file():
                                    catalog.add(f"{plugin_name}:{skill_dir.name}")
                                    catalog.add(skill_dir.name)
                                    found_any = True
                        # commands/<cmd>.md — e.g. feature-dev plugin uses this layout
                        cmds_root = version_dir / "commands"
                        if cmds_root.is_dir():
                            for cmd_file in cmds_root.iterdir():
                                if cmd_file.is_file() and cmd_file.suffix == ".md":
                                    catalog.add(f"{plugin_name}:{cmd_file.stem}")
                                    catalog.add(cmd_file.stem)
                                    found_any = True
        except OSError:
            pass

    if not found_any:
        return None
    return catalog


def valid_skill(name: str) -> bool:
    """True if `name` is in the installed skill catalog.

    Fail-open: if the catalog can't be enumerated (no skills dir, OS error),
    return True so we don't suppress legitimate routes when verification is
    impossible. The guard exists to catch typos and stale references — not
    to second-guess a working install.
    """
    if not name:
        return False
    catalog = _skill_catalog()
    if catalog is None:
        return True
    return name in catalog


# ---- Logging ----------------------------------------------------------------

def log_chain(path: str, chain: list[Step], domains: list[str]) -> None:
    if path == "SKIP" or not chain:
        return
    LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    name = f"{path.lower()}-{'-'.join(domains).lower() or 'single'}"
    name = re.sub(r"[^a-z0-9-]", "", name)[:40]
    with LOG.open("a") as f:
        f.write(json.dumps({
            "ts": ts, "type": "chain-start", "name": name,
            "steps": [s.skill for s in chain],
            "models": [s.model for s in chain],
            "saved": False, "via": "router-hook",
        }) + "\n")
        for i, s in enumerate(chain, 1):
            f.write(json.dumps({
                "ts": ts, "type": "chain-step",
                "step": i, "of": len(chain),
                "skill": s.skill, "model": s.model, "via": "table",
            }) + "\n")
            if s.thinking != "none":
                f.write(json.dumps({
                    "ts": ts, "type": "thinking-active",
                    "level": s.thinking, "active": True,
                }) + "\n")
        f.write(json.dumps({
            "ts": ts, "type": "chain-end", "name": name,
        }) + "\n")


# ---- Entry point ------------------------------------------------------------

def route(prompt: str) -> tuple[str, list[Step], list[str], str]:
    """Return (path, chain, domains, announcement)."""
    domains = detect_domains(prompt)
    path = triage(prompt)
    if path == "SKIP":
        # Local embedding fallback — fail-open. The daemon is local-only
        # (Unix socket, fastembed ONNX, zero network). If it's down, missing,
        # or low-confidence, we keep today's silent SKIP behavior. The
        # fallback can NEVER override a confident regex match because the
        # regex always runs first.
        rescued = _try_embedding_fallback(prompt)
        if rescued is not None:
            path, chain, domains = rescued
            ghost = next((s.skill for s in chain if not valid_skill(s.skill)), None)
            if ghost is None:
                return path, chain, domains, render(path, chain, domains)
            print(f"[skill-router-warn] embedding ghost skill: {ghost}", file=sys.stderr)
        return "SKIP", [], domains, ""
    if path == "BROKEN":
        chain = build_broken_chain(prompt)
    elif path == "BUILD":
        chain = build_build_chain(prompt, domains)
    else:
        chain = build_operate_chain(prompt)
    # Ghost-skill guard: if any step references an uninstalled skill, drop
    # the whole chain rather than announce a name the model can't invoke.
    # Better silent than misleading. Only kicks in when the catalog loads —
    # `valid_skill` fails open if it can't be enumerated.
    ghost = next((s.skill for s in chain if not valid_skill(s.skill)), None)
    if ghost is not None:
        print(f"[skill-router-warn] skipping ghost skill: {ghost}", file=sys.stderr)
        return "SKIP", [], domains, ""
    return path, chain, domains, render(path, chain, domains)


def _try_embedding_fallback(prompt: str) -> Optional[tuple[str, list[Step], list[str]]]:
    """Ask the local embedder daemon to rescue a SKIP-classified prompt.

    Returns (path, chain, domains) if the daemon returns a confident match
    that matches an existing route table entry, or None to keep silent SKIP.

    Active only in the real hook path (SKILL_ROUTER_HOOK_MODE=1) or explicit
    embedder tests/manual probes (SKILL_ROUTER_EMBED=1). Disabled entirely
    when SKILL_ROUTER_NO_EMBED=1 is set.
    """
    if os.environ.get("SKILL_ROUTER_NO_EMBED") == "1":
        return None
    if (
        os.environ.get("SKILL_ROUTER_HOOK_MODE") != "1"
        and os.environ.get("SKILL_ROUTER_EMBED") != "1"
    ):
        return None
    try:
        # Lazy import — keeps router import side-effect-free for tests that
        # don't need the embedder, and avoids any startup cost when the
        # SKIP path doesn't fire.
        from embedder_client import classify  # type: ignore[import-not-found]
    except ImportError:
        return None
    result = classify(prompt)
    if not result or result.get("path") not in {"BROKEN", "BUILD", "OPERATE"}:
        _log_embedding_attempt(prompt, result, accepted=False)
        return None
    path = result["path"]
    skill = result.get("skill", "")
    if not skill or not valid_skill(skill):
        _log_embedding_attempt(prompt, result, accepted=False, rejected_skill=skill or None)
        return None

    # Build a single-step chain matching the embedder's recommendation.
    # We do NOT trust the embedder to do multi-domain build chains — those
    # require domain detection, which the regex layer already does. Single-
    # step is the safe wedge.
    if path == "BROKEN":
        chain = [Step(skill, "general-purpose", "sonnet", "think")]
    elif path == "BUILD":
        chain = [Step(skill, "feature-dev:code-architect", "sonnet", "think")]
    else:  # OPERATE
        chain = [Step(skill, "general-purpose", "sonnet", "none")]

    _log_embedding_attempt(prompt, result, accepted=True)

    return path, chain, []


def _log_embedding_attempt(
    prompt: str,
    result: Optional[dict],
    *,
    accepted: bool,
    rejected_skill: Optional[str] = None,
) -> None:
    """Log every daemon response so dogfood data can tune recall safely.

    Prompt text is private by default: store a hash and length. Set
    SKILL_ROUTER_LOG_EMBED_PROMPTS=1 for short local tuning sessions when
    reviewing raw prompts is useful.
    """
    if not result:
        return
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        prompt_hash = hashlib.sha256(
            prompt.encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "type": "embedding-route" if accepted else "embedding-skip",
            "accepted": accepted,
            "path": result.get("path"),
            "reason": result.get("reason"),
            "skill": result.get("skill"),
            "rejected_skill": rejected_skill,
            "confidence": result.get("confidence"),
            "agreement": result.get("agreement"),
            "winner_count": result.get("winner_count"),
            "avg_sim": result.get("avg_sim"),
            "winner_avg_sim": result.get("winner_avg_sim"),
            "runner_up_avg_sim": result.get("runner_up_avg_sim"),
            "margin": result.get("margin"),
            "ms": result.get("_ms"),
            "prompt_hash": prompt_hash,
            "prompt_len": len(prompt),
            "neighbors": result.get("neighbors"),
        }
        if os.environ.get("SKILL_ROUTER_LOG_EMBED_PROMPTS") == "1":
            payload["prompt"] = prompt
        with LOG.open("a") as f:
            f.write(json.dumps(payload) + "\n")
    except OSError:
        pass


def main() -> int:
    prompt = os.environ.get("CLAUDE_USER_INPUT", "") or sys.stdin.read()
    prompt = prompt.strip()
    # Hook-mode gate: only the UserPromptSubmit hook should mutate the live
    # iron-rule state. CLI invocations (testing, scripts, dashboards) must not
    # poison ~/.claude/skill_router_pending.json — that would block tools in
    # the user's active session. The hook command sets SKILL_ROUTER_HOOK_MODE=1.
    hook_mode = os.environ.get("SKILL_ROUTER_HOOK_MODE") == "1"
    # Always reset pending state at turn start — prevents a stale entry from a
    # previous turn from blocking this turn's tools, and means a misrouted
    # turn naturally clears itself when the user types a follow-up.
    if hook_mode:
        clear_pending()
    if not prompt:
        return 0
    # Escape hatch: user explicitly opts out of routing for this turn.
    if escape_active(prompt):
        return 0
    try:
        path, chain, domains, announcement = route(prompt)
    except Exception as e:
        print(f"[skill-router-error] {e}", file=sys.stderr)
        return 1
    if announcement:
        print(announcement)
        log_chain(path, chain, domains)
        if hook_mode:
            write_pending(chain, path, domains)
    elif os.environ.get("SKILL_ROUTER_DEBUG") == "1":
        print(f"[skill-router] (silent — no clear route for prompt of {len(prompt)} chars)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
