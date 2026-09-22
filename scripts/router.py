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
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LOG = Path.home() / ".claude" / "skill_router_log.jsonl"
PENDING = Path.home() / ".claude" / "skill_router_pending.json"
STRIKES = Path.home() / ".claude" / "skill_router_strikes.json"
# Reasoned-override state — the ask+learn loop. When the model judges a route
# wrong, it records *why* via scripts/router_override.py instead of silently
# fighting the IRON RULE (or the user having to type [no-router]). Two files:
#   OVERRIDES_LOG   — append-only audit trail, one JSON line per override, so
#                     scripts/weekly-analysis.sh can surface why routes get
#                     rejected, not just that they do.
#   OVERRIDES_COUNT — per-skill reasoned-override tally, consumed by
#                     is_overridden() to defer a skill the model keeps correcting.
OVERRIDES_LOG = Path.home() / ".claude" / "skill_router_overrides.jsonl"
OVERRIDES_COUNT = Path.home() / ".claude" / "skill_router_overrides_count.json"
# Local + online catalog snapshots. Local lists installed skills; online lists
# uninstallable-but-discoverable skills (antigravity, anthropic marketplace,
# etc.). The online catalog powers the "you don't have this but it'd fit"
# soft-suggestion path — strictly local file reads, no network.
LOCAL_CATALOG_FILE = Path.home() / ".claude" / "skill_router_catalog.json"
ONLINE_CATALOG_FILE = Path.home() / ".claude" / "skill_router_online_catalog.json"
# Online-suggestion confidence floor — see suggest_online_skill() for scoring.
# 0.5 = at least half the prompt tokens overlap with the skill text or vice
# versa (whichever is smaller). Tuned to suppress noise on short prompts.
ONLINE_SUGGEST_THRESHOLD = 0.5
# Minimum prompt-token overlap count required before scoring kicks in. Below
# 3 overlapping tokens (≥4 chars each), even a high Jaccard is too noisy on
# short skill descriptions to trust.
ONLINE_SUGGEST_MIN_OVERLAP = 3
# A skill that has `STRIKE_THRESHOLD` consecutive unsatisfied announcements
# (turn ended with skill in pending state and never invoked) moves to SOFT
# mode: silently dropped from future announcements until a successful invoke
# resets its counter. Self-tuning — bad routes auto-demote, good ones recover
# the moment they're actually used.
# Four, not two. A silent miss is weak evidence: the announcement may have
# been right and the user changed topic, or interrupted, or the turn ended in a
# question. Two of those in a row was enough to silence a skill for over a
# week — a test loop that ran the hook three times without invoking anything
# demoted `systematic-debugging` and `youtube-manager` outright. Demotion must
# require a pattern, not a coincidence.
STRIKE_THRESHOLD = 4
# Deferral half-life. Both strike-based and override-based demotions expire
# after this many days.
#
# Why this exists: deferral used to be permanent. Two reasoned overrides ever
# — even overrides recorded for a reason that has since stopped applying, like
# "the user is running autonomously today so an interactive planning skill is
# wrong" — silently removed a skill from routing forever. On this machine that
# had quietly killed systematic-debugging, writing-plans, brainstorming,
# requesting-code-review and frontend-design, which is to say the entire BROKEN
# path and most of BUILD. The router looked healthy and answered SKIP to
# everything. A demotion must be a cooldown, never a tombstone.
# Two TTLs, because the two signals differ in strength. A reasoned override is
# the model stating, with a recorded reason, that the route was wrong; that
# deserves to be remembered for a while. A silent miss is ambiguous and should
# be forgotten quickly.
DEFER_TTL_DAYS = 10       # reasoned overrides
STRIKE_TTL_DAYS = 3       # silent misses
# A reasoned override is a STRONGER signal than a silent miss — the model
# explicitly said the route was wrong and stated why. But override counts are
# keyed per-skill (not per-prompt), so we still require a small pattern before
# deferring a generally-useful skill from one or two corrections. At
# OVERRIDE_THRESHOLD reasoned overrides a skill drops to SOFT mode until a
# successful invoke re-arms it. Coarse by design; the reasons in OVERRIDES_LOG
# let the weekly analysis (or a human) make finer calls.
OVERRIDE_THRESHOLD = 2
SKILLS_DIR = Path.home() / ".claude" / "skills"
PLUGINS_DIR = Path.home() / ".claude" / "plugins" / "cache"
# Slash-commands DO surface as valid `Skill(skill="<name>")` targets.
# Sub-agents do NOT — they are dispatched with the Agent tool, and
# `Skill(skill="db-expert")` fails. Treating the agents directory as part of
# the skill catalog is what let the failing-test route announce the
# uninvokable `test-runner`, so the agent scan lives in a separate function
# and never feeds valid_skill().
COMMANDS_DIR = Path.home() / ".claude" / "commands"
AGENTS_DIR = Path.home() / ".claude" / "agents"

# Words that release the iron-rule enforcement when present in the prompt.
# Documented escape hatch — the router stays silent and writes no pending
# state, so all hooks pass through. Use when the user explicitly wants to
# work outside the routed skill (e.g., to override a wrong route).
ESCAPE_MARKERS = ("[no-router]", "[skip-router]", "[router-off]")

# An explicit slash-command invocation: the user already chose the skill, so the
# router must stand down — no classification, no embedder rescue, no IRON rule.
# Matches one leading command segment terminated by whitespace or end of string:
# "/gstack", "/ship prod", "/feature-dev:feature-dev". Deliberately does NOT
# match a filesystem path like "/Users/airbook/x.py" — there the segment is
# followed by "/", not whitespace/end, so the prompt routes normally. Hijacking
# an explicit command into a *different* skill was the original "router fights
# the user" bug.
EXPLICIT_INVOCATION_RE = re.compile(r"^\s*/[A-Za-z][\w-]*(?::[\w-]+)*(?:\s|$)")

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
    # Harness text that arrives as a user turn: the skill it names is already loaded.
    r"^\s*\(Re-invocation of /",
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
    """One routed step: which skill, which agent runs it, at what model and depth.

    `model` is one of:

      inherit  run in the parent session at whatever model the user chose.
               The default, and correct for nearly every step.
      haiku    dispatch to a sub-agent on the cheap model. Only for bulk
               read-only work (repo scans, log greps) where the answer is a
               list of file paths, not a judgment.

    The table used to name `sonnet` on almost every row and `opus` on the hard
    ones. That was written when the parent was always Sonnet, so 'sonnet'
    silently meant 'inherit'. It stopped meaning that: this session runs Fable,
    and the dispatch protocol reads "step model != parent model" as "fan out to
    a sub-agent", so every routed step would have been shipped to a *weaker*
    model than the one the user is paying for. Depth is now expressed through
    `thinking`, which composes with any model, instead of through a model name
    that only held for one family.
    """
    skill: str
    agent: str = "general-purpose"
    model: str = "inherit"
    thinking: str = "none"


