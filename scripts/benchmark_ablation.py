#!/usr/bin/env python3
"""Isolated retrieval and response-format experiments; never install live routing."""
from pathlib import Path
import argparse
import json
import benchmark_global_fix as shared
import jev_source_v3 as source
import jev_choice_fit as binary
import jev_gate_v2
import shortlist_reuse
import index_match
import skill_capabilities


def run(mode, cases, index, capabilities, out, max_calls=36):
    if mode not in ('shortlist', 'scoring'):
        raise ValueError('unknown ablation')
    snapshot = json.loads(Path(capabilities).read_text())
    skill_capabilities.validate_snapshot(snapshot)
    if len(json.loads(Path(cases).read_text())) > 12 or max_calls > 36:
        raise ValueError('each ablation allows at most 12 cases and 36 requests')
    arms = ('source_fixed', 'source_retrieval') if mode == 'shortlist' else ('source_noul', 'source_binary')
    selectors = {arms[0]: (source, {'capabilities': snapshot}),
                 arms[1]: (source if mode == 'shortlist' else binary, {'capabilities': snapshot})}
    builders = {arm: shortlist_reuse.expand for arm in (arms[1:] if mode == 'shortlist' else arms)}
    return shared.run(cases, index, out, max_calls,
        selectors=selectors, gated_arms=arms, process_candidate_arms=arms,
        no_fallback_arms=arms, shortlist_builders=builders,
        dependency_paths=(__file__, source.__file__, binary.__file__, shortlist_reuse.__file__,
                          index_match.__file__, jev_gate_v2.__file__, skill_capabilities.__file__,
                          skill_capabilities.build_index.__file__, Path(capabilities)))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--mode', choices=('shortlist', 'scoring'), required=True)
    for name in ('cases', 'index', 'capabilities', 'out'):
        parser.add_argument('--'+name, required=True, type=Path)
    parser.add_argument('--max-calls', type=int, default=36)
    args = parser.parse_args()
    result = run(args.mode, args.cases, args.index, args.capabilities, args.out, args.max_calls)
    print(json.dumps({k:v['primary'] for k,v in result.items() if isinstance(v,dict) and 'primary' in v}, indent=2))

if __name__ == '__main__':
    main()
