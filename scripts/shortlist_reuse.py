"""Offline recall supplement reusing installed-index lexical retrieval.

Returned names are candidates, never an authorization or confidence score.
"""
from __future__ import annotations

import index_match
import jev_choose

MAX_POOL = 18
LEXICAL_LIMIT = 6


def expand(prompt, context, entries, index_path, baseline_shortlist):
    """Union baseline, current-request and prior-context recall, in that order.

    The caller supplies a frozen index path. Context matches cannot remove or
    outrank request candidates; downstream applicability must obey the user.
    """
    if not index_path:
        raise ValueError('a frozen index_path is required')
    idx = index_match.load_index(str(index_path))
    supplied = {row['name'] for row in entries if isinstance(row, dict)
               and isinstance(row.get('name'), str) and row.get('kind') != 'process'
               and row.get('type') not in jev_choose.NOT_ROUTABLE
               and row['name'] != jev_choose.SELF_SKILL}
    installed = {doc.name for doc in idx.docs if doc.kind != 'process'} if idx else supplied
    allowed = supplied & installed
    baseline = list(dict.fromkeys(name for name in (baseline_shortlist or [])
                                  if isinstance(name, str) and name in allowed))
    if len(baseline) > MAX_POOL:
        raise ValueError('baseline shortlist exceeds maximum pool; refusing candidate removal')
    request, _ = jev_choose._bounded(prompt, 16000)
    previous, _ = jev_choose._bounded(context, 4000)
    kinds = tuple(sorted({doc.kind for doc in idx.docs if doc.kind != 'process'})) if idx else ()
    result = []

    def append(names):
        for name in names:
            if isinstance(name, str) and name in allowed and name not in result:
                result.append(name)
                if len(result) == MAX_POOL:
                    return

    def retrieve(text):
        # Rank the frozen catalog before eligibility filtering so commands or
        # names absent from supplied entries cannot consume six recall slots.
        ranked = index_match.rank(text, limit=len(idx.docs),
                                  index_path=str(index_path), kinds=kinds)
        return [match.name for match in ranked if match.name in allowed][:LEXICAL_LIMIT]

    append(baseline)
    if idx is not None and len(result) < MAX_POOL:
        append(retrieve(request))
    if idx is not None and previous and len(result) < MAX_POOL:
        append(retrieve(previous))
    return result