DOMAIN_SKILL: dict[str, Step] = {
    "UI/Frontend":   Step("frontend-design:frontend-design", "feature-dev:code-architect", "inherit", "none"),
    "DB schema":     Step("supabase:supabase", "db-expert", "inherit", "think"),
    "API/Backend":   Step("feature-dev:feature-dev", "feature-dev:code-architect", "inherit", "think"),
    "Edge function": Step("vercel:vercel-functions", "integration-specialist", "inherit", "none"),
    "Auth":          Step("security", "security-auditor", "inherit", "ultrathink"),
    "Mobile":        Step("frontend-design:frontend-design", "feature-dev:code-architect", "inherit", "none"),
    "Data/AI":       Step("superpowers:writing-plans", "feature-dev:code-architect", "inherit", "think-hard"),
    "3rd-party":     Step("connect-apps", "integration-specialist", "inherit", "none"),
    "DevOps":        Step("superpowers:writing-plans", "general-purpose", "inherit", "think"),
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
        return [Step("superpowers:systematic-debugging", "general-purpose", "inherit", "ultrathink")]
    if _TESTS_FAILING_RE.search(text):
        # One step, not two. The old first step announced Skill(skill="test-runner"),
        # but test-runner is a sub-agent, not a skill — the call fails and the IRON
        # RULE then blocks every edit waiting for a skill that cannot be invoked.
        # The agent is where test-runner belongs.
        return [Step("superpowers:systematic-debugging", "test-runner", "inherit", "think")]
    return [Step("superpowers:systematic-debugging", "general-purpose", "inherit", "think")]


def build_build_chain(text: str, domains: list[str]) -> list[Step]:
    if has_ambiguity(text):
        return [Step("superpowers:brainstorming", "general-purpose", "inherit", "none")]
    if _NEW_SKILL_RE.search(text):
        return [Step("superpowers:writing-skills", "general-purpose", "inherit", "think")]
    if not domains:
        return [Step("superpowers:writing-plans", "feature-dev:code-architect", "inherit", "think")]
    if len(domains) == 1:
        s = DOMAIN_SKILL[domains[0]]
        if domains[0] == "3rd-party":
            specialist = catalog_upgrade(text, s.skill)
            if valid_skill(specialist):
                s = Step(specialist, "integration-specialist", "inherit", "none")
            else:
                # The integration skill is archived or uninstalled. A missing
                # specialist must not silence routing on the whole prompt —
                # the ghost guard would drop the chain and say nothing — so
                # degrade to the generic plan step instead.
                s = Step("superpowers:writing-plans", "integration-specialist", "inherit", "think")
        return [s]
    # Multi-domain build → writing-plans + parallel domain skills
    chain: list[Step] = [Step("superpowers:writing-plans", "general-purpose", "inherit", "none")]
    parallel = [DOMAIN_SKILL[d] for d in domains]
    parallel = [Step(catalog_upgrade(text, s.skill), s.agent, s.model, s.thinking)
                if s.skill == "integration-specialist" else s for s in parallel]
    chain.extend(parallel)
    return chain


def build_operate_chain(text: str) -> list[Step]:
    if _REFACTOR_RE.search(text):
        return [Step("refactor", "code-simplifier:code-simplifier", "inherit", "none")]
    if _ADD_TESTS_RE.search(text):
        return [Step("superpowers:test-driven-development", "test-runner", "inherit", "none")]
    if _DEPLOY_RE.search(text):
        return [Step("superpowers:verification-before-completion", "general-purpose", "inherit", "none"),
                Step("vercel:deploy", "general-purpose", "inherit", "none")]
    if _REVIEW_RE.search(text):
        return [Step("superpowers:requesting-code-review", "code-reviewer", "inherit", "think-hard")]
    if _MERGE_SHIP_RE.search(text):
        return [Step("superpowers:finishing-a-development-branch", "general-purpose", "inherit", "none")]
    # OPERATE_RE matched but no specific subpath — fall back to refactor.
    return [Step("refactor", "code-simplifier:code-simplifier", "inherit", "none")]


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
    card = LAST_CARD
    # render() is also called directly (tests, dashboards) with a chain the
    # last _finish never saw. A card that does not belong to this chain is
    # stale: derive the tier from the path instead of trusting it.
    if card.primary != primary:
        card = RouteCard(tier=_tier_for(_path_of(chain), ()), primary=primary)
    out: list[str] = [""]
    if card.gates:
        out.append("[skill-router] Gates before done: " + " · ".join(card.gates[:3]))
    if card.memory:
        out.append("[skill-router] Memory: " + ", ".join(card.memory[:2])
                   + "  (read from ~/.claude/projects/-Users-airbook/memory/)")
    # Four lines, not nine. This block is injected on every routed turn, so
    # every line is a permanent tax on the context window. State the rule,
    # name the call, name the way out, stop.
    if card.tier == "hard":
        out += [
            f"[skill-router] IRON RULE: call Skill(skill=\"{primary}\") before any "
            f"Edit/Write/Task.",
            "[skill-router] Read/Glob/Grep/Bash/TodoWrite/Skill stay allowed.",
            "[skill-router] Wrong call? scripts/router_override.py \"<reason>\" clears it "
            "and teaches the router.",
        ]
    else:
        out += [
            f"[skill-router] Soft route: call Skill(skill=\"{primary}\") before editing. "
            f"Skipping it is allowed; the Stop hook will ask once why, and the answer "
            f"teaches the router. Wrong call? scripts/router_override.py \"<reason>\".",
        ]
    return out


def _path_of(chain: list[Step]) -> str:
    """Best guess of the path a bare chain belongs to, for direct render() calls."""
    if chain and chain[0].skill in ("superpowers:systematic-debugging", "systematic-debugging"):
        return "BROKEN"
    return "OPERATE"


def _model_label(model: str) -> str:
    """How a step's model reads in the announcement.

    `inherit` is not a model name the user picked, so printing it verbatim
    invites the reader to wonder which model that is. Say what actually
    happens instead: the step runs in this session, at this session's model.
    """
    return "in-session" if model == "inherit" else model


def _dispatch_label(step: Step, parallel: bool = False) -> str:
    """The trailing `(...)` on a ▶ line: the dispatch decision, made once here
    so the announcement and the protocol can never disagree."""
    if parallel:
        # Parallel steps always go through Agent — not for the model, but
        # because they need independent contexts to run at the same time.
        return f"{step.model}, parallel via Agent"
    if step.model == "inherit":
        return "inherit, in-session"
    return f"{step.model}, via Agent"


def _models_line(chain: list[Step]) -> str:
    """The `Models:` value. Collapses to one phrase when nothing is overridden,
    because 'in-session · in-session · in-session' says the same thing three
    times and reads like a bug."""
    if all(s.model == "inherit" for s in chain):
        return "inherit (this session)"
    return " · ".join(_model_label(s.model) for s in chain)


def render(path: str, chain: list[Step], domains: list[str], note: str = "") -> str:
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
        if note:
            out.append(f"[skill-router] Using your {note}.")
        out.append(f"[skill-router] This touches {len(domains)} domains: {', '.join(domains)}.")
        chain_display = f"{chain[0].skill} → {' + '.join(s.skill for s in chain[1:])}"
        out.append(f"[skill-router] Chain: {chain_display}")
        models_display = _models_line(chain)
        thinking = max_thinking(chain)
        if thinking != "none":
            out.append(f"[skill-router] Models: {models_display}  ·  Thinking: {thinking}")
        else:
            out.append(f"[skill-router] Models: {models_display}")
        out.append(f"[skill-router] Invoke step 1/2 now:")
        out.append("")
        out.append(f"▶ {chain[0].skill}  ({_dispatch_label(chain[0])})")
        domain_skills = " + ".join(s.skill for s in chain[1:])
        out.append(f"▶ {domain_skills}  ({_dispatch_label(chain[1], parallel=True)})")
        out.extend(iron_rule_block(chain))
        return "\n".join(out)

    if n == 1:
        s = chain[0]
        if note:
            out.append(f"[skill-router] Using your {note}.")
        out.append(f"[skill-router] This is a {path} task → {s.skill} → {s.agent}.")
        if s.thinking != "none":
            out.append(f"[skill-router] Model: {_model_label(s.model)}  ·  Thinking: {s.thinking}")
        else:
            out.append(f"[skill-router] Model: {_model_label(s.model)}")
        out.append(f"[skill-router] Invoke now:")
        out.append("")
        out.append(f"▶ {s.skill}  ({_dispatch_label(s)})")
        out.extend(iron_rule_block(chain))
        return "\n".join(out)

    # Sequential N-step (e.g. test-runner → systematic-debugging, verify → deploy)
    if note:
        out.append(f"[skill-router] Using your {note}.")
    out.append(f"[skill-router] This is a {path} task — {n}-step chain.")
    out.append("[skill-router] Chain: " + " → ".join(s.skill for s in chain))
    models_display = _models_line(chain)
    thinking = max_thinking(chain)
    if thinking != "none":
        out.append(f"[skill-router] Models: {models_display}  ·  Thinking: {thinking}")
    else:
        out.append(f"[skill-router] Models: {models_display}")
    out.append(f"[skill-router] Invoke step 1/{n} now:")
    out.append("")
    for s in chain:
        out.append(f"▶ {s.skill}  ({_dispatch_label(s)})")
    out.extend(iron_rule_block(chain))
    return "\n".join(out)


# ---- Iron-rule pending state -----------------------------------------------

def escape_active(prompt: str) -> bool:
    """True if the prompt contains an escape marker that disables the iron rule."""
    lower = prompt.lower()
    return any(m in lower for m in ESCAPE_MARKERS)


def explicit_invocation(prompt: str) -> bool:
    """True if the prompt is an explicit slash-command invocation (e.g. '/gstack',
    '/ship prod', '/feature-dev:feature-dev'). The user has already chosen the
    skill, so the router stands down — it must never reclassify an explicit
    command into a different skill. Filesystem paths ('/Users/...') are not
    matched and route normally. See EXPLICIT_INVOCATION_RE."""
    return bool(EXPLICIT_INVOCATION_RE.match(prompt))


def write_session_route(chain: list[Step], path: str, meta: Optional[dict]) -> None:
    """What sub-agents and the Task hook read: the parent's current route.

    One file per session id under ~/.claude/skill_router_session/, plus
    latest.json for callers that have no session id. Fail-soft."""
    meta = meta or {}
    sid = str(meta.get("session_id") or "").strip()
    card = LAST_CARD
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session_id": sid or None,
        "path": path,
        "skills": [s.skill for s in chain],
        "primary": chain[0].skill if chain else None,
        "tier": card.tier,
        "gates": list(card.gates),
        "memory": list(card.memory),
        "decided_by": card.decided_by,
        "work": card.work or None,
        "work_confidence": round(card.work_confidence, 2),
        "model": card.model,
    }
    # Resolved at call time so a test module can point it somewhere hermetic
    # after this module was imported.
    session_dir = Path(os.environ.get("SKILL_ROUTER_SESSION_DIR") or SESSION_DIR)
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload) + "\n"
        (session_dir / "latest.json").write_text(text)
        if sid:
            safe = re.sub(r"[^A-Za-z0-9_-]", "_", sid)[:80]
            (session_dir / f"{safe}.json").write_text(text)
    except OSError:
        pass


