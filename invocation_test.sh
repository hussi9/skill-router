#!/bin/bash
# Test whether skills-master causes the Skill tool to actually be INVOKED
# for real task prompts — not just routing declarations, but actual tool calls.
# Uses claude -p and inspects the tool_use events in the JSON output.

TASKS=(
  "TypeError: Cannot read property map of undefined in ProductList.tsx line 42"
  "Add a dark mode toggle to the settings page"
  "The auth service has grown to 800 lines. Clean it up."
  "Production is down. 500 errors on /api/checkout for the last 5 minutes"
  "Deploy the current branch to production"
  "Add test coverage to the payment module — it has 0% tests"
  "Build a new REST API endpoint for user analytics"
  "What does this function do?"
)

EXPECTED_SKILLS=(
  "systematic-debugging"
  "frontend-design"
  "refactor"
  "systematic-debugging"
  "verification-before-completion"
  "test-driven-development"
  "system-design"
  "SKIP"
)

echo ""
echo "============================================================"
echo "SKILL INVOCATION TEST — Does the Skill tool actually fire?"
echo "============================================================"
echo ""

python3 << 'PYEOF'
import subprocess, json, sys, time

tasks = [
    ("TypeError: Cannot read property map of undefined in ProductList.tsx line 42", "systematic-debugging", "BROKEN"),
    ("Add a dark mode toggle to the settings page", "frontend-design", "BUILD"),
    ("The auth service has grown to 800 lines. Clean it up.", "refactor", "OPERATE"),
    ("Production is down. 500 errors on /api/checkout for the last 5 minutes", "systematic-debugging", "BROKEN"),
    ("Deploy the current branch to production", "verification-before-completion", "OPERATE"),
    ("Add test coverage to the payment module — it has 0% tests", "test-driven-development", "OPERATE"),
    ("Build a new REST API endpoint for user analytics", "system-design", "BUILD"),
    ("What does this function do?", "SKIP", "SKIP"),
]

# Instruction appended to each task to stop before execution
STOP_INSTRUCTION = " [ROUTING TEST: identify which skill to invoke and call it via the Skill tool, then STOP — do not execute the task itself]"

results = []

for i, (task, expected_skill, expected_path) in enumerate(tasks):
    prompt = task + STOP_INSTRUCTION
    print(f"[{i+1:02d}] Testing: {task[:60]}...")

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json"],
            capture_output=True, text=True, timeout=45,
            cwd="/tmp"
        )

        raw = result.stdout.strip()
        if not raw:
            print(f"     ERROR: No output\n")
            results.append({"task": task, "skill_invoked": None, "expected": expected_skill, "pass": False, "error": "no output"})
            continue

        # Parse events - could be array or single object
        try:
            events = json.loads(raw)
            if not isinstance(events, list):
                events = [events]
        except:
            print(f"     ERROR: Parse failed\n")
            results.append({"task": task, "skill_invoked": None, "expected": expected_skill, "pass": False, "error": "parse failed"})
            continue

        # Find Skill tool_use events
        skill_calls = []
        for event in events:
            if event.get("type") == "assistant":
                msg = event.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "tool_use" and block.get("name") == "Skill":
                        skill_name = block.get("input", {}).get("skill", "")
                        skill_calls.append(skill_name)

        if expected_skill == "SKIP":
            # Should NOT invoke any skill
            passed = len(skill_calls) == 0
            status = "PASS" if passed else "FAIL"
            skill_str = ", ".join(skill_calls) if skill_calls else "none"
            print(f"     {status}  Skill tool calls: [{skill_str}]  (expected: none for SKIP)")
        else:
            if not skill_calls:
                passed = False
                print(f"     FAIL  No Skill tool called  (expected: {expected_skill})")
            else:
                skill_str = ", ".join(skill_calls)
                # Check if any called skill contains the expected skill name
                passed = any(expected_skill.lower() in s.lower() or s.lower() in expected_skill.lower()
                            for s in skill_calls)
                status = "PASS" if passed else "FAIL"
                print(f"     {status}  Skill called: [{skill_str}]  (expected: {expected_skill})")

        results.append({
            "task": task[:60],
            "expected_skill": expected_skill,
            "expected_path": expected_path,
            "skills_invoked": skill_calls,
            "pass": passed
        })
        print("")

    except subprocess.TimeoutExpired:
        print(f"     TIMEOUT\n")
        results.append({"task": task, "skill_invoked": None, "expected": expected_skill, "pass": False, "error": "timeout"})
    except Exception as e:
        print(f"     ERROR: {e}\n")
        results.append({"task": task, "skill_invoked": None, "expected": expected_skill, "pass": False, "error": str(e)})

    time.sleep(1)

total = len(results)
passed = sum(1 for r in results if r.get("pass"))
skill_fired = sum(1 for r in results if r.get("skills_invoked") and len(r["skills_invoked"]) > 0)

print("============================================================")
print("INVOCATION SUMMARY")
print("============================================================")
print(f"Correct skill invoked:  {passed}/{total} ({passed/total*100:.0f}%)")
print(f"Skill tool fired at all: {skill_fired}/{total-1} non-SKIP cases (denominator excludes SKIP)")
print("============================================================")

# Save results
with open('/Users/airbook/devpro/skills-master/invocation_test_results.json', 'w') as f:
    json.dump({"summary": {"total": total, "passed": passed, "accuracy": round(passed/total*100,1)}, "cases": results}, f, indent=2)
print("\nResults saved to invocation_test_results.json")

PYEOF
