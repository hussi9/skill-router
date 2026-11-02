#!/usr/bin/env python3
"""Fresh full-versus-compact evidence comparison, offline and bounded."""
import argparse
import json
from pathlib import Path
import benchmark_global_fix as shared
import jev_source_v3 as full
import jev_compact as compact
import jev_gate_v2
import skill_capabilities

ARMS = ('current','source_full','source_compact')


def run(cases, index, full_path, compact_path, out, max_calls=36):
    if len(json.loads(Path(cases).read_text())) > 12 or not 0 < max_calls <= 36:
        raise ValueError('compact experiment allows 12 cases and 36 requests')
    original=json.loads(Path(full_path).read_text())
    smaller=json.loads(Path(compact_path).read_text())
    skill_capabilities.validate_snapshot(original)
    if smaller != compact.build_snapshot(original):
        raise ValueError('compact snapshot must match deterministic frozen full snapshot')
    return shared.run(cases,index,out,max_calls,
        selectors={'source_full':(full,{'capabilities':original}),
                   'source_compact':(compact,{'capabilities':smaller})},
        gated_arms=ARMS[1:],process_candidate_arms=ARMS[1:],no_fallback_arms=ARMS[1:],
        dependency_paths=(__file__,full.__file__,compact.__file__,jev_gate_v2.__file__,
                          skill_capabilities.__file__,skill_capabilities.build_index.__file__,
                          full_path,compact_path))


def main():
    parser=argparse.ArgumentParser(__doc__)
    for name in ('cases','index','full','compact','out'):
        parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--max-calls',type=int,default=36)
    args=parser.parse_args()
    result=run(args.cases,args.index,args.full,args.compact,args.out,args.max_calls)
    print(json.dumps({a:result[a]['primary'] for a in ARMS},indent=2))

if __name__=='__main__':main()