def write_pending(chain: list[Step], path: str, domains: list[str],
                  meta: Optional[dict] = None) -> None:
    """Persist the announced skill chain so PreToolUse / Stop hooks can enforce it.

    State file shape:
      {
        "ts": "...",
        "primary": "<first announced skill>",
        "remaining": ["<in-session steps only>"],
        "all": ["<all announced steps>"],
        "tier": "hard" | "soft",
        "session_id": "...", "prompt_id": "..."
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
    meta = meta or {}
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "primary": in_session[0].skill,
        "remaining": [s.skill for s in in_session],
        "all": [s.skill for s in chain],
        "tier": LAST_CARD.tier,
        "decided_by": LAST_CARD.decided_by,
        "session_id": meta.get("session_id"),
        "prompt_id": meta.get("prompt_id"),
    }
    PENDING.write_text(json.dumps(payload) + "\n")
    write_session_route(chain, path, meta)


def clear_pending() -> None:
    """Clear the pending-state file. Called at the start of each user turn so
    nothing carries over across turns and a misroute cannot deadlock.

    Side effect: any skills still in `.remaining` when this fires were
    announced but never invoked — each one gets +1 strike. At STRIKE_THRESHOLD
    consecutive strikes the skill goes soft (silently dropped from future
    announcements until a successful invoke resets the counter).
    """
    if not PENDING.is_file():
        return
    try:
        prior = json.loads(PENDING.read_text() or "{}")
        unsatisfied = prior.get("remaining") or []
        if unsatisfied:
            _bump_strikes(unsatisfied)
    except (json.JSONDecodeError, OSError):
        pass
    PENDING.write_text("{}\n")


# ---- Strike-based soft-mode (per-skill follow-rate enforcement) -------------

def _now_epoch() -> float:
    return time.time()


def _decay_counts(raw: dict, ttl_days: float = DEFER_TTL_DAYS) -> dict[str, int]:
    """Normalize a demotion tally, dropping entries older than DEFER_TTL_DAYS.

    Two on-disk shapes are accepted:
      {"skill": 3}                          legacy, no timestamp
      {"skill": {"n": 3, "ts": <epoch>}}    current

    A legacy bare integer has no timestamp, so its age is unknowable and it is
    treated as expired. That is deliberate: the legacy files on this machine
    were the permanent tombstones this TTL exists to end, and honoring them
    would carry the bug forward across the upgrade.
    """
    cutoff = _now_epoch() - ttl_days * 86400
    out: dict[str, int] = {}
    for skill, val in (raw or {}).items():
        if isinstance(val, dict):
            try:
                n = int(val.get("n", 0))
                ts = float(val.get("ts", 0))
            except (TypeError, ValueError):
                continue
            if n > 0 and ts >= cutoff:
                out[skill] = n
    return out


def _bump_count(path: Path, skill: str, ttl_days: float = DEFER_TTL_DAYS) -> None:
    """Increment a demotion tally for `skill`, stamped with the current time."""
    if not skill:
        return
    raw: dict = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text() or "{}")
            if isinstance(loaded, dict):
                raw = loaded
        except (json.JSONDecodeError, OSError):
            raw = {}
    live = _decay_counts(raw, ttl_days)
    live[skill] = live.get(skill, 0) + 1
    payload = {s: {"n": n, "ts": _now_epoch()} for s, n in live.items()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, sort_keys=True) + "\n")
    except OSError:
        pass


def _clear_count(path: Path, skill: str) -> None:
    """Drop `skill` from a demotion tally — it was invoked, so it is re-armed."""
    if not skill or not path.is_file():
        return
    try:
        raw = json.loads(path.read_text() or "{}")
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(raw, dict) or skill not in raw:
        return
    del raw[skill]
    try:
        path.write_text(json.dumps(raw, sort_keys=True) + "\n")
    except OSError:
        pass


def _load_strikes() -> dict[str, int]:
    """Return the live strike map, expired entries already dropped."""
    if not STRIKES.is_file():
        return {}
    try:
        data = json.loads(STRIKES.read_text() or "{}")
    except (json.JSONDecodeError, OSError):
        return {}
    return _decay_counts(data if isinstance(data, dict) else {}, STRIKE_TTL_DAYS)


def _bump_strikes(skills: list[str]) -> None:
    """Add one strike to each skill announced but never invoked this turn."""
    for s in skills:
        if isinstance(s, str) and s:
            _bump_count(STRIKES, s, STRIKE_TTL_DAYS)


def reset_strikes(skill: str) -> None:
    """Clear strikes for `skill`. Called via the PostToolUse Skill hook so any
    successful invoke re-arms the skill for IRON enforcement next time."""
    _clear_count(STRIKES, skill)


def is_soft(skill: str) -> bool:
    """True if `skill` has accumulated >= STRIKE_THRESHOLD consecutive
    unsatisfied announcements. Soft skills are silently dropped from the
    announcement and never written to pending state (no IRON enforcement)."""
    return int(_load_strikes().get(skill, 0)) >= STRIKE_THRESHOLD


# ---- Reasoned overrides (the ask+learn loop) -------------------------------

def _load_overrides_count() -> dict[str, int]:
    """Return the live reasoned-override tally, expired entries dropped."""
    if not OVERRIDES_COUNT.is_file():
        return {}
    try:
        data = json.loads(OVERRIDES_COUNT.read_text() or "{}")
    except (json.JSONDecodeError, OSError):
        return {}
    return _decay_counts(data if isinstance(data, dict) else {})


def _bump_override_count(skill: str) -> None:
    """Record one reasoned override against `skill`, stamped with the time."""
    _bump_count(OVERRIDES_COUNT, skill)


def reset_override_count(skill: str) -> None:
    """Clear the reasoned-override tally for `skill`. Called via the PostToolUse
    Skill hook so a successful invoke re-arms the skill for full enforcement."""
    _clear_count(OVERRIDES_COUNT, skill)


def is_overridden(skill: str) -> bool:
    """True if `skill` has accumulated >= OVERRIDE_THRESHOLD reasoned overrides.
    Like strike-based soft mode, but driven by the model explicitly stating the
    route was wrong (via scripts/router_override.py) rather than a silent miss.
    Reset on a successful invoke, so a skill recovers as soon as it's used."""
    return int(_load_overrides_count().get(skill, 0)) >= OVERRIDE_THRESHOLD


def is_deferred(skill: str) -> bool:
    """A skill is deferred — dropped from announcements, no IRON rule — if it is
    in strike-based soft mode OR has crossed the reasoned-override threshold."""
    return is_soft(skill) or is_overridden(skill)


def record_override(reason: str, prompt: Optional[str] = None) -> dict:
    """Record a reasoned override of the current pending route, then clear it.

    The collaborative escape hatch. Instead of the model being forced to invoke
    a route it judges wrong (or the user having to type [no-router]), the model
    states *why* and proceeds. This:
      1. reads the announced skill from pending state,
      2. appends an audit line to OVERRIDES_LOG with the reason,
      3. bumps the per-skill override tally so is_overridden() can defer the
         skill on similar future prompts, and
      4. clears pending so the PreToolUse / Stop hooks pass through.

    Fail-soft throughout — an override must never crash the model's turn.
    Returns a summary dict for the CLI wrapper to print.
    """
    reason = (reason or "").strip()
    announced = ""
    try:
        if PENDING.is_file():
            prior = json.loads(PENDING.read_text() or "{}")
            announced = prior.get("primary") or (prior.get("remaining") or [""])[0] or ""
    except (json.JSONDecodeError, OSError):
        announced = ""
    entry: dict = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "skill": announced,
        "reason": reason,
    }
    if prompt:
        entry["prompt_hash"] = hashlib.sha256(
            prompt.encode("utf-8", errors="replace")
        ).hexdigest()[:16]
    try:
        OVERRIDES_LOG.parent.mkdir(parents=True, exist_ok=True)
        with OVERRIDES_LOG.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass
    if announced:
        _bump_override_count(announced)
    # Clear pending so the IRON RULE stops blocking this turn.
    try:
        PENDING.write_text("{}\n")
    except OSError:
        pass
    count = _load_overrides_count().get(announced, 0) if announced else 0
    return {"skill": announced, "reason": reason, "count": count}


# ---- Personalized re-ranking from 30-day history ---------------------------

# ---- Personal project routes -----------------------------------------------
#
# The routing table is generic: it knows what *kind* of work a prompt is, not
# which of your projects it belongs to. "ship the next video" and "submit to
# TestFlight" both read as OPERATE/ship, but one wants youtube-manager and the
# other wants scrollbook-deploy — and no amount of pattern tuning on generic
# English recovers that. Project routes close the gap with the one thing the
# router can be certain about: a name that only ever means one project.
#
# They are read from a fenced ```yaml routes: block in SKILL.personal.md, so
# adding a project is a text edit, not a code change. Deliberately parsed by
# hand rather than with PyYAML: this runs inside a 3-second UserPromptSubmit
# hook on whatever python3 happens to be first on PATH, and a missing
# third-party import there would take the whole router down.

PERSONAL_FILE = Path(__file__).resolve().parents[1] / "SKILL.personal.md"

# A trigger shorter than this is too collision-prone to be evidence — "qa" or
# "ios" would fire on half of all prompts.
MIN_TRIGGER_LEN = 4


@dataclass(frozen=True)
class PersonalRoute:
    name: str
    triggers: tuple[str, ...]
    skill: str
    agent: str = "general-purpose"
    thinking: str = "none"
    path: str = "BUILD"
    matched: str = ""
    tier: str = ""                       # hard | soft | "" (derive)
    gates: tuple[str, ...] = ()


def _parse_personal_routes(text: str) -> list[PersonalRoute]:
    """Parse the `routes:` list out of SKILL.personal.md.

    Expected shape (inside any fenced yaml block):

        routes:
          - name: youtube-pipeline
            when: ["@economicalai", "youtube short"]
            skill: youtube-manager
            agent: yt-showrunner
            path: OPERATE
            thinking: think

    Anything malformed is skipped rather than raised. A typo in a personal
    config file must degrade to "that one route is ignored", never to "the
    router crashed and you lost the announcement".
    """
    routes: list[PersonalRoute] = []
    in_routes = False
    cur: dict[str, object] = {}

    def flush() -> None:
        skill = str(cur.get("skill") or "").strip()
        triggers = tuple(
            t for t in (cur.get("when") or ())  # type: ignore[union-attr]
            if isinstance(t, str) and len(t.strip()) >= MIN_TRIGGER_LEN
        )
        if skill and triggers:
            routes.append(PersonalRoute(
                name=str(cur.get("name") or skill),
                triggers=tuple(t.strip().lower() for t in triggers),
                skill=skill,
                agent=str(cur.get("agent") or "general-purpose"),
                thinking=str(cur.get("thinking") or "none"),
                path=str(cur.get("path") or "BUILD").upper(),
                tier=str(cur.get("tier") or "").lower(),
                gates=tuple(cur.get("gates") or ()),  # type: ignore[arg-type]
            ))
        cur.clear()

    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("routes:"):
            in_routes = True
            continue
        if not in_routes:
            continue
        # The block ends at the closing fence or any new top-level key.
        if stripped.startswith("```") or (raw and not raw[0].isspace() and not stripped.startswith("-")):
            flush()
            in_routes = False
            continue
        if stripped.startswith("- "):
            flush()
            stripped = stripped[2:].strip()
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        if key in ("when", "gates"):
            items = val.strip("[]")
            cur[key] = [
                piece.strip().strip("\"'")
                for piece in items.split(",")
                if piece.strip().strip("\"'")
            ]
        elif key in ("name", "skill", "agent", "thinking", "path", "tier"):
            cur[key] = val.strip("\"'")
    flush()
    return routes


