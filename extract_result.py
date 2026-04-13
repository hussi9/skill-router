#!/usr/bin/env python3
"""Extract the text result from claude -p --output-format json output."""
import json

with open('/tmp/sm_test_raw.json') as f:
    content = f.read().strip()

# Output is either a JSON array or newline-delimited JSON
try:
    events = json.loads(content)
    if isinstance(events, list):
        for event in events:
            if isinstance(event, dict) and event.get('type') == 'result':
                print(event.get('result', ''))
                break
    elif isinstance(events, dict) and events.get('type') == 'result':
        print(events.get('result', ''))
except json.JSONDecodeError:
    # Try newline-delimited
    for line in content.split('\n'):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if event.get('type') == 'result':
                print(event.get('result', ''))
                break
        except json.JSONDecodeError:
            continue
