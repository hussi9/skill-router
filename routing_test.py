#!/usr/bin/env python3
"""
skills-master routing accuracy test
Tests whether Claude correctly applies the 3-question triage router
to produce the right Skill + Agent + Model dispatch triple.
"""

import json
import time
import urllib.request
import urllib.error
from pathlib import Path

OLLAMA_URL = "http://192.168.86.30:11434"
OLLAMA_MODEL = "qwen2.5:7b"

# Load the skills-master routing content
SKILLS_MASTER_DIR = Path.home() / ".claude/skills/skills-master"
CORE = (SKILLS_MASTER_DIR / "skills-master-core.md").read_text()

SYSTEM = f"""You are a routing engine. Apply the skills-master routing rules below to each task prompt.

Always respond in this exact JSON format (no markdown, no extra text):
{{
  "path": "BROKEN|BUILD|OPERATE|SKIP",
  "skill": "skill-name or empty string if SKIP",
  "agent": "agent-name or empty string if SKIP",
  "model": "haiku|sonnet|opus or empty string if SKIP",
  "reasoning": "one sentence explanation"
}}

--- ROUTING RULES ---
{CORE}
"""


def call_ollama(prompt: str) -> tuple[str, int, int]:
    """Call Ollama API, return (response_text, input_tokens, output_tokens)."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Route this task: {prompt}"},
        ],
        "stream": False,
        "options": {"temperature": 0},
    }).encode()

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    text = data["message"]["content"].strip()
    # Ollama doesn't always return token counts; estimate from char length
    input_tokens = data.get("prompt_eval_count", len(SYSTEM) // 4)
    output_tokens = data.get("eval_count", len(text) // 4)
    return text, input_tokens, output_tokens

# Ground truth test cases
# Format: (prompt, expected_path, expected_skill_contains, expected_model, description)
TEST_CASES = [
    # --- BROKEN PATH ---
    (
        "TypeError: Cannot read property 'map' of undefined in ProductList.tsx line 42",
        "BROKEN", "systematic-debugging", "sonnet",
        "JS error → BROKEN path"
    ),
    (
        "My test suite is failing after the refactor — 12 tests red",
        "BROKEN", "test-runner", "sonnet",
        "failing tests → BROKEN path"
    ),
    (
        "Production is down. 500 errors on /api/checkout for the last 10 minutes",
        "BROKEN", "systematic-debugging", "opus",
        "production incident → opus model"
    ),
    (
        "TypeScript is throwing 47 type errors after I updated the auth types",
        "BROKEN", "typescript-expert", "sonnet",
        "TS errors → BROKEN path"
    ),
    (
        "The deploy failed — Vercel build error in CI pipeline",
        "BROKEN", "systematic-debugging", "sonnet",
        "deploy fail → BROKEN path"
    ),
    (
        "CRITICAL: database corrupted in production, users losing data right now",
        "BROKEN", "systematic-debugging", "opus",
        "production critical → opus model"
    ),

    # --- BUILD PATH ---
    (
        "Add a dark mode toggle to the settings page",
        "BUILD", "frontend-design", "sonnet",
        "new UI feature → BUILD path"
    ),
    (
        "Build a new REST API endpoint for user analytics",
        "BUILD", "system-design", "sonnet",
        "new API endpoint → BUILD path"
    ),
    (
        "I need to integrate Stripe payments into checkout",
        "BUILD", "stripe", "sonnet",
        "new integration → BUILD path"
    ),
    (
        "Create a new database schema for the notifications system",
        "BUILD", "db-expert", "sonnet",
        "new schema → BUILD path"
    ),
    (
        "Write a new Claude skill file for ML model routing",
        "BUILD", "writing-skills", "sonnet",
        "new skill file → BUILD path"
    ),

    # --- OPERATE PATH ---
    (
        "The auth service has grown to 800 lines. Clean it up.",
        "OPERATE", "refactor", "sonnet",
        "refactor existing code → OPERATE path"
    ),
    (
        "Add test coverage to the payment module — it has 0% tests",
        "OPERATE", "test-driven-development", "sonnet",
        "add tests → OPERATE path"
    ),
    (
        "Deploy the current branch to production",
        "OPERATE", "verification-before-completion", "sonnet",
        "deploy → OPERATE path"
    ),
    (
        "Review my PR before I merge",
        "OPERATE", "requesting-code-review", "sonnet",
        "PR review → OPERATE path"
    ),

    # --- AMBIGUOUS → default to BUILD (higher complexity) ---
    (
        "Fix the login bug AND add OAuth support while you're at it",
        "BUILD", "brainstorming", "sonnet",
        "fix+add is ambiguous → BUILD (higher complexity default)"
    ),
    (
        "Refactor the auth module AND add tests to it",
        "BUILD", "brainstorming", "sonnet",
        "refactor+add is ambiguous → BUILD"
    ),

    # --- SKIP (no skill needed) ---
    (
        "What does this function do?",
        "SKIP", "", "",
        "simple question → SKIP"
    ),
    (
        "What's the difference between map and flatMap?",
        "SKIP", "", "",
        "factual question → SKIP"
    ),
    (
        "Show me line 42 of auth.ts",
        "SKIP", "", "",
        "single read → SKIP"
    ),
]


def score_result(result: dict, expected_path: str, expected_skill: str, expected_model: str) -> dict:
    path_correct = result.get("path", "").upper() == expected_path.upper()

    # SKIP cases have no skill/model expectation
    if expected_skill == "":
        skill_correct = True
    else:
        actual_skill = result.get("skill", "").lower()
        skill_correct = expected_skill.lower() in actual_skill or actual_skill in expected_skill.lower()

    if expected_model == "":
        model_correct = True
    else:
        model_correct = result.get("model", "").lower() == expected_model.lower()

    return {
        "path_correct": path_correct,
        "skill_correct": skill_correct,
        "model_correct": model_correct,
        "all_correct": path_correct and skill_correct and model_correct,
    }


def run_test():
    results = []
    total_input_tokens = 0
    total_output_tokens = 0

    print(f"\n{'='*60}")
    print("skills-master routing accuracy test")
    print(f"Model: {OLLAMA_MODEL} @ {OLLAMA_URL}")
    print(f"Test cases: {len(TEST_CASES)}")
    print(f"{'='*60}\n")

    for i, (prompt, exp_path, exp_skill, exp_model, description) in enumerate(TEST_CASES):
        try:
            raw, in_tok, out_tok = call_ollama(prompt)
            total_input_tokens += in_tok
            total_output_tokens += out_tok

            # Strip markdown code blocks if present
            if "```" in raw:
                parts = raw.split("```")
                if len(parts) >= 2:
                    raw = parts[1]
                    if raw.startswith("json"):
                        raw = raw[4:]

            try:
                parsed = json.loads(raw.strip())
            except json.JSONDecodeError:
                parsed = {
                    "path": "PARSE_ERROR", "skill": "", "agent": "",
                    "model": "", "reasoning": raw[:120]
                }

            score = score_result(parsed, exp_path, exp_skill, exp_model)
            icon = "PASS" if score["all_correct"] else "FAIL"

            print(f"[{i+1:02d}] {icon}  {description}")

            if not score["all_correct"]:
                print(f"     Prompt:   {prompt[:70]}")
                print(f"     Expected: path={exp_path}  skill={exp_skill}  model={exp_model}")
                print(f"     Got:      path={parsed.get('path')}  skill={parsed.get('skill')}  model={parsed.get('model')}")
                print(f"     Reason:   {parsed.get('reasoning', '')[:90]}")
                mismatches = []
                if not score["path_correct"]: mismatches.append("PATH")
                if not score["skill_correct"]: mismatches.append("SKILL")
                if not score["model_correct"]: mismatches.append("MODEL")
                print(f"     Wrong:    {', '.join(mismatches)}\n")

            results.append({
                "description": description,
                "prompt": prompt,
                "expected": {"path": exp_path, "skill": exp_skill, "model": exp_model},
                "actual": parsed,
                "score": score,
            })

            time.sleep(0.25)

        except Exception as e:
            print(f"[{i+1:02d}] ERROR  {description}: {e}")
            results.append({"description": description, "error": str(e), "score": {"all_correct": False, "path_correct": False, "skill_correct": False, "model_correct": False}})

    # Summary
    passed = sum(1 for r in results if r.get("score", {}).get("all_correct", False))
    path_passed = sum(1 for r in results if r.get("score", {}).get("path_correct", False))
    skill_passed = sum(1 for r in results if r.get("score", {}).get("skill_correct", False))
    model_passed = sum(1 for r in results if r.get("score", {}).get("model_correct", False))
    total = len(TEST_CASES)

    total_cost = 0.0  # Ollama is free/local

    print(f"\n{'='*60}")
    print(f"RESULTS SUMMARY")
    print(f"{'='*60}")
    print(f"Overall (path+skill+model): {passed}/{total} ({passed/total*100:.0f}%)")
    print(f"Path routing only:          {path_passed}/{total} ({path_passed/total*100:.0f}%)")
    print(f"Skill selection only:       {skill_passed}/{total} ({skill_passed/total*100:.0f}%)")
    print(f"Model selection only:       {model_passed}/{total} ({model_passed/total*100:.0f}%)")
    print(f"\nTokens:  ~{total_input_tokens:,} input / ~{total_output_tokens:,} output (estimated)")
    print(f"Cost:    $0.00 (local Ollama)")
    print(f"{'='*60}\n")

    # Save results
    output_path = Path(__file__).parent / "routing_test_results.json"
    with open(output_path, "w") as f:
        json.dump({
            "summary": {
                "total": total,
                "passed": passed,
                "accuracy": round(passed/total*100, 1),
                "path_accuracy": round(path_passed/total*100, 1),
                "skill_accuracy": round(skill_passed/total*100, 1),
                "model_accuracy": round(model_passed/total*100, 1),
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "cost_usd": round(total_cost, 4),
            },
            "cases": results,
        }, f, indent=2)

    print(f"Results saved to: {output_path}")
    return passed, total


if __name__ == "__main__":
    run_test()