@functools.lru_cache(maxsize=1)
def load_personal_routes() -> tuple[PersonalRoute, ...]:
    if not PERSONAL_FILE.is_file():
        return ()
    try:
        return tuple(_parse_personal_routes(PERSONAL_FILE.read_text(encoding="utf-8")))
    except OSError:
        return ()


def match_personal_route(prompt: str) -> Optional[PersonalRoute]:
    """First project route whose trigger appears in the prompt, or None.

    First match wins, so file order is priority order. The route is dropped if
    its skill is not installed — a personal file that names a skill you removed
    should go quiet, not deadlock the IRON RULE on a name Claude cannot invoke.
    """
    low = prompt.lower()
    for route in load_personal_routes():
        for trigger in route.triggers:
            if trigger in low:
                if not valid_skill(route.skill):
                    print(f"[skill-router-warn] personal route '{route.name}' names "
                          f"uninstalled skill '{route.skill}'", file=sys.stderr)
                    break
                agent = route.agent if valid_agent(route.agent) else "general-purpose"
                return PersonalRoute(
                    name=route.name, triggers=route.triggers, skill=route.skill,
                    agent=agent, thinking=route.thinking,
                    path=route.path if route.path in ("BROKEN", "BUILD", "OPERATE") else "BUILD",
                    matched=trigger, tier=route.tier, gates=route.gates,
                )
    return None


# ---- Specialist layer -------------------------------------------------------

def specialist_for(prompt: str, exclude: list[str]) -> Optional[tuple[str, str]]:
    """Best installed domain specialist for the prompt, or None.

    Returns (skill_name, one_line_description). Advisory, never enforced: the
    IRON RULE covers the process skill only, because a specialist suggestion
    that turns out wrong should cost a glance, not a blocked turn.
    """
    if os.environ.get("SKILL_ROUTER_NO_CATALOG") == "1":
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import catalog_match  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        match = catalog_match.best(prompt, exclude=exclude)
    except Exception:  # never let ranking break a turn
        return None
    if match is None:
        return None
    desc = " ".join(match.description.split())[:110]
    return match.name, desc


# The learned overlay written by scripts/learn.py. Supersedes the old
# skill_router_history.json, which nothing had written since May: the router
# was reading a per-skill follow-rate table frozen at the moment the miner
# was last run by hand. The overlay is regenerated every session start.
HISTORY = Path.home() / ".claude" / "skill_router_learned.json"
LEARNED = HISTORY


@functools.lru_cache(maxsize=1)
def _load_history() -> dict:
    """Return the per-skill history map computed by the history miner.

    The file is written periodically by scripts that analyze
    `skill_usage.log` + `skill_router_log.jsonl` over a 30-day window.
    Fail-open with {} on any read or schema error — routing falls back to
    embedder confidence with no personalization.
    """
    if not HISTORY.is_file():
        return {}
    try:
        data = json.loads(HISTORY.read_text() or "{}")
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _history_follow_rate(skill: str) -> Optional[float]:
    """Return the 30-day announcement→invocation ratio for `skill`, or None
    if there's not enough data to judge (default-open). The embedder fallback
    uses this to refuse rescues for skills the user routinely ignores."""
    hist = _load_history()
    per_skill = hist.get("per_skill") if isinstance(hist, dict) else None
    if not isinstance(per_skill, dict):
        return None
    entry = per_skill.get(skill)
    if not isinstance(entry, dict):
        return None
    # Need at least 3 announcements before the ratio means anything —
    # 0/1 is noise, 0/10 is signal.
    announcements = entry.get("announcements")
    if not isinstance(announcements, int) or announcements < 3:
        return None
    fr = entry.get("follow_rate")
    if isinstance(fr, (int, float)):
        return float(fr)
    return None


# ---- Skill catalog (ghost-skill guard) -------------------------------------

# Skills that are valid Skill() targets but live outside the standard on-disk
# layouts (e.g. vercel:deploy ships via the vercel plugin). Add entries here
# ONLY after confirming Skill(skill="<name>") actually succeeds in practice.
ROUTED_SKILL_ALIASES: frozenset[str] = frozenset({
    "vercel:deploy",
})


@functools.lru_cache(maxsize=1)
@functools.lru_cache(maxsize=1)
def _plugins_off() -> frozenset[str]:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import build_catalog  # type: ignore[import-not-found]
        return build_catalog.disabled_plugins()
    except Exception:
        return frozenset()


def _plugin_on(plugin: str, marketplace: str) -> bool:
    """A disabled plugin stays in the cache on disk; its skills and agents
    must not count as installed."""
    return f"{plugin}@{marketplace}" not in _plugins_off()


