#!/usr/bin/env python3
"""Compare frozen current routing, applicability v1, and gate v2; no live writes."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import benchmark_global_fix as shared

ARMS = ('current', 'global_applicability', 'global_gate_v2')


def run(cases_path, index_path, out, max_calls=72, timeout=1.2):
    import jev_applicability as previous
    import jev_gate_v2 as challenger
    summary = shared.run(cases_path, index_path, out, max_calls, timeout,
        selectors={'global_applicability': (previous, {}), 'global_gate_v2': (challenger, {})},
        gated_arms=ARMS[1:], dependency_paths=(__file__,))
    return summary


def main():
    parser = argparse.ArgumentParser(__doc__)
    for name in ('cases', 'index', 'out'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--max-calls', type=int, default=72)
    args = parser.parse_args()
    try:
        summary = run(args.cases, args.index, args.out, args.max_calls)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({arm:summary[arm]['primary'] for arm in ARMS}, indent=2))


if __name__ == '__main__':
    main()
