#!/usr/bin/env python3
"""Opt-in absolute applicability experiment; importing never changes live routing."""
from __future__ import annotations

import math
import time
import jev_global as global_selector

base = global_selector.base
GlobalResult = global_selector.GlobalResult
current_shortlist = global_selector.current_shortlist


def build_questions(entries, shortlist):
    """Current summaries only: isolate selection mechanics from representation."""
    by_name = {entry['name']: entry for entry in global_selector.eligible(entries)
               if entry.get('kind') != 'process'}
    keys = {f'fit_{i}': name for i, name in enumerate(shortlist)}
    questions = {'needs_specialist': {
        'type': 'noul',
        'instructions': 'Does the current request call for concrete specialist work? Judge the requested action and deliverable, not its subject. Interpret typos. Use previous assistant context only to resolve what a short continuation asks to proceed with.',
        'criteria': {
            'true': 'The user requests a concrete specialist deliverable, action, implementation, analysis, or inspection. A short approval or continuation explicitly authorizing such work also qualifies.',
            'false': 'The user asks only for a simple explanation, factual clarification, status, or acknowledgment, without requesting specialist work.'}}}
    for qid, name in keys.items():
        questions[qid] = {
            'type': 'noul',
            'instructions': 'Does this skill directly support the action and deliverable requested now? Evaluate independently: several skills may fit. Do not divide probability between skills. Match intended meaning despite typos. Prior context only resolves a short continuation; subject overlap alone is insufficient.',
            'criteria': {
                'true': 'The requested work directly matches this skill capability: ' + base._summary(by_name[name], 150),
                'false': 'The skill shares only keywords or subject matter, addresses a different action or deliverable, or its capability is not requested.'}}
    return questions, keys


def _score(answer):
    if not isinstance(answer, dict) or answer.get('type') != 'noul':
        raise ValueError('malformed_applicability')
    value = answer.get('noul')
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 1):
        raise ValueError('malformed_applicability')
    return float(value)


def choose(prompt: str, context: str = '', entries=None, timeout=None, *,
           transport=None, shortlist=None, question_builder=None,
           context_policy='short') -> GlobalResult:
    """One request; explicit installed-domain shortlist, no retries or cache.

    Returns GlobalResult. stages[-1]['scores'] contains only known skill names
    and numeric fit scores; needs_specialist records the separate action gate.
    """
    started = time.monotonic()
    result = GlobalResult()
    seconds = base.TIMEOUT_S if timeout is None else timeout
    request, rmeta = base._bounded(prompt, base.PROMPT_CHARS)
    previous, cmeta = base._bounded(context, base.CONTEXT_CHARS)
    state = {'request': request}
    if previous and (context_policy == 'always' or
                     len((prompt or '').split()) <= base.CONTEXT_MAX_WORDS):
        state['previous_assistant_message'] = previous
    else:
        cmeta = {**cmeta, 'sent_chars': 0, 'truncated': False}
    result.input = {'request': rmeta, 'context': cmeta}

    def finish(failure=''):
        result.failure = failure
        result.ms = int((time.monotonic() - started) * 1000)
        return result

    if context_policy not in ('short', 'always'):
        return finish('invalid_context_policy')
    if question_builder is not None and not callable(question_builder):
        return finish('invalid_question_builder')
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds) or seconds <= 0):
        return finish('invalid_timeout')
    if base.ROUTE_AT != .8:
        return finish('threshold_mismatch')
    if not request:
        return finish('empty_input')
    entries = base.load_entries() if entries is None else entries
    if not entries:
        return finish('empty_index')
    if not isinstance(shortlist, (list, tuple)) or any(not isinstance(n, str) for n in shortlist):
        return finish('invalid_shortlist')
    allowed = {entry['name'] for entry in global_selector.eligible(entries)
               if entry.get('kind') != 'process'}
    if any(name not in allowed for name in shortlist):
        return finish('invalid_shortlist')
    result.shortlist = list(dict.fromkeys(shortlist))
    if len(result.shortlist) > 254:
        return finish('shortlist_overflow')
    if not result.shortlist:
        return finish()
    key = '' if transport is not None else base._key()
    if transport is None and not key:
        return finish('no_key')
    questions, keys = (question_builder or build_questions)(entries, result.shortlist)
    send = transport or base._post
    result.attempts = 1
    t0 = time.monotonic()
    deadline = t0 + seconds
    data = base._deadline(lambda: send({'model': base.MODEL, 'state': state,
                                      'questions': questions}, key, seconds), seconds)
    answers = data.get('answers') if isinstance(data, dict) else None
    result.tokens = base._usage(data) if isinstance(data, dict) else 0
    stage = {'stage': 'applicability', 'attempt': 1, 'input_tokens': result.tokens,
             'elapsed_ms': int((time.monotonic() - t0) * 1000), 'failure': ''}
    result.stages.append(stage)
    if not isinstance(answers, dict):
        stage['failure'] = 'timeout' if time.monotonic() >= deadline else 'error'
        return finish(stage['failure'])
    try:
        if set(answers) != set(questions):
            raise ValueError('malformed_applicability')
        gate = _score(answers['needs_specialist'])
        scores = {name: _score(answers[qid]) for qid, name in keys.items()}
    except ValueError as error:
        stage['failure'] = str(error)
        return finish(str(error))
    stage['needs_specialist'] = gate
    stage['scores'] = scores
    best = max(scores.values())
    winners = [name for name, value in scores.items() if value == best]
    confidence = min(best, gate)
    stage['confidence'] = confidence
    if gate < .8:
        stage['decision'] = 'gate_below_threshold'
    elif best < .8:
        stage['decision'] = 'fit_below_threshold'
    elif len(winners) != 1:
        stage['decision'] = 'tied_best_fit'
    else:
        stage['decision'] = 'route'
        result.primary = base.Pick(winners[0], confidence, 'route')
    stage['selected_skill'] = result.primary.name
    return finish()
