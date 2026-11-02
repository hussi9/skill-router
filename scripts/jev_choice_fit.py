#!/usr/bin/env python3
"""Offline v3 response-format ablation; choice probabilities are not calibrated nouls."""
from __future__ import annotations

import math
import jev_source_v3 as source


def build_questions(entries, shortlist, capabilities):
    questions, keys = source.build_questions(entries, shortlist, capabilities)
    for qid in keys:
        question = questions[qid]
        question['type'] = 'choice'
        question['criteria'] = {'supported': question['criteria']['true'],
                                'unsupported': question['criteria']['false']}
    return questions, keys


def _number(value):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or not 0 <= value <= 1):
        raise ValueError('malformed_choice_fit')
    return float(value)


def _score(answer):
    if not isinstance(answer, dict) or answer.get('type') != 'choice':
        raise ValueError('malformed_choice_fit')
    probabilities = answer.get('probabilities')
    if not isinstance(probabilities, dict) or set(probabilities) != {'supported', 'unsupported'}:
        raise ValueError('malformed_choice_fit')
    supported, unsupported = (_number(probabilities[key]) for key in ('supported', 'unsupported'))
    confidence = _number(answer.get('confidence'))
    choice = answer.get('choice')
    if (not math.isclose(supported + unsupported, 1, abs_tol=1e-6, rel_tol=0) or
            not isinstance(choice, str) or choice not in probabilities or probabilities[choice] != max(supported, unsupported)):
        raise ValueError('malformed_choice_fit')
    if supported == unsupported:
        return min(supported, .5)
    return min(supported, confidence) if choice == 'supported' else min(supported, .5)


def choose(prompt, context='', entries=None, timeout=None, *, transport=None,
           shortlist=None, capabilities=None, process_candidate=None):
    # Reuse all v3 validation, bounded context, pool construction and scoring.
    # With no injected transport the existing provider key discovery still runs.
    key = '' if transport is not None else source.applicability.base._key()
    if transport is None and not key:
        result = source.applicability.GlobalResult()
        result.failure = 'no_key'
        return result
    send = transport or source.applicability.base._post

    def adapt(body, _key, seconds):
        questions = {}
        fit_keys = []
        for qid, question in body['questions'].items():
            if qid.startswith('fit_'):
                fit_keys.append(qid)
                questions[qid] = {**question, 'type': 'choice', 'criteria': {
                    'supported': question['criteria']['true'],
                    'unsupported': question['criteria']['false']}}
            else:
                questions[qid] = question
        data = send({**body, 'questions': questions}, key, seconds)
        if not isinstance(data, dict) or not isinstance(data.get('answers'), dict):
            return data
        answers = dict(data['answers'])
        try:
            if set(answers) != set(questions):
                raise ValueError('malformed_choice_fit')
            for qid in fit_keys:
                answers[qid] = {'type': 'noul', 'noul': _score(answers[qid])}
        except ValueError:
            # The shared core rejects this sentinel and preserves provider usage.
            answers = {'needs_specialist': {'type': 'noul', 'noul': None}}
        return {**data, 'answers': answers}

    return source.choose(prompt, context, entries, timeout, transport=adapt,
        shortlist=shortlist, capabilities=capabilities, process_candidate=process_candidate)