def _builtin_skills() -> frozenset[str]:
    """Skills the Claude Code binary ships with.

    They are invokable but live nowhere on disk, so the directory scan cannot
    see them and valid_skill() would reject every one — silently making
    `code-review`, `dataviz` and `security-review` unroutable. The names come
    from the catalog builder, which owns the list.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from build_catalog import BUILTIN_SKILLS  # type: ignore[import-not-found]
        return frozenset(BUILTIN_SKILLS)
    except ImportError:
        return frozenset()


@functools.lru_cache(maxsize=1)
def _skill_catalog() -> Optional[set[str]]:
    """Return the set of skill names that exist on disk, or None if the
    catalog can't be loaded (so callers fail open).

    Layouts scanned:
      1. ~/.claude/skills/<name>/        → bare name (e.g., 'refactor')
      2. ~/.claude/commands/<name>.md    → bare name (slash-command)
      3. ~/.claude/plugins/cache/<repo>/<plugin>/<version>/skills/<skill>/SKILL.md
         → namespaced as '<plugin>:<skill>' AND bare '<skill>'

    Sub-agents are deliberately excluded — see the AGENTS_DIR comment.

    All of these surface as valid `Skill(skill="<name>")` targets in the
    Claude Code harness. Plus a static ROUTED_SKILL_ALIASES whitelist for
    routing-table entries that resolve via marketplace / model-side aliases.

    The result is cached for the lifetime of the process — the catalog
    doesn't change between hook invocations within a single turn, and the
    hook is short-lived enough that staleness doesn't matter.
    """
    catalog: set[str] = set(ROUTED_SKILL_ALIASES) | set(_builtin_skills())
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

    # Plugin skills under ~/.claude/plugins/cache/*/<plugin>/*/skills/<skill>/SKILL.md
    # and plugin commands under ~/.claude/plugins/cache/*/<plugin>/*/commands/<cmd>.md
    if PLUGINS_DIR.is_dir():
        try:
            for repo_dir in PLUGINS_DIR.iterdir():
                if not repo_dir.is_dir():
                    continue
                for plugin_dir in repo_dir.iterdir():
                    if not plugin_dir.is_dir() or not _plugin_on(plugin_dir.name, repo_dir.name):
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


@functools.lru_cache(maxsize=1)
def _agent_catalog() -> Optional[set[str]]:
    """Every name that is legal as `Agent(subagent_type=...)`.

    Kept separate from the skill catalog on purpose. Announcing an agent as a
    skill deadlocks the IRON RULE; announcing a skill as an agent fails the
    dispatch. Returns None (fail open) when nothing can be enumerated.
    """
    agents: set[str] = {"general-purpose", "Explore", "Plan", "claude"}
    found = False
    if AGENTS_DIR.is_dir():
        try:
            for f in AGENTS_DIR.iterdir():
                if f.is_file() and f.suffix == ".md" and not f.name.startswith("_"):
                    agents.add(f.stem)
                    found = True
        except OSError:
            pass
    if PLUGINS_DIR.is_dir():
        try:
            for repo in PLUGINS_DIR.iterdir():
                if not repo.is_dir():
                    continue
                for plugin in repo.iterdir():
                    if not plugin.is_dir() or not _plugin_on(plugin.name, repo.name):
                        continue
                    for version in plugin.iterdir():
                        for root in (version / "agents", version / ".claude" / "agents"):
                            if not root.is_dir():
                                continue
                            for f in root.iterdir():
                                if f.is_file() and f.suffix == ".md":
                                    agents.add(f"{plugin.name}:{f.stem}")
                                    agents.add(f.stem)
                                    found = True
        except OSError:
            pass
    return agents if found else None


def valid_agent(name: str) -> bool:
    """True if `name` can be passed as Agent(subagent_type=...). Fails open."""
    if not name:
        return False
    catalog = _agent_catalog()
    return True if catalog is None else name in catalog


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

def log_chain(path: str, chain: list[Step], domains: list[str],
              meta: Optional[dict] = None) -> None:
    if path == "SKIP" or not chain:
        return
    # Same guard as log_prompt_event: a test or a smoke probe that runs in
    # hook mode must not write an announcement nothing will ever follow —
    # the learner would read it as one you ignored.
    if os.environ.get("SKILL_ROUTER_NO_LEARN") == "1":
        return
    meta = meta or {}
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
            "tier": LAST_CARD.tier, "decided_by": LAST_CARD.decided_by,
            "confidence": LAST_CARD.confidence,
            "work": LAST_CARD.work or None, "work_model": LAST_CARD.model,
            "session_id": meta.get("session_id"),
            "prompt_id": meta.get("prompt_id"),
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

def _drop_soft(chain: list[Step]) -> list[Step]:
    """Filter out steps whose skill is deferred — either strike-based soft mode
    (>= STRIKE_THRESHOLD silent misses) or reasoned-override mode
    (>= OVERRIDE_THRESHOLD explicit corrections). Silent: no announcement, no
    enforcement, no log noise. Returns a new list; original untouched."""
    return [s for s in chain
            if not is_deferred(s.skill) and s.skill != SELF_SKILL and s.skill not in LOADED]


# The router's own skill was the single most-loaded skill while it ran (46 of
# 215 loads): a personal `projects:` entry listed it, so any prompt saying
# "routing" pulled ~2.5k tokens of router documentation into the session.
SELF_SKILL = "skill-router"

# Skills already loaded in this session (skill_invoked.py records them). A
# skill body stays in context once loaded, so a second card for it buys
# nothing and costs the card plus, if obeyed, the body again. main() fills it.
LOADED: frozenset[str] = frozenset()


def loaded_this_session(meta: dict) -> frozenset[str]:
    sid = str((meta or {}).get("session_id") or "").strip()
    if not sid:
        return frozenset()
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", sid)[:80]
    session_dir = Path(os.environ.get("SKILL_ROUTER_SESSION_DIR") or SESSION_DIR)
    try:
        lines = (session_dir / f"{safe}.loaded").read_text(encoding="utf-8").splitlines()
    except OSError:
        return frozenset()
    return frozenset(x.strip() for x in lines if x.strip())


# ---- Learned overlay consumers -----------------------------------------------
#
# Everything below reads ~/.claude/skill_router_learned.json (written by
# scripts/learn.py) and is advisory. Learned associations never carry the IRON
# RULE: they are statistics about your habits, not a decision the table made,
# and enforcing a habit would turn a nudge into a cage.

LEARNED_TRIGGER_MIN_SCORE = 1.2   # summed precision×log(1+n) over matched tokens
LEARNED_TRIGGER_MIN_MARGIN = 0.25


def learned_trigger_for(prompt: str) -> Optional[tuple[str, list[str]]]:
    """Skill your history says this prompt wants, when the table is silent.

    Scores each skill by the learned keyword→skill statistics for the words in
    the prompt. Requires a clear winner; ties stay silent.
    """
    triggers = _load_history().get("triggers")
    if not isinstance(triggers, list) or not triggers:
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from catalog_match import tokenize  # type: ignore[import-not-found]
    except ImportError:
        return None
    tokens = set(tokenize(prompt))
    if not tokens:
        return None
    score: dict[str, float] = {}
    hits: dict[str, list[str]] = {}
    for t in triggers:
        tok, skill = t.get("token"), t.get("skill")
        if tok in tokens and skill and valid_skill(skill):
            score[skill] = score.get(skill, 0.0) + float(t.get("precision", 0)) * math.log1p(int(t.get("n", 0)))
            hits.setdefault(skill, []).append(tok)
    if not score:
        return None
    ranked = sorted(score.items(), key=lambda kv: -kv[1])
    best, best_score = ranked[0]
    if best_score < LEARNED_TRIGGER_MIN_SCORE:
        return None
    if len(ranked) > 1 and (best_score - ranked[1][1]) / best_score < LEARNED_TRIGGER_MIN_MARGIN:
        return None
    return best, hits[best]


def learned_chain_after(skill: str) -> Optional[list[str]]:
    """The recurring sequence that starts with `skill`, if you have one."""
    chains = _load_history().get("chains")
    if not isinstance(chains, list):
        return None
    for c in chains:
        steps = c.get("steps") or []
        if len(steps) >= 3 and steps[0] == skill:
            return list(steps)
    return None


# ---- v4 route card state ------------------------------------------------------
#
# route() keeps its 4-tuple signature for every caller and test; the extra
# facts a v4 route carries (tier, gates, memory, how it was decided) travel
# through this module-level record, set by _finish and read by main().

@dataclass
class RouteCard:
    tier: str = "soft"                  # hard | soft
    gates: tuple[str, ...] = ()
    memory: tuple[str, ...] = ()
    decided_by: str = "table"           # project-route | index | llm | table
    primary_kind: str = ""              # domain | design | project | process
    confidence: str = ""
    path: str = ""                      # the path this card was built for
    primary: str = ""                   # chain[0].skill this card belongs to
    work: str = ""                      # light | standard | heavy | "" (Jev's work tier)
    work_confidence: float = 0.0
    model: str = "inherit"              # haiku | sonnet | inherit — what the tier earns


LAST_CARD = RouteCard()
SESSION_DIR = Path(os.environ.get("SKILL_ROUTER_SESSION_DIR")
                   or Path.home() / ".claude" / "skill_router_session")
DEFAULT_SOFT_PATHS = ("BUILD", "OPERATE")
CARD_MAX_CHARS = 1000               # ~250 tokens


def _tier_for(path: str, gates: tuple[str, ...], explicit: str = "",
              from_route: bool = False) -> str:
    """hard for BROKEN and for project routes that declare gates or say so;
    everything else soft. Gates inherited from the projects block are shown
    on the card but do not harden an index-decided route."""
    if explicit in ("hard", "soft"):
        return explicit
    if path == "BROKEN" or (gates and from_route):
        return "hard"
    return "soft"


def _finish(path: str, chain: list[Step], domains: list[str],
            prompt: str, note: str = "", card: Optional[RouteCard] = None,
            ) -> tuple[str, list[Step], list[str], str]:
    """Render an announcement and bolt the advisory lines onto it."""
    global LAST_CARD
    # A route that names an archived or disabled agent (integration-specialist
    # went to .archive/ on 2026-09-14) keeps its skill and runs in-session.
    chain = [s if valid_agent(s.agent) else Step(s.skill, "general-purpose", s.model, s.thinking)
             for s in chain]
    LAST_CARD = card or RouteCard(tier=_tier_for(path, ()))
    LAST_CARD.path = path
    LAST_CARD.primary = chain[0].skill if chain else ""
    announcement = base = render(path, chain, domains, note=note)
    if not announcement:
        return path, chain, domains, ""
    extra = work_lines(LAST_CARD, path)
    if extra:
        announcement = base = base + "\n" + "\n".join(extra)
    # The v4 index puts the domain skill *in* the chain, so the specialist
    # advisory only fires when the chain is process-only.
    if LAST_CARD.primary_kind in ("", "process"):
        found = specialist_for(prompt, exclude=[s.skill for s in chain])
        if found is not None:
            name, desc = found
            announcement += (
                f"\n[skill-router] Specialist available: {name}"
                f"{' — ' + desc if desc else ''}"
                f"\n[skill-router] (advisory — load it alongside the step above if it fits; "
                f"not enforced)"
            )
    flow = learned_chain_after(chain[0].skill) if chain else None
    if flow and len(chain) == 1:
        announcement += ("\n[skill-router] Your usual flow from here: "
                         + " → ".join(flow[1:]) + "  (learned; advisory)")
    # Every injected character is re-read on every later turn of the session.
    # The card proper is ~140 tokens; if the advisory lines push it past the
    # cap, they go and the card stays.
    if len(announcement) > CARD_MAX_CHARS:
        announcement = base
    return path, chain, domains, announcement


# ---- v4: index + LLM stages ---------------------------------------------------

def _is_question(prompt: str) -> bool:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import index_match  # type: ignore[import-not-found]
        return index_match.detect_path(prompt) == "QUESTION"
    except Exception:
        return False


def _index_classify(prompt: str):
    """index_match.classify, or None when the index is missing/disabled."""
    if os.environ.get("SKILL_ROUTER_NO_INDEX") == "1":
        return None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import index_match  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        res = index_match.classify(prompt)
    except Exception:
        return None
    return res if res.candidates or res.path in ("QUESTION", "NONE") else res


def _llm_decide(prompt: str, res) -> Optional[dict]:
    """Ask the small model to settle a low-confidence ranking. None = no answer."""
    if os.environ.get("SKILL_ROUTER_LLM", "1") in ("0", "off", "false"):
        return None
    if res is None or not res.candidates:
        return None
    if len(prompt.split()) < 4:
        return None
    # A prompt that already waited out a Jev timeout gets the 50 ms lexical
    # answer and nothing slower: stacking a ~1 s (cap 6 s) second model call
    # on top of the wait is how a typing pause becomes a stall.
    if JEV_TIMED_OUT:
        return None
    try:
        import llm_classify  # type: ignore[import-not-found]
        import index_match  # type: ignore[import-not-found]
    except ImportError:
        return None
    idx = index_match.load_index()
    by_name = {d.name: d for d in idx.docs} if idx else {}
    cands: list[tuple[str, str]] = []
    for m in res.candidates[:6]:
        d = by_name.get(m.name)
        summary = "; ".join(d.use_when[:3]) if d and d.use_when else m.description[:140]
        cands.append((m.name, summary))
    projects = list(idx.project_aliases.keys()) if idx else []
    try:
        return llm_classify.classify(prompt, cands, projects)
    except Exception:
        return None


# ---- v4.1: Jev chooses over the whole index ----------------------------------
#
# The lexical rank above feeds the small model a top-8 that held the right
# skill 23 times in 66 on real prompts (typos defeat token matching), so the
# tie-break never had a chance. Jev reads every indexed skill in one call and
# needs no pre-filter. See scripts/jev_choose.py and docs/jev-eval-2026-09-21/.

PREV_ASSISTANT = ""                 # tail of the previous assistant turn; main() sets it
JEV_TIMED_OUT = False               # this prompt already spent its network budget waiting
TRANSCRIPT_TAIL_BYTES = 256_000


def _jev_active() -> bool:
    """Real hook turns only, like the embedder rescue: tests, calibration and
    doctor probes must stay offline and deterministic. SKILL_ROUTER_JEV=1
    opts a manual probe in; =0 (or SKILL_ROUTER_LLM=0) turns it off."""
    flag = os.environ.get("SKILL_ROUTER_JEV", "")
    if flag in ("0", "off", "false"):
        return False
    if os.environ.get("SKILL_ROUTER_LLM", "1") in ("0", "off", "false"):
        return False
    return flag == "1" or os.environ.get("SKILL_ROUTER_HOOK_MODE") == "1"


def _jev_decide(prompt: str):
    """jev_choose.Choice, or None — None means "use the lexical + Gemini path"."""
    if not _jev_active():
        return None
    global JEV_TIMED_OUT
    JEV_TIMED_OUT = False
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import jev_choose  # type: ignore[import-not-found]
        got = jev_choose.choose(prompt, context=PREV_ASSISTANT)
        JEV_TIMED_OUT = got is None and jev_choose.LAST_FAILURE == "timeout"
        return got
    except Exception:
        return None


def previous_assistant_tail(meta: dict, chars: int = 300) -> str:
    """Last `chars` of the assistant text that preceded this prompt, read from
    the tail of the hook's transcript. Lets "yes please continue" be judged
    against what it answers. Empty on any problem."""
    tp = meta.get("transcript_path") if isinstance(meta, dict) else None
    if not tp:
        return ""
    try:
        with open(tp, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TRANSCRIPT_TAIL_BYTES))
            lines = fh.read().decode("utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        if '"type":"assistant"' not in line and '"type": "assistant"' not in line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue                                   # first line of the window may be cut
        if ev.get("type") != "assistant" or ev.get("isSidechain"):
            continue
        content = (ev.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content
        else:
            text = "\n".join(p.get("text", "") for p in content or []
                             if isinstance(p, dict) and p.get("type") == "text")
        if text.strip():
            return text.strip()[-chars:]
    return ""


def _suggestions_on() -> bool:
    """The 0.5-0.8 "Possible fit:" tier is built but OFF by default. Measured:
    right 6 times in 18 on turns that did use a skill, and it would print on
    35 % of turns that needed none. A one-in-three nudge toward a ~2.5k-token
    skill body is not worth a line on a third of all prompts. Silence is free.
    SKILL_ROUTER_JEV_SUGGEST=1 turns it on."""
    return os.environ.get("SKILL_ROUTER_JEV_SUGGEST", "0") in ("1", "on", "true")


def _suggestion_line(name: str, confidence: float) -> str:
    return (f"[skill-router] Possible fit: Skill(skill=\"{name}\")  "
            f"(jev {confidence:.2f}; advisory — not enforced)")


def _route_from_jev(prompt: str, jev, res, domains: list[str],
                    ) -> tuple[str, list[Step], list[str], str]:
    """Build the route from a Jev answer.

    >= 0.8 routes; 0.5-0.8 is printed as a suggestion with no pending state
    and no rule; below that the pick is ignored and nothing replaces it.
    Jev's QUESTION is never used to silence a turn: on real prompts that did
    invoke a skill it said QUESTION 8 times in 126.
    """
    path = triage(prompt)
    if path == "SKIP" and res.path in ("BROKEN", "BUILD", "OPERATE"):
        path = res.path
    if path == "SKIP" and jev.path in ("BROKEN", "BUILD", "OPERATE") and jev.path_confidence >= 0.5:
        path = jev.path
    by_name = {m.name: m for m in res.candidates}
    suggestions: list[str] = []
    chain: list[Step] = []
    primary_kind, gates, memory = "process", (), ()
    confidence = 0.0

    def usable(pick) -> bool:
        return (bool(pick.name) and valid_skill(pick.name) and not is_deferred(pick.name)
                and pick.name != SELF_SKILL and pick.name not in LOADED)

    d = jev.domain
    if usable(d) and d.tier == "route":
        if path == "SKIP":
            path = "OPERATE"
        m = by_name.get(d.name)
        kind = m.kind if m is not None else "domain"
        thinking = "think" if path in ("BROKEN", "BUILD") else "none"
        chain.append(Step(d.name, _agent_for_kind(kind), "inherit", thinking))
        primary_kind, confidence = kind, d.confidence
        if m is not None:
            gates, memory = tuple(m.gates), tuple(m.memory)
    elif usable(d) and d.tier == "suggest":
        suggestions.append(_suggestion_line(d.name, d.confidence))

    # When Jev answers, only a >= 0.8 pick puts a skill on the card. The regex
    # table's process leg is NOT a fallback here: on 150 real turns where no
    # skill was needed it produced 25 of the 53 carded steps ("ok lets do it"
    # -> writing-plans, "wtf ... why did you push" -> hard systematic-debugging)
    # while Jev's own confidence on them sat between 0.25 and 0.79.
    p = jev.process
    if usable(p) and p.tier == "route":
        if path == "SKIP":
            path = "OPERATE"
        known = next((s for s in _process_leg(path, prompt, domains) if s.skill == p.name), None)
        thinking = "think" if path in ("BROKEN", "BUILD") else "none"
        step = known or Step(p.name, "general-purpose", "inherit", thinking)
        if all(step.skill != c.skill for c in chain):
            chain.append(step)
        confidence = confidence or p.confidence
    elif usable(p) and p.tier == "suggest":
        suggestions.append(_suggestion_line(p.name, p.confidence))

    if not _suggestions_on():
        suggestions = []
    chain = _drop_soft([s for s in chain if valid_skill(s.skill)])
    if not chain:
        # No skill fits, but the turn may still be light/standard work on a
        # strained quota — the one case a line is worth printing without a card.
        work_path = path if path != "SKIP" else (
            jev.path if jev.path in ("BROKEN", "BUILD", "OPERATE") and jev.path_confidence >= 0.5
            else "SKIP")
        nudge = quota_only_line(jev, work_path)
        if nudge:
            suggestions.append(nudge)
        return "SKIP", [], domains, "\n".join(suggestions)
    card = RouteCard(tier=_tier_for(path, gates), gates=gates, memory=memory,
                     decided_by="jev", primary_kind=primary_kind,
                     confidence=f"jev:{confidence:.2f}" if confidence else "table",
                     work=jev.work.name or "", work_confidence=jev.work.confidence,
                     model=jev.model)
    path, chain, domains, announcement = _finish(path, chain, domains, prompt, card=card)
    if announcement and suggestions:
        announcement += "\n" + "\n".join(suggestions)
    return path, chain, domains, announcement


# ---- v4.2: work tier → model, quota → Kimi ------------------------------------

WORK_LINE_MAX = 220


def _quota():
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import quota  # type: ignore[import-not-found]
        return quota
    except ImportError:
        return None


SESSION_ID = ""                      # set by main() in hook mode
QUOTA_HINT_EVERY_S = 30 * 60         # one Kimi nudge per band per half hour per session


def _quota_hint_due(band: str) -> bool:
    """Every injected line is re-read on every later turn, so the Kimi nudge
    prints once per band per half hour per session, not on every prompt.
    Outside hook mode (tests, probes) it is always due and nothing is written."""
    if os.environ.get("SKILL_ROUTER_HOOK_MODE") != "1":
        return True
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", SESSION_ID or "nosession")[:80]
    session_dir = Path(os.environ.get("SKILL_ROUTER_SESSION_DIR") or SESSION_DIR)
    mark = session_dir / f"{sid}.quota-{band}"
    try:
        if mark.is_file() and time.time() - mark.stat().st_mtime < QUOTA_HINT_EVERY_S:
            return False
        session_dir.mkdir(parents=True, exist_ok=True)
        mark.write_text(str(time.time()))
    except OSError:
        return True
    return True


def quota_only_line(jev, path: str) -> str:
    """The Kimi nudge on a turn that got no card: light/standard work, no
    skill fit, quota strained. Empty otherwise."""
    if jev is None or path in ("SKIP", "QUESTION"):
        return ""
    card = RouteCard(work=jev.work.name or "", work_confidence=jev.work.confidence, model=jev.model)
    lines = [ln for ln in work_lines(card, path) if "Quota:" in ln or "Kimi mode" in ln]
    return lines[0] if lines else ""


def work_lines(card: RouteCard, path: str) -> list[str]:
    """The model-choice lines for a card. Empty on most turns.

    Two things can print, both one line and both only when they change what
    the model should do next:

      Work: light (jev 0.91) → sub-agents dispatch on haiku
          only when the tier moves the model off inherit. The Task hook is
          what actually sets the model; this line tells the parent why a
          dispatch came back on Haiku and nudges it to delegate the bulk part.

      Quota: 5h 87% · 7d 62% → offload to Kimi: bash …/kimi_offload.sh "<task>"
          only when the status line has reported a window over the threshold
          (or SKILL_ROUTER_KIMI=always) and the work is light/standard. A
          heavy task on a strained quota still gets the session model: that
          is the one place a downgrade shows.

    Questions and silent turns print nothing: the tier of a chat reply is
    not information anyone acts on.
    """
    out: list[str] = []
    if path in ("SKIP", "QUESTION") or not card.work:
        return out
    q = _quota()
    if card.model != "inherit":                       # only ever true at >= 0.8
        out.append(f"[skill-router] Work: {card.work} (jev {card.work_confidence:.2f}) "
                   f"→ sub-agents dispatch on {card.model}")
    # Measured on 150 real user prompts (2026-09-22): light/standard at >= 0.8
    # is 2 % of turns, at >= 0.5 it is 20 % — and the 0.5-0.8 band is mostly
    # "commit this", "push a preview", "untrack .archive". Small, but real,
    # and exactly what Kimi should absorb once the quota is nearly gone. So
    # the nudge bar drops to 0.5 only when a window is past the critical mark.
    band = q.band() if q is not None else ""
    nudge_at = 0.5 if band == "critical" else 0.8
    if (q is not None and card.work_confidence >= nudge_at and q.offload_wanted(card.work)
            and _quota_hint_due(band or "always")):
        used = q.summary()
        tier_flag = f" --tier {card.work}" if card.work != "standard" else ""
        script = Path(__file__).resolve().parent / "kimi_offload.sh"
        why = "Kimi mode: always" if q.kimi_mode() == "always" else f"Quota: {used or 'strained'}"
        out.append(f"[skill-router] {why} → this is {card.work} work; offload it: "
                   f"bash {script}{tier_flag} \"<the task>\"")
    return [ln for ln in out if len(ln) <= WORK_LINE_MAX]


def _agent_for_kind(kind: str) -> str:
    if kind == "design" and valid_agent("product-designer"):
        return "product-designer"
    return "general-purpose"


def _process_leg(path: str, prompt: str, domains: list[str]) -> list[Step]:
    """The process skill(s) that pair with this path. OPERATE only when the
    prompt actually asked for an operation the table knows; 'research
    competitors' must not become `refactor`."""
    if path == "BROKEN":
        return build_broken_chain(prompt)
    if path == "BUILD":
        chain = build_build_chain(prompt, domains)
        # Multi-domain BUILD chains fan the domain legs out to sub-agents;
        # with a v4 primary in front that is one plan step plus the primary.
        return chain[:1] if len(chain) > 1 else chain
    if path == "OPERATE" and any_match(prompt, OPERATE_RE):
        return build_operate_chain(prompt)
    return []


