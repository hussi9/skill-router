#!/usr/bin/env python3
"""Compare current routing, gate v2, and frozen-source v3 without live writes."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import benchmark_global_fix as shared

ARMS = ('current', 'global_gate_v2', 'global_source_v3')


def run(cases_path, index_path, capabilities_path, out, max_calls=54, timeout=1.2):
    import jev_gate_v2 as previous
    import jev_source_v3 as challenger
    import skill_capabilities
    cases_path, capabilities_path = Path(cases_path), Path(capabilities_path)
    cases = json.loads(cases_path.read_text())
    if len(cases) > 18 or not 0 < max_calls <= 54:
        raise ValueError('source v3 requires at most 18 cases and 54 requests')
    capabilities = json.loads(capabilities_path.read_text())
    if not isinstance(capabilities, dict):
        raise ValueError('capability snapshot must be an object')
    skill_capabilities.validate_snapshot(capabilities)
    return shared.run(cases_path, index_path, out, max_calls, timeout,
        selectors={'global_gate_v2': (previous, {}),
                   'global_source_v3': (challenger, {'capabilities': capabilities})},
        gated_arms=ARMS[1:], process_candidate_arms=('global_source_v3',),
        no_fallback_arms=('global_source_v3',),
        dependency_paths=(__file__, previous.__file__, challenger.__file__,
                          skill_capabilities.__file__, skill_capabilities.build_index.__file__,
                          capabilities_path))


def main():
    parser = argparse.ArgumentParser(__doc__)
    for name in ('cases', 'index', 'capabilities', 'out'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--max-calls', type=int, default=54)
    args = parser.parse_args()
    try:
        summary = run(args.cases, args.index, args.capabilities, args.out, args.max_calls)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({arm:summary[arm]['primary'] for arm in ARMS}, indent=2))


if __name__ == '__main__':
    main()
