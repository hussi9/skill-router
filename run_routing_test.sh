#!/bin/bash
# Run all routing test cases through real Claude Code in one batched call

PROMPT='You are running a routing accuracy test for skill-router. For each of the 20 tasks below, apply the skill-router 3-question routing rules and output ONLY a JSON array. No explanation, no markdown, no prose — just the raw JSON array starting with [ and ending with ].

Format: [{"id":1,"path":"BROKEN","skill":"systematic-debugging","agent":"general-purpose","model":"sonnet"},...]

Valid path values: BROKEN, BUILD, OPERATE, SKIP
Valid model values: haiku, sonnet, opus

Tasks:
1. TypeError: Cannot read property map of undefined in ProductList.tsx line 42
2. My test suite is failing after the refactor — 12 tests red
3. Production is down. 500 errors on /api/checkout for the last 10 minutes
4. TypeScript is throwing 47 type errors after I updated the auth types
5. The deploy failed — Vercel build error in CI pipeline
6. CRITICAL: database corrupted in production, users losing data right now
7. Add a dark mode toggle to the settings page
8. Build a new REST API endpoint for user analytics
9. I need to integrate Stripe payments into checkout
10. Create a new database schema for the notifications system
11. Write a new Claude skill file for ML model routing
12. The auth service has grown to 800 lines. Clean it up.
13. Add test coverage to the payment module — it has 0% tests
14. Deploy the current branch to production
15. Review my PR before I merge
16. Fix the login bug AND add OAuth support while you are at it
17. Refactor the auth module AND add tests to it
18. What does this function do?
19. What is the difference between map and flatMap?
20. Show me line 42 of auth.ts'

DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Running 20-case routing test through real Claude Code (claude-sonnet-4-6)..."
echo ""

# Run claude -p and save full JSON output
claude -p "$PROMPT" --model claude-sonnet-4-6 --output-format json > /tmp/sm_test_raw.json 2>/dev/null

# Extract the result text from the JSON event stream
python3 "$DIR/extract_result.py" > /tmp/sm_test_response.txt

echo "Claude response:"
cat /tmp/sm_test_response.txt
echo ""

# Score it
python3 "$DIR/score_results.py"
