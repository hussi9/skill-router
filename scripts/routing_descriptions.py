#!/usr/bin/env python3
"""Versioned, source-pinned routing metadata; never modifies skill source files."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path

DEFAULT_OVERLAY = Path(__file__).resolve().parents[1] / 'references' / 'routing-descriptions.json'
KINDS = {'process', 'domain', 'design', 'meta', 'project'}


def load_overlay(path: Path = DEFAULT_OVERLAY) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def apply_overlay(index: dict, overlay: dict | None = None) -> dict:
    """Copy index; pin rules to source content and retain original enrichment."""
    overlay = load_overlay() if overlay is None else overlay
    out = copy.deepcopy(index)
    fingerprint = hashlib.sha256(json.dumps(overlay, sort_keys=True).encode()).hexdigest()
    provenance = {'version': overlay.get('version'), 'sha256': fingerprint,
                  'applied': 0, 'warnings': []}
    rules = overlay.get('rules', {})
    if not isinstance(rules, dict):
        rules = {}
    for entry in out.get('entries', []):
        # Always reset prior derived state, even if a rule was removed or invalidated.
        if 'source_kind' in entry:
            entry['kind'] = entry.pop('source_kind')
        for field in ('routing_summary', 'routing_alias', 'routing_description_version',
                      'routing_description_sha256'):
            entry.pop(field, None)
        rule = rules.get(entry.get('name'))
        if not isinstance(rule, dict):
            continue
        name = entry['name']
        if not rule.get('expected_source_hash') or rule['expected_source_hash'] != entry.get('source_hash'):
            provenance['warnings'].append(f'{name}: stale_source_hash; overlay skipped')
            continue
        summary = rule.get('routing_summary')
        if not isinstance(summary, str) or not 0 < len(summary.strip()) <= 320:
            provenance['warnings'].append(f'{name}: invalid_summary; overlay skipped')
            continue
        entry['routing_summary'] = summary.strip()
        if rule.get('routing_kind') in KINDS:
            entry.setdefault('source_kind', entry.get('kind'))
            entry['kind'] = rule['routing_kind']
        entry['routing_description_version'] = overlay.get('version')
        entry['routing_description_sha256'] = fingerprint
        provenance['applied'] += 1
    out['routing_descriptions'] = provenance
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--overlay', type=Path, default=DEFAULT_OVERLAY)
    args = parser.parse_args()
    out = apply_overlay(json.loads(args.input.read_text()), load_overlay(args.overlay))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=1) + '\n')
    print(json.dumps(out['routing_descriptions']))

if __name__ == '__main__':
    main()
