#!/usr/bin/env python3
"""Freeze installed skill capabilities as bounded, inert source evidence."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import build_index
import jev_global

MAX_SOURCE_BYTES = 1024 * 1024
MAX_EVIDENCE_CHARS = 6000
SECTION = re.compile(r'capabilit|when.{0,12}(?:use|not)|use.{0,12}when|what.{0,12}(?:does|do)|scope|trigger', re.I)


def source_evidence(text):
    """Extract frontmatter, introduction and explicit scope sections, never execute."""
    text = text.replace('\x00', '')
    text = re.sub(r'(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$', '', text)
    front = ''
    if text.startswith('---\n'):
        end = text.find('\n---', 4)
        if end >= 0:
            front, text = text[4:end][:1200], text[end + 4:]
    parts = re.split(r'(?m)^(#{1,6} .*)$', text)
    intro = parts[0][:1200]
    title = next((i for i in range(1, len(parts) - 1, 2) if parts[i].startswith('# ')), None)
    if title is not None:
        # Prefer the authored capability overview below the document title over
        # installation instructions that may precede it.
        intro = (parts[title] + '\n' + parts[title + 1]).split('\n\n')[0:2]
        intro = '\n\n'.join(intro)[:1200]
    sections = []
    for i in range(1, len(parts) - 1, 2):
        if SECTION.search(parts[i]):
            sections.append((parts[i] + '\n' + parts[i + 1])[:1800])
    # Many skills start with a title and put their introduction below it.
    if not intro.strip() and len(parts) >= 3:
        intro = (parts[1] + '\n' + parts[2])[:1200]
    return ('Explicit scope sections:\n' + '\n'.join(sections)[:3600] +
            '\nIntroduction:\n' + intro + '\nFrontmatter:\n' + front)[:MAX_EVIDENCE_CHARS]


def build_snapshot(entries, catalog=None):
    catalog = build_index.load_catalog() if catalog is None else catalog
    by_name = {}
    for entry in catalog.get('entries', []):
        if isinstance(entry, dict):
            by_name.setdefault(entry.get('name'), []).append(entry)
    records = {}
    for entry in jev_global.eligible(entries):
        name = entry['name']
        candidates = by_name.get(name, [])
        paths = {str(path.resolve()) for candidate in candidates
                 if (path := build_index._skill_file(candidate)) is not None}
        record = {'name': name, 'kind': entry.get('kind'), 'source_path': None,
                  'source_sha256': None, 'status': 'index_fallback', 'reason': '',
                  'evidence': ''}
        if len(paths) != 1:
            record['reason'] = 'ambiguous_source' if len(paths) > 1 else 'missing_source'
        else:
            path = Path(next(iter(paths)))
            try:
                if path.stat().st_size > MAX_SOURCE_BYTES:
                    raise ValueError('source_too_large')
                raw = path.read_bytes()
                record.update(source_path=str(path), source_sha256=hashlib.sha256(raw).hexdigest(),
                              evidence=source_evidence(raw.decode('utf-8', errors='replace')),
                              status='source', reason='')
            except (OSError, ValueError) as error:
                record['reason'] = 'source_too_large' if isinstance(error, ValueError) else 'unreadable_source'
        records[name] = record
    return {'version': 1, 'max_evidence_chars': MAX_EVIDENCE_CHARS, 'skills': records}


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('version') != 1 or not isinstance(snapshot.get('skills'), dict):
        raise ValueError('invalid_capability_snapshot')
    for name, record in snapshot['skills'].items():
        if not isinstance(record, dict) or record.get('name') != name:
            raise ValueError('invalid_capability_snapshot')
        evidence = record.get('evidence')
        if not isinstance(evidence, str) or len(evidence) > MAX_EVIDENCE_CHARS:
            raise ValueError('invalid_capability_snapshot')
        if record.get('status') == 'source':
            if not isinstance(record.get('source_path'), str) or not re.fullmatch(r'[0-9a-f]{64}', str(record.get('source_sha256', ''))):
                raise ValueError('invalid_capability_snapshot')
        elif record.get('status') != 'index_fallback' or not record.get('reason'):
            raise ValueError('invalid_capability_snapshot')
    return snapshot


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, default=build_index.OUTPUT)
    parser.add_argument('--catalog', type=Path, default=build_index.CATALOG)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    snapshot = build_snapshot(json.loads(args.index.read_text())['entries'], build_index.load_catalog(args.catalog))
    validate_snapshot(snapshot)
    with os.fdopen(os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as target:
        target.write(json.dumps(snapshot, indent=2) + '\n')
    args.out.chmod(0o600)