def route_v4(prompt: str) -> Optional[tuple[str, list[Step], list[str], str]]:
    """Index + LLM route. Returns None to fall back to the v3 table route."""
    domains = detect_domains(prompt)
    res = _index_classify(prompt)
    if res is None:
        return None
    if any_match(prompt, SKIP_RE) or res.path == "QUESTION":
        return "SKIP", [], domains, ""
    # The v3 regex triage keeps first refusal on the path: it knows
    # "CRITICAL: database corrupted" and "clean it up" are work even though
    # neither is phrased as a request. The index's path fills its silences.
    jev = _jev_decide(prompt)
    if jev is not None:
        return _route_from_jev(prompt, jev, res, domains)
    path = triage(prompt)
    if path == "SKIP":
        path = res.path if res.path in ("BROKEN", "BUILD", "OPERATE") else "SKIP"
    primary = res.primary if res.confidence == "high" else None
    decided_by = "index" if primary else "table"
    confidence = res.confidence
    # On BROKEN a confident *plugin* match with no project evidence is still
    # a guess ("vitest fails after upgrading vite" → vercel:next-upgrade).
    # Let the small model confirm before it leads a hard-tier chain.
    if (primary is not None and path == "BROKEN" and primary.owner == "plugin"
            and not primary.project_hit):
        primary = None
        decided_by = "table"
    if primary is None and res.candidates:
        llm = _llm_decide(prompt, res)
        if llm:
            if llm["path"] == "QUESTION":
                return "SKIP", [], domains, ""
            if llm["path"] in ("BROKEN", "BUILD", "OPERATE"):
                path = llm["path"]
            by_name = {m.name: m for m in res.candidates}
            for s in llm["skills"]:
                m = by_name.get(s)
                if m is not None:
                    primary = m
                    decided_by = "llm"
                    confidence = "llm"
                    break
            if primary is None and not llm["skills"]:
                decided_by = "llm-none"
        # No model answer (offline, no key, disabled): a low-confidence match
        # on one of the user's *own* skills is still worth a soft route —
        # being wrong costs one line, being silent costs the skill.
        if (primary is None and decided_by not in ("llm-none",) and res.primary is not None
                and res.confidence == "low" and res.primary.owner in ("user", "project")
                and path != "BROKEN"):
            primary = res.primary
            decided_by = "index-low"
            confidence = "low"
    if path == "SKIP":
        return "SKIP", [], domains, ""
    process = _process_leg(path, prompt, domains)
    chain: list[Step] = []
    if primary is not None and valid_skill(primary.name):
        thinking = "think" if path in ("BROKEN", "BUILD") else "none"
        chain.append(Step(primary.name, _agent_for_kind(primary.kind), "inherit", thinking))
    for s in process:
        if all(s.skill != c.skill for c in chain):
            chain.append(s)
    ghost = next((s.skill for s in chain if not valid_skill(s.skill)), None)
    if ghost is not None:
        chain = [s for s in chain if s.skill != ghost]
    chain = _drop_soft(chain)
    if not chain:
        return "SKIP", [], domains, ""
    gates = tuple(primary.gates) if primary is not None else ()
    memory = tuple(primary.memory) if primary is not None else ()
    card = RouteCard(tier=_tier_for(path, gates), gates=gates, memory=memory,
                     decided_by=decided_by,
                     primary_kind=(primary.kind if primary is not None else "process"),
                     confidence=confidence)
    return _finish(path, chain, domains, prompt, card=card)


