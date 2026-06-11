#!/usr/bin/env python3
"""Extract the text result from claude -p --output-format json output."""
import json
import sys

with open('/tmp/sm_test_raw.json') as f:
    content = f.read().strip()

if not content:
    print("ERROR: /tmp/sm_test_raw.json is empty — did claude -p run successfully?", file=sys.stderr)
    sys.exit(1)

# Output is either a JSON array of events or a single result object
def find_result(events):
    if isinstance(events, list):
        for event in events:
            if isinstance(event, dict) and event.get('type') == 'result':
                return event.get('result', '')
    elif isinstance(events, dict):
        if events.get('type') == 'result':
            return events.get('result', '')
        # Some versions return {result: "..."} at top level
        if 'result' in events:
            return events['result']
    return None

try:
    events = json.loads(content)
    result = find_result(events)
    if result is not None:
        print(result)
    else:
        # Try newline-delimited JSON
        result = None
        for line in content.split('\n'):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                if event.get('type') == 'result':
                    result = event.get('result', '')
                    break
            except json.JSONDecodeError:
                continue
        if result is not None:
            print(result)
        else:
            print("ERROR: No 'result' event found in claude output. Raw output:", file=sys.stderr)
            print(content[:500], file=sys.stderr)
            sys.exit(1)
except json.JSONDecodeError as e:
    print(f"ERROR: Failed to parse claude output as JSON: {e}", file=sys.stderr)
    print(f"Raw output (first 500 chars): {content[:500]}", file=sys.stderr)
    sys.exit(1)
