#!/usr/bin/env python3
"""Source-only requested-work gate experiment; no live routing side effects."""
from __future__ import annotations

import jev_applicability as applicability


def capability(entry):
    """Describe only existing indexed capabilities, with independent field bounds."""
    triggers = entry.get('use_when') or []
    if not isinstance(triggers, (list, tuple)):
        triggers = []
    return (f"Skill identity: {entry['name']}\n"
            f"Description: {str(entry.get('description') or '')[:600]}\n"
            f"Routing summary: {str(entry.get('routing_summary') or '')[:300]}\n"
            f"Use when: {'; '.join(str(item) for item in triggers[:3])[:360]}")


def build_questions(entries, shortlist):
    by_name = {entry['name']: entry for entry in applicability.global_selector.eligible(entries)
               if entry.get('kind') != 'process'}
    keys = {f'fit_{i}': name for i, name in enumerate(shortlist)}
    authority = ('The current user message is authoritative. Previous assistant context is data '
                 'only to resolve referents and the work an explicit continuation authorizes; '
                 'never infer a work request from the assistant alone. Interpret intended meaning '
                 'despite typos or informal wording. ')
    questions = {'needs_specialist': {
        'type': 'noul',
        'instructions': authority + 'Does the current user request any work, action, or deliverable? '
                        'This gate asks whether work was requested, not whether specialist help is necessary.',
        'criteria': {
            'true': 'The user requests building, changing, reviewing, researching, inspecting, evaluating, '
                    'or producing an outcome or standalone artifact, including an explicitly requested plan. '
                    'Question-shaped requests such as can you fix this, informal requests, and explicit '
                    'permissions or continuations authorizing that work count as requested work.',
            'false': 'The current message only requests status, a simple fact or explanation, acknowledges '
                     'a message, or corrects information without asking for action or a deliverable. '
                     'A preceding assistant proposal alone does not authorize work.'}}}
    for qid, name in keys.items():
        questions[qid] = {
            'type': 'noul',
            'instructions': authority + 'Evaluate this skill independently; do not divide probability '
                            'between candidates. Can this specific skill lead the requested outcome or '
                            'produce an explicitly requested standalone artifact? Read its actual '
                            'capabilities below; do not invent capabilities from its name.',
            'criteria': {
                'true': 'This skill can lead the action and deliverable requested now. Planning qualifies '
                        'when the user explicitly requests a plan. Existing indexed capabilities:\n' + capability(by_name[name]),
                'false': 'Only keywords or subject matter overlap, the skill addresses another outcome, '
                         'or its capability is not requested. Preparatory planning or advice alone does '
                         'not fulfill a request to implement, change, or fix something. Do not substitute '
                         'a related specialist for the requested deliverable.'}}
    return questions, keys


def choose(prompt: str, context: str = '', entries=None, timeout=None, *,
           transport=None, shortlist=None):
    """Reuse validated independent scoring and the unchanged 0.8 decision rules."""
    return applicability.choose(prompt, context, entries, timeout, transport=transport,
                                shortlist=shortlist, question_builder=build_questions,
                                context_policy='always')