def _learned_fallback(prompt: str, domains: list[str]) -> tuple[str, list[Step], list[str], str]:
    """When every deterministic layer is silent, ask the overlay. Advisory only:
    no IRON RULE, no pending state — hence the announcement is rendered without
    the rule block and route() returns path 'SKIP' so nothing is enforced."""
    found = learned_trigger_for(prompt)
    if found is None:
        return "SKIP", [], domains, ""
    skill, toks = found
    text = (f"[skill-router] Learned from your history: prompts with "
            f"{', '.join(toks[:4])} usually use {skill}.\n"
            f"[skill-router] Advisory — Skill(skill=\"{skill}\") if it fits; not enforced.")
    return "SKIP", [], domains, text


def route(prompt: str) -> tuple[str, list[Step], list[str], str]:
    """Return (path, chain, domains, announcement)."""
    domains = detect_domains(prompt)

    # A question is a question even when it names a project: "what does the
    # skill router do" must not fire the skill-system route.
    if any_match(prompt, SKIP_RE) or _is_question(prompt):
        return "SKIP", [], domains, ""

    # Project routes win over triage. A prompt naming one of your projects is
    # the least ambiguous signal the router ever gets, and generic triage
    # cannot recover it — see the PersonalRoute docs.
    personal = match_personal_route(prompt)
    if personal is not None and not is_deferred(personal.skill):
        chain = [Step(personal.skill, personal.agent, "inherit", personal.thinking)]
        card = RouteCard(tier=_tier_for(personal.path, personal.gates, personal.tier,
                                        from_route=True),
                         gates=personal.gates, decided_by="project-route",
                         primary_kind="project", confidence="route")
        return _finish(personal.path, chain, domains, prompt,
                       note=f"project route `{personal.name}`", card=card)

    # v4: enriched index (+ small-model tie-break) decides the domain skill;
    # the v3 table below only runs when the index is absent.
    v4 = route_v4(prompt)
    if v4 is not None:
        return v4

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
                chain = _drop_soft(chain)
                if not chain:
                    return "SKIP", [], domains, ""
                return _finish(path, chain, domains, prompt)
            print(f"[skill-router-warn] embedding ghost skill: {ghost}", file=sys.stderr)
        return _learned_fallback(prompt, domains)
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
    # Soft-mode filter: drop steps whose skill has struck out. If nothing
    # left, the whole route goes silent — the model isn't asked to invoke
    # something that history shows it will ignore.
    chain = _drop_soft(chain)
    if not chain:
        return "SKIP", [], domains, ""
    return _finish(path, chain, domains, prompt)


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

    # Personalized re-rank: even when the embedder is confident, defer to the
    # user's actual 30-day follow rate. If history shows they routinely ignore
    # this skill (<30% follow with ≥3 announcements), refuse the rescue and
    # let the prompt stay SKIP. Avoids the embedder reviving a skill the strike
    # rule would just demote on next miss.
    fr = _history_follow_rate(skill)
    if fr is not None and fr < 0.30:
        _log_embedding_attempt(prompt, result, accepted=False, rejected_skill=f"{skill} (history_follow_rate={fr:.2f})")
        return None

    # Build a single-step chain matching the embedder's recommendation.
    # We do NOT trust the embedder to do multi-domain build chains — those
    # require domain detection, which the regex layer already does. Single-
    # step is the safe wedge.
    if path == "BROKEN":
        chain = [Step(skill, "general-purpose", "inherit", "think")]
    elif path == "BUILD":
        chain = [Step(skill, "feature-dev:code-architect", "inherit", "think")]
    else:  # OPERATE
        chain = [Step(skill, "general-purpose", "inherit", "none")]

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


# ---- Online-catalog soft suggestion -----------------------------------------
#
# Last-resort path. When regex triage AND embedding fallback both returned
# SKIP, we still might be able to point the user at an online skill they
# haven't installed yet. This is advisory only — no IRON enforcement, no
# pending state. Token-overlap scoring (zero network, cheap) by design:
# loading the embedder for skills the user doesn't have is wasted compute,
# and the local-first principle prohibits any cloud call on the hot path.

