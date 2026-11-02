#!/usr/bin/env python3
"""Experimental advisory ranking of supplied external skill candidates.

--out is a new directory containing result.json and isolated chooser telemetry/cache.
This command never searches, installs, invokes skills, or returns executable routes.
External descriptions are untrusted matching data; any result requires human review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit

import jev_choose


def normalize(candidates, installed):
    if not isinstance(candidates, list):
        raise ValueError('candidates must be a JSON list')
    if not isinstance(installed, dict) or not isinstance(installed.get('entries'), list):
        raise ValueError('installed index must contain entries')
    installed_names = {str(e.get('name', '')).strip().casefold() for e in installed['entries']
                       if isinstance(e, dict)}
    result, seen = {}, set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError('candidate must be an object')
        fields = {}
        for field in ('name', 'description', 'source_url', 'source'):
            value = candidate.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'candidate requires {field}')
            fields[field] = value.strip()
        url = urlsplit(fields['source_url'])
        if url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password:
            raise ValueError('candidate source_url must be a public HTTP(S) reference')
        name = fields['name'].casefold()
        if name in installed_names:
            continue
        identity = (name, fields['source_url'])
        if identity in seen:
            continue
        seen.add(identity)
        # A complete source identity prevents same-name candidates on different
        # registries from sharing chooser labels. The ID cannot invoke a skill.
        key = 'external-' + hashlib.sha256(json.dumps(
            [fields['source'], fields['source_url'], name]).encode()).hexdigest()
        result[key] = fields
    if len(result) > 20:
        raise ValueError('at most 20 eligible candidates; narrow retrieval explicitly')
    return result


def rank(query, candidates, installed, out: Path):
    if not isinstance(query, str) or not query.strip():
        raise ValueError('query must not be empty')
    if jev_choose.ROUTE_AT != .8:
        raise ValueError('advisory experiment requires unchanged 0.8 threshold')
    eligible = normalize(candidates, installed)
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    result = {'status': 'abstain', 'reason': 'no_eligible_candidates', 'confidence': None,
              'source_url': None, 'requires_review': True, 'invokable': False,
              'eligible_candidates': len(eligible), 'experimental': True}
    if eligible:
        entries = [{'name': key, 'kind': 'domain', 'type': 'skill',
                    'description': f"{value['name']}: {value['description']}"}
                   for key, value in eligible.items()]
        original_cache = jev_choose.CACHE
        try:
            jev_choose.CACHE = out / 'cache'
            choice = jev_choose.choose(query, entries=entries)
            if choice is None:
                result['reason'] = 'chooser_error'
            else:
                pick = choice.domain
                confidence = pick.confidence
                valid_confidence = (isinstance(confidence, (int, float))
                                    and math.isfinite(confidence) and 0 <= confidence <= 1)
                if not valid_confidence or (pick.name is not None and pick.name not in eligible):
                    result['reason'] = 'invalid_chooser_result'
                else:
                    result['confidence'] = confidence
                    if pick.name is None:
                        result['reason'] = 'no_fit'
                    elif confidence < .8:
                        result['reason'] = 'below_threshold'
                    else:
                        candidate = eligible[pick.name]
                        result.update(status='candidate_to_review', reason='confident_match',
                                      candidate_id=pick.name, name=candidate['name'],
                                      source=candidate['source'], source_url=candidate['source_url'])
        except Exception:
            # Never expose provider errors or candidate instructions in output.
            result['reason'] = 'chooser_error'
        finally:
            jev_choose.CACHE = original_cache
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', required=True)
    for field in ('candidates', 'installed-index', 'out'):
        parser.add_argument('--' + field, required=True, type=Path)
    args = parser.parse_args()
    try:
        result = rank(args.query, json.loads(args.candidates.read_text()),
                      json.loads(args.installed_index.read_text()), args.out)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
