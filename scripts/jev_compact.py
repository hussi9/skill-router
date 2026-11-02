#!/usr/bin/env python3
"""Offline compact evidence adapter; never changes live sources or decision rules."""
from __future__ import annotations
import re
import hashlib
from pathlib import Path
import jev_source_v3 as source
import skill_capabilities

SOURCE_LIMIT = 1000
NEGATIVE = re.compile(r'\b(?:do not|does not|don.t|not for|not a|unless|avoid|exclude|out of scope|never|instead|only when|only for)\b', re.I)


def _excerpt(text, limit):
    text = ' '.join(str(text or '').split())
    if len(text) <= limit:
        return text
    # Retain whole words, explicitly flag an excerpt instead of inventing a claim.
    return text[:limit-2].rsplit(' ', 1)[0] + ' …'


def compact_evidence(evidence):
    """Deterministic excerpts: reserve space for boundaries before overview/scope."""
    lines = [line.strip() for line in evidence.splitlines() if line.strip()]
    sentences = [part.strip() for line in lines for part in re.split(r'(?<=[.!?;])\s+', line) if part.strip()]
    negatives = list(dict.fromkeys(part for part in sentences if NEGATIVE.search(part)))
    scope = evidence.split('Introduction:', 1)[0].removeprefix('Explicit scope sections:')
    intro = evidence.split('Introduction:',1)[-1].split('Frontmatter:',1)[0] if 'Introduction:' in evidence else ''
    parts = []
    if negatives:
        parts.append('Scope boundaries (excerpts): '+_excerpt(' '.join(negatives), 360))
    if scope.strip():
        parts.append('Authored scope (excerpt): '+_excerpt(scope, 360))
    if intro.strip():
        parts.append('Overview (excerpt): '+_excerpt(intro, 180))
    if not parts and evidence.strip():
        parts.append('Source excerpt: '+_excerpt(evidence, 800))
    return '\n'.join(parts)[:SOURCE_LIMIT]


SCOPE_HEADING = re.compile(r'\b(?:capabilities|capability|scope|safety|guardrails|constraints|boundaries)\b|\bwhen\b.{0,32}\b(?:use|not)\b|\buse\b.{0,24}\bwhen\b', re.I)


def authored_evidence(text):
    """Exact-word scope headings avoid matching 'use' inside AskUserQuestion."""
    text = re.sub(r'(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$', '', text)
    if text.startswith('---\n'):
        end = text.find('\n---',4)
        if end >= 0:
            text = text[end+4:]
    parts = re.split(r'(?m)^(#{1,6} .*)$',text)
    sections = [parts[i]+'\n'+parts[i+1] for i in range(1,len(parts)-1,2)
                if SCOPE_HEADING.search(parts[i])]
    title = next((i for i in range(1,len(parts)-1,2) if parts[i].startswith('# ')),None)
    intro = parts[title+1] if title is not None else parts[0]
    return 'Explicit scope sections:\n'+'\n'.join(sections)+'\nIntroduction:\n'+intro[:1200]+'\nFrontmatter:\n'


def build_snapshot(full):
    skill_capabilities.validate_snapshot(full)
    skills = {}
    for name, record in full['skills'].items():
        evidence = record['evidence']
        if record['status'] == 'source':
            path = Path(record['source_path'])
            if path.stat().st_size > skill_capabilities.MAX_SOURCE_BYTES:
                raise ValueError('compact source too large')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != record['source_sha256']:
                raise ValueError('compact source hash mismatch')
            evidence = authored_evidence(raw.decode('utf-8',errors='replace'))
        skills[name] = {**record, 'evidence': compact_evidence(evidence)}
    return {**full, 'max_evidence_chars': SOURCE_LIMIT, 'skills': skills,
            'representation': 'compact-excerpts-v1'}


def compact_entries(entries):
    """Bound indexed overlap while retaining identity, kinds and trigger meaning."""
    return [{**entry, 'description': _excerpt(entry.get('description'), 320),
             'routing_summary': _excerpt(entry.get('routing_summary'), 160),
             'use_when': [_excerpt(v,100) for v in (entry.get('use_when') or [])[:2]]
                if isinstance(entry.get('use_when'), (list,tuple)) else []}
            for entry in entries]


def choose(prompt, context='', entries=None, timeout=None, *, transport=None,
           shortlist=None, capabilities=None, process_candidate=None):
    # Precomputed frozen evidence avoids source I/O in the timed provider path.
    entries = source.applicability.base.load_entries() if entries is None else entries
    return source.choose(prompt, context, compact_entries(entries or []), timeout,
        transport=transport, shortlist=shortlist, capabilities=capabilities,
        process_candidate=process_candidate)