# Tiny stopword set — only the highest-frequency English filler words that
# would otherwise dominate the token overlap. Kept short so we don't strip
# legitimate signal (e.g., "use", "new" can be meaningful in skill names).
_STOPWORDS: frozenset[str] = frozenset({
    "about", "above", "after", "again", "also", "and", "any", "are", "because",
    "been", "before", "being", "between", "both", "but", "can", "could", "did",
    "does", "doing", "done", "down", "during", "each", "few", "for", "from",
    "had", "has", "have", "having", "her", "here", "him", "his", "how", "into",
    "its", "itself", "just", "like", "make", "many", "more", "most", "much",
    "must", "need", "now", "off", "once", "only", "other", "our", "ours", "out",
    "over", "own", "same", "she", "should", "some", "such", "than", "that",
    "the", "their", "them", "then", "there", "these", "they", "this", "those",
    "through", "too", "under", "until", "very", "was", "way", "were", "what",
    "when", "where", "which", "while", "who", "whom", "why", "will", "with",
    "would", "you", "your", "yours", "yourself",
})

# Pre-compiled token splitter — strip everything that isn't a word char. Used
# for both prompt and skill-description tokenization so the two are comparable.
_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    """Lowercase, regex-split, drop stopwords + tokens shorter than 4 chars.

    Returns a set (order doesn't matter for overlap scoring) — duplicates
    inside the prompt or skill description don't double-count.
    """
    if not text:
        return set()
    return {
        t for t in _WORD_RE.findall(text.lower())
        if len(t) >= 4 and t not in _STOPWORDS
    }


@functools.lru_cache(maxsize=1)
def _online_skill_index() -> Optional[list[tuple[dict, frozenset[str]]]]:
    """Load the online catalog once and pre-tokenize every novel entry.

    Returns a list of (entry, tokens) pairs for entries where:
      - `installed` is False, AND
      - `name` is NOT present in the local installed catalog (i.e., the 810
        truly-novel set from the 1,697 total online entries).

    Pre-tokenization is the perf trick: we pay it once per process, then
    every prompt does O(novel_skills) set-intersections (fast). Returns
    None if either catalog file is missing / malformed — caller fails open.
    """
    try:
        if not ONLINE_CATALOG_FILE.is_file():
            return None
        online = json.loads(ONLINE_CATALOG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    # Local catalog: name → installed. Treat missing local file as "nothing
    # installed" (every online entry is novel) so the suggestion still works
    # in a fresh setup. The router will still ghost-guard before announcing.
    local_names: set[str] = set()
    try:
        if LOCAL_CATALOG_FILE.is_file():
            local = json.loads(LOCAL_CATALOG_FILE.read_text())
            for entry in local.get("entries", []):
                name = entry.get("name")
                if name:
                    local_names.add(name)
    except (OSError, json.JSONDecodeError):
        pass

    index: list[tuple[dict, frozenset[str]]] = []
    catalogs = online.get("catalogs", {}) if isinstance(online, dict) else {}
    for entries in catalogs.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("installed"):
                continue
            name = entry.get("name")
            if not name or name in local_names:
                continue
            # Tokenize name + description + tags. Tags are short, so they're
            # high-signal noise-free overlap fuel (e.g., "pricing", "thumbnail").
            tags = entry.get("tags") or []
            text = " ".join([
                name,
                entry.get("description") or "",
                " ".join(str(t) for t in tags if t),
            ])
            tokens = _tokenize(text)
            if not tokens:
                continue
            index.append((entry, frozenset(tokens)))
    return index


def suggest_online_skill(prompt: str) -> Optional[dict]:
    """Return the best-matching uninstalled online skill, or None.

    Scoring is symmetric token-overlap with two gates:
      1. At least `ONLINE_SUGGEST_MIN_OVERLAP` prompt tokens overlap with the
         skill's tokenized text (raw count gate — kills tiny-prompt noise).
      2. Confidence = |overlap| / min(|prompt|, |skill|) ≥
         `ONLINE_SUGGEST_THRESHOLD`. Using min() instead of union (Jaccard)
         lets a focused prompt match a verbose skill description, and vice
         versa, without one drowning the other.

    Adds `_router_confidence` to the returned dict so the caller can render
    it in logs. Returns the entry verbatim otherwise — caller pulls `name`,
    `source`, `install_command` from it.
    """
    prompt_tokens = _tokenize(prompt)
    if len(prompt_tokens) < ONLINE_SUGGEST_MIN_OVERLAP:
        return None
    index = _online_skill_index()
    if not index:
        return None
    best_entry: Optional[dict] = None
    best_score = 0.0
    best_overlap = 0
    for entry, tokens in index:
        overlap = prompt_tokens & tokens
        count = len(overlap)
        if count < ONLINE_SUGGEST_MIN_OVERLAP:
            continue
        denom = min(len(prompt_tokens), len(tokens))
        if denom == 0:
            continue
        score = count / denom
        if score > best_score:
            best_score = score
            best_overlap = count
            best_entry = entry
    if best_entry is None or best_score < ONLINE_SUGGEST_THRESHOLD:
        return None
    # Return a shallow copy so callers mutating the result can't poison cache.
    result = dict(best_entry)
    result["_router_confidence"] = round(best_score, 3)
    result["_router_overlap"] = best_overlap
    return result


def render_online_suggestion(entry: dict) -> str:
    """Render the soft suggestion announcement. Three lines, IRON-free."""
    name = entry.get("name", "?")
    source = entry.get("source", "online")
    install_cmd = entry.get("install_command") or "(no install command provided)"
    return "\n".join([
        f"[skill-router] No installed skill matches, but `{name}` from {source} might fit.",
        f"[skill-router] Install: {install_cmd}",
        "[skill-router] (Skipped — soft suggestion only, no enforcement.)",
    ])


def _read_input() -> tuple[str, dict]:
    """(prompt, hook_meta). Accepts the hook's JSON or a raw prompt.

    The UserPromptSubmit hook now pipes its whole stdin JSON here instead of
    pre-extracting `.prompt` with jq, because the learner needs session_id
    and prompt_id to join a prompt to the skill that was later invoked on it.
    Raw text still works so tests and manual probes stay simple.
    """
    raw = os.environ.get("CLAUDE_USER_INPUT", "") or sys.stdin.read()
    stripped = raw.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict) and "prompt" in data:
                return str(data.get("prompt") or "").strip(), data
        except (json.JSONDecodeError, ValueError):
            pass
    return stripped, {}


def log_prompt_event(prompt: str, meta: dict, path: str, chain: list[Step],
                     note: str = "") -> None:
    """Record what this prompt looked like and what was announced for it.

    Keywords only — never the prompt text. This is the learner's join key:
    the invoke event written when a Skill runs carries the same session_id
    and prompt_id, and the pair says "these words led to that skill". Without
    it the router could only ever learn from its own announcements, i.e.
    re-learn its own table.
    """
    if os.environ.get("SKILL_ROUTER_NO_LEARN") == "1":
        return
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from catalog_match import tokenize  # type: ignore[import-not-found]
        tokens = sorted(set(tokenize(prompt)))[:40]
    except ImportError:
        tokens = []
    if not tokens:
        return
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "type": "prompt",
                "session_id": meta.get("session_id"),
                "prompt_id": meta.get("prompt_id"),
                "path": path,
                "route": chain[0].skill if chain else None,
                "note": note or None,
                "tokens": tokens,
            }) + "\n")
    except OSError:
        pass


def main() -> int:
    if os.environ.get("SKILL_ROUTER_OFF") == "1":   # a Kimi offload child, or the user
        return 0
    prompt, hook_meta = _read_input()
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
    # Explicit slash-command invocation: the user already chose the skill. Stand
    # down entirely — no classification, no embedder rescue, no IRON rule. The
    # router must never reclassify an explicit command into a different skill.
    # (Pending was already cleared above in hook_mode.)
    if explicit_invocation(prompt):
        if os.environ.get("SKILL_ROUTER_DEBUG") == "1":
            print("[skill-router] (stand-down — explicit slash-command invocation)",
                  file=sys.stderr)
        return 0
    global PREV_ASSISTANT, LOADED, SESSION_ID
    SESSION_ID = str(hook_meta.get("session_id") or "") if hook_mode else ""
    PREV_ASSISTANT = previous_assistant_tail(hook_meta) if hook_mode else ""
    LOADED = loaded_this_session(hook_meta) if hook_mode else frozenset()
    try:
        path, chain, domains, announcement = route(prompt)
    except Exception as e:
        print(f"[skill-router-error] {e}", file=sys.stderr)
        return 1
    if hook_mode:
        log_prompt_event(prompt, hook_meta, path, chain)
    if announcement:
        print(announcement)
        # Logging, like pending state, belongs to hook mode only. Every test
        # run, doctor smoke prompt and manual probe used to write a
        # chain-start event that no Skill call would ever follow — the learner
        # then read those as announcements you ignored, drove the follow rate
        # for systematic-debugging to zero, and the embedder rescue started
        # refusing it. The router was being taught by its own test suite.
        # A card-less line (quota nudge, suggestion) announces no chain: nothing
        # to enforce, and a chain-start with no steps would teach the learner
        # about an announcement nobody could follow.
        if hook_mode and chain:
            log_chain(path, chain, domains, meta=hook_meta)
            write_pending(chain, path, domains, meta=hook_meta)
    else:
        # Both regex triage and embedding fallback returned SKIP. Last-ditch
        # path: check the online catalog for a novel uninstalled skill that
        # token-matches the prompt. Soft suggestion only — no write_pending(),
        # no IRON enforcement, since the skill can't actually be invoked.
        suggestion = suggest_online_skill(prompt)
        if suggestion is not None:
            print(render_online_suggestion(suggestion))
        elif os.environ.get("SKILL_ROUTER_DEBUG") == "1":
            print(f"[skill-router] (silent — no clear route for prompt of {len(prompt)} chars)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
