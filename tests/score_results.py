#!/usr/bin/env python3
"""Score routing test results against ground truth."""
import json
import sys
from pathlib import Path

with open('/tmp/sm_test_response.txt') as f:
    raw = f.read().strip()

if not raw:
    print("ERROR: No response from Claude")
    sys.exit(1)

# Parse JSON array from response
parsed = []
try:
    if raw.startswith('['):
        parsed = json.loads(raw)
    else:
        start = raw.find('[')
        end = raw.rfind(']') + 1
        if start >= 0:
            parsed = json.loads(raw[start:end])
        else:
            print(f"ERROR: No JSON array found in response")
            print(f"Response was: {raw[:300]}")
            sys.exit(1)
except Exception as e:
    print(f"PARSE ERROR: {e}")
    print(f"Raw: {raw[:300]}")
    sys.exit(1)

ground_truth = [
    (1,  "BROKEN",  "systematic-debugging",           "sonnet", "JS error → BROKEN"),
    (2,  "BROKEN",  "test-runner",                    "sonnet", "failing tests → BROKEN"),
    (3,  "BROKEN",  "systematic-debugging",           "opus",   "prod incident → opus"),
    (4,  "BROKEN",  "systematic-debugging",           "sonnet", "TS errors → BROKEN"),
    (5,  "BROKEN",  "systematic-debugging",           "sonnet", "deploy fail → BROKEN"),
    (6,  "BROKEN",  "systematic-debugging",           "opus",   "prod critical → opus"),
    (7,  "BUILD",   "frontend-design",                "sonnet", "new UI → BUILD"),
    (8,  "BUILD",   "feature-dev",                    "sonnet", "new API → BUILD"),
    (9,  "BUILD",   "connect-apps",                   "sonnet", "new integration → BUILD"),
    (10, "BUILD",   "db-expert",                      "sonnet", "new schema → BUILD"),
    (11, "BUILD",   "writing-skills",                 "sonnet", "new skill file → BUILD"),
    (12, "OPERATE", "refactor",                       "sonnet", "clean up code → OPERATE"),
    (13, "OPERATE", "test-driven-development",        "sonnet", "add tests → OPERATE"),
    (14, "OPERATE", "verification-before-completion", "sonnet", "deploy → OPERATE"),
    (15, "OPERATE", "requesting-code-review",         "sonnet", "PR review → OPERATE"),
    (16, "BUILD",   "brainstorming",                  "sonnet", "fix+add ambiguous → BUILD"),
    (17, "BUILD",   "brainstorming",                  "sonnet", "refactor+add ambiguous → BUILD"),
    (18, "SKIP",    "",                               "",       "simple question → SKIP"),
    (19, "SKIP",    "",                               "",       "factual question → SKIP"),
    (20, "SKIP",    "",                               "",       "single read → SKIP"),
]

print(f"\n{'='*62}")
print(f"ROUTING TEST — Real Claude Code (claude-sonnet-4-6)")
print(f"{'='*62}\n")

results = []
for gt_id, exp_path, exp_skill, exp_model, desc in ground_truth:
    actual = next((r for r in parsed if r.get('id') == gt_id), None)
    if not actual:
        print(f"[{gt_id:02d}] MISSING  {desc}")
        results.append({"pass": False, "path_ok": False, "skill_ok": False, "model_ok": False})
        continue

    act_path  = actual.get('path', '').upper()
    act_skill = actual.get('skill', '').lower()
    act_model = actual.get('model', '').lower()

    path_ok  = act_path == exp_path
    skill_ok = (exp_skill == "") or (exp_skill.lower() in act_skill) or (act_skill in exp_skill.lower())
    model_ok = (exp_model == "") or (act_model == exp_model)
    all_ok   = path_ok and skill_ok and model_ok

    icon = "PASS" if all_ok else "FAIL"
    print(f"[{gt_id:02d}] {icon}  {desc}")

    if not all_ok:
        print(f"     Expected: path={exp_path}  skill={exp_skill}  model={exp_model}")
        print(f"     Got:      path={act_path}  skill={act_skill}  model={act_model}")
        wrong = [d for d, ok in [("PATH", path_ok), ("SKILL", skill_ok), ("MODEL", model_ok)] if not ok]
        print(f"     Wrong:    {', '.join(wrong)}\n")

    results.append({"pass": all_ok, "path_ok": path_ok, "skill_ok": skill_ok, "model_ok": model_ok})

total    = len(results)
passed   = sum(1 for r in results if r["pass"])
path_ok  = sum(1 for r in results if r["path_ok"])
skill_ok = sum(1 for r in results if r["skill_ok"])
model_ok = sum(1 for r in results if r["model_ok"])

print(f"\n{'='*62}")
print(f"SUMMARY")
print(f"{'='*62}")
print(f"Overall (path+skill+model):  {passed}/{total} ({passed/total*100:.0f}%)")
print(f"Path routing:                {path_ok}/{total} ({path_ok/total*100:.0f}%)")
print(f"Skill selection:             {skill_ok}/{total} ({skill_ok/total*100:.0f}%)")
print(f"Model selection:             {model_ok}/{total} ({model_ok/total*100:.0f}%)")
print(f"{'='*62}\n")

# Save to JSON
results_path = Path(__file__).parent / "routing_test_real_results.json"
with open(results_path, 'w') as f:
    json.dump({"summary": {"total": total, "passed": passed,
                         "accuracy": round(passed/total*100,1),
                         "path_accuracy": round(path_ok/total*100,1),
                         "skill_accuracy": round(skill_ok/total*100,1),
                         "model_accuracy": round(model_ok/total*100,1)},
             "raw_response": raw}, f, indent=2)
print(f"Results saved to {results_path}")
