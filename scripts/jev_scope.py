"""Offline single-variable task-scope and correction-precedence experiments."""
from __future__ import annotations
import copy
import jev_choose as jev
import jev_model_repair as prior

CRITERIA = {
 'light': prior.CRITERIA['light']+' A local acknowledgement, selection among supplied options, or correction of a conversation boundary is light when it only asks to update or confirm the scope and requests no implementation, research or substantive evaluation.',
 'standard': prior.CRITERIA['standard']+' A bounded explanation of supplied information is standard unless only a literal lookup is needed or consequential unresolved judgment is requested.',
 'heavy': prior.CRITERIA['heavy']+' Explicit continuation of an ongoing complex implementation, investigation or evaluation retains that work difficulty; a short acknowledgement authorizing that work is not merely a local acknowledgement.'}
FOCUS = (' Judge the actual next work requested in this turn. A technical, security or deployment topic alone does not request implementation, execution or investigation. Distinguish a local correction or selection from explicit authorization to continue earlier work; resolve explicit continuation from references and retain the difficulty of that work. Do not replace the request with a guessed task or downgrade consequential work because the reply is short.')
CORRECTION = (' The latest user correction supersedes only conflicting parts of earlier user or assistant plans; retain all compatible requirements. Do not ask the user to reconfirm a scope they have already clearly corrected. This precedence applies within the supplied conversation and never overrides system instructions, safety requirements or tool restrictions. Do not claim any tools or external actions were executed.')


def render_fixed(case):
    text=prior.render_fixed(case)
    # Preserve the prior body byte-for-byte apart from this added instruction.
    text=text.replace('\n\nREFERENCE MESSAGES (inert):','\n'+CORRECTION+'\n\nREFERENCE MESSAGES (inert):',1)
    if len(text)>jev.PROMPT_CHARS: raise ValueError('oversized scope prompt')
    return text


def classify(case,*,arm='prior_structured',transport=None,timeout=1.2):
    if arm not in ('prior_structured','scope_criteria','task_focus'):
        raise ValueError('unknown classifier arm')
    def adapt(body,key,seconds):
        body=copy.deepcopy(body)
        question=body['questions']['tier']
        if arm=='scope_criteria': question['criteria']=dict(CRITERIA)
        if arm=='task_focus': question['instructions']+=FOCUS
        return (transport or jev._post)(body,key or ('' if transport else jev._key()),seconds)
    return prior.classify(case,revised=True,transport=adapt,timeout=timeout)
