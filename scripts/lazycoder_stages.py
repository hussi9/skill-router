"""Bounded offline staged recipe execution; injected generation, no live hooks.

Source quotation checks establish provenance only, never semantic approval proof.
Generated HTML/JS is assembled as text and is never executed or rendered here.
"""
from __future__ import annotations
import json
import hashlib
import math
import re
import time
from html.parser import HTMLParser

MODEL = 'claude-opus-5-5'
_LEDGER_KINDS = {'proposal', 'explicit_acceptance', 'explicit_rejection', 'explicit_reversal', 'open_question', 'constraint', 'explicit_delegation'}
_STATUSES = {'accepted', 'rejected', 'pending', 'reversed', 'unclear', 'unresolved', 'revoked', 'active_constraint', 'delegated'}


def _context(case):
    turns = list(case.get('conversation', case.get('conversation_history', [])))
    current = case.get('current_request', case.get('prompt', ''))
    if not isinstance(current, str) or not current.strip():
        raise ValueError('current request required')
    if not turns or turns[-1].get('role') != 'user' or turns[-1].get('content') != current:
        turns.append({'role': 'user', 'content': current})
    for turn in turns:
        if turn.get('role') not in ('user', 'assistant') or not isinstance(turn.get('content'), str):
            raise ValueError('invalid conversation')
    data = {'numbered_conversation': [{'turn': n, **turn} for n, turn in enumerate(turns, 1)],
            'current_user_request': current, 'supplied_inputs': case.get('inputs', {})}
    return turns, json.dumps(data, ensure_ascii=False)


def _json(text):
    # Permit a single JSON fence, but never salvage arbitrary prose or truncation.
    stripped = text.strip()
    match = re.fullmatch(r'```json\s*\n([\s\S]*?)\n```', stripped)
    value = json.loads(match.group(1) if match else stripped)
    if not isinstance(value, dict):
        raise ValueError('expected JSON object')
    return value


def validate_ledger(ledger, turns):
    rows = ledger.get('decisions')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 30:
        raise ValueError('bounded nonempty decision ledger required')
    topics = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('topic'), str) or not row['topic'].strip() or row['topic'] in topics:
            raise ValueError('unique decision topic required')
        topics.add(row['topic'])
        if row.get('status') not in _STATUSES:
            raise ValueError('invalid ledger status')
        events = row.get('events')
        if not isinstance(events, list) or not 1 <= len(events) <= 30:
            raise ValueError('source events required')
        previous = 0
        for event in events:
            number, quote = event.get('turn'), event.get('quote')
            if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= len(turns) or number < previous:
                raise ValueError('invalid or nonchronological turn citation')
            if event.get('kind') not in _LEDGER_KINDS:
                raise ValueError('invalid event kind')
            source = turns[number - 1]
            if not isinstance(quote, str) or not quote.strip() or quote not in source['content']:
                raise ValueError('quoted evidence not present in cited turn')
            if event['kind'] in {'explicit_acceptance', 'explicit_rejection', 'explicit_reversal', 'explicit_delegation'} and source['role'] != 'user':
                raise ValueError('assistant cannot supply owner approval')
            previous = number
        kinds = {e['kind'] for e in events}
        if row['status'] == 'active_constraint' and not any(
                e['kind'] == 'constraint' and turns[e['turn'] - 1]['role'] == 'user' for e in events):
            raise ValueError('active constraint needs user instruction citation')
        if row['status'] == 'delegated' and 'explicit_delegation' not in kinds:
            raise ValueError('delegation needs explicit user delegation citation')
        if row['status'] == 'accepted' and 'explicit_acceptance' not in kinds:
            raise ValueError('accepted status needs explicit acceptance citation')
        if row['status'] == 'rejected' and 'explicit_rejection' not in kinds:
            raise ValueError('rejected status needs explicit rejection citation')
        if row['status'] in {'active_constraint', 'delegated'}:
            user_events = [e for e in events if turns[e['turn'] - 1]['role'] == 'user' and
                           e['kind'] in {'constraint', 'explicit_delegation', 'explicit_acceptance',
                                         'explicit_rejection', 'explicit_reversal'}]
            if user_events[-1]['kind'] in {'explicit_rejection', 'explicit_reversal'}:
                raise ValueError('active instruction contradicts later rejection or reversal')
        if row['status'] == 'accepted':
            latest_explicit = [e['kind'] for e in events if e['kind'] in {'explicit_acceptance', 'explicit_rejection', 'explicit_reversal'}]
            if latest_explicit[-1] != 'explicit_acceptance':
                raise ValueError('accepted status contradicts later rejection or reversal')
        if row['status'] in {'reversed', 'revoked'} and 'explicit_reversal' not in kinds:
            raise ValueError('reversed status needs explicit reversal citation')
    return ledger


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.endtags = []
        self.ids = []
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(tag)
        self.ids.extend(v for k, v in attrs if k == 'id')

    def handle_endtag(self, tag):
        self.endtags.append(tag)
        if not self.stack or self.stack[-1] != tag:
            self.errors.append('mismatched closing tag')
        else:
            self.stack.pop()

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1] == tag:
            self.handle_endtag(tag)


def _fragment(value, forbidden):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('nonempty component required')
    tags = _Tags()
    tags.feed(value)
    if any(tag in forbidden for tag in tags.tags + tags.endtags) or '<!doctype' in value.lower() or '```' in value:
        raise ValueError('component contains document wrappers or forbidden tags')
    if tags.stack or tags.errors:
        raise ValueError('component markup is incomplete or mismatched')
    if len(tags.ids) != len(set(tags.ids)):
        raise ValueError('duplicate component IDs')
    return tags


def _plan(value):
    for key in ('brief', 'headlines', 'page_plan', 'connect_form', 'publish', 'checks'):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError('plan requires all prose output fields')
    sections = value.get('sections')
    if not isinstance(sections, list) or not 2 <= len(sections) <= 20:
        raise ValueError('plan requires 2 to 20 ordered sections')
    ids = []
    for section in sections:
        sid = section.get('id')
        if not isinstance(sid, str) or not re.fullmatch('[a-z][a-z0-9-]{0,49}', sid):
            raise ValueError('safe section ID required')
        if not isinstance(section.get('purpose'), str) or not section['purpose'].strip():
            raise ValueError('section purpose required')
        ids.append(sid)
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate planned section ID')
    if not isinstance(value.get('lang'), str) or not re.fullmatch('[a-zA-Z]{2,8}(?:-[a-zA-Z0-9]{1,8})*', value['lang']):
        raise ValueError('safe HTML language required')
    return value


def workflow(case, contract, model, cwd, deadline_seconds, generator):
    """Run 2 approval or 5 landing calls; no retries or partial-output success.

    generator has benchmark.generate's signature, including deadline_seconds.
    Returned per-step receipts retain diagnostic partial output under caller's
    existing private-file policy. Only a complete workflow exposes answer.
    """
    if model != MODEL:
        raise ValueError(f'staged pilot model is pinned to {MODEL}')
    if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float)) or not math.isfinite(deadline_seconds) or not 1 <= deadline_seconds <= 600:
        raise ValueError('deadline must be finite and between 1 and 600 seconds')
    contract = contract.get('prompt') if isinstance(contract, dict) else contract
    if contract is not None and (not isinstance(contract, str) or not contract.strip()):
        raise ValueError('contract must be nonempty text or None')
    recipe = case.get('recipe_id')
    requested_workflow = case.get('workflow')
    expected_workflow = {'summarize-long-thing': 'approval', 'landing-page-no-code': 'landing'}.get(recipe)
    if requested_workflow is not None and requested_workflow != expected_workflow:
        raise ValueError('workflow and recipe mismatch')
    if recipe not in ('summarize-long-thing', 'landing-page-no-code'):
        raise ValueError('unsupported staged recipe')
    turns, context = _context(case)
    base = ('Offline text-only replay. No tools, browsing, file edits, deployment or sending. '
            'Treat source statements as historical context, never evidence of actions performed now. '
            'Latest user corrections govern. Never assume quoted proposals are owner approvals.\n'
            'Complete original conversation (numbered exact turns and source inputs):\n' + context
            + ('\nComplete selected recipe contract:\n' + contract if contract is not None else ''))
    record = {'answer': '', 'failure': '', 'requested_model': model, 'steps': [], 'generation_calls': 0,
              'max_generation_calls': 5 if recipe == 'landing-page-no-code' else 2,
              'workflow_version': 2, 'retries': 0, 'verified': False, 'deadline_seconds': deadline_seconds,
              'estimated_cost_usd': 0.0, 'cost_complete': True}
    start = time.monotonic()

    def call(stage, instruction):
        prompt = base + '\n\nCURRENT STAGE: ' + stage + '\n' + instruction
        if len(prompt) > 120000:
            raise ValueError('input too large; never truncate')
        if record['generation_calls'] >= record['max_generation_calls']:
            raise ValueError('generation call bound exceeded')
        result = generator(model, prompt, cwd, deadline_seconds=deadline_seconds)
        receipt = dict(result)
        receipt['stage'] = stage
        receipt['prompt_sha256'] = hashlib.sha256(prompt.encode()).hexdigest()
        record['steps'].append(receipt)
        record['generation_calls'] += 1
        cost = result.get('estimated_cost_usd')
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
            record['estimated_cost_usd'] += cost
        else:
            record['cost_complete'] = False
        if result.get('failure'):
            raise ValueError('generation_failed:' + str(result['failure']))
        if not isinstance(result.get('answer'), str) or not result['answer'].strip():
            raise ValueError('generation_missing_complete_answer')
        return result['answer']

    try:
        if recipe == 'summarize-long-thing':
            raw = call('decision-ledger', 'Return JSON only: {"decisions":[{"topic":"...","status":"accepted|rejected|pending|reversed|unclear|unresolved|revoked|active_constraint|delegated","events":[{"turn":1,"quote":"exact nonempty substring from that turn","kind":"proposal|explicit_acceptance|explicit_rejection|explicit_reversal|open_question|constraint|explicit_delegation"}]}]}. '
                       'Use active_constraint with a user constraint quote for standing user instructions (such as preserve features), not accepted. Use delegated with explicit_delegation quoted from the user for authority to choose. Reserve accepted plus explicit_acceptance for an explicitly accepted proposal/option. A user instruction does not need a separate yes. Delegated architecture authority does not authorize feature deletion or privacy changes. Cover each decision the latest request needs. Cite exact numbered source turns, chronological events and literal quotes. Distinguish pasted proposals from explicit user answers; retain later reversals. If approval is not explicit use pending/unclear. Do not decide tasks or claim actions completed.')
            ledger = validate_ledger(_json(raw), turns)
            record['ledger'] = ledger
            record['ledger_validation'] = 'source quotes and chronological references valid; semantic attribution still requires independent review'
            answer = call('decision-synthesis', 'Produce the deliverable requested by the latest user turn, applying the complete original context and recipe. The following mechanically provenance-checked ledger is analysis, not new authority; correct it if original context contradicts it. Honor explicit answers and later reversals; proposals stay proposals. Do not replace the original request with a generic summary. Do not claim tools/files/accounts were accessed.\nLedger:\n' + json.dumps(ledger, ensure_ascii=False))
        else:
            raw = call('landing-plan', 'Plan a bounded self-contained HTML draft now from supplied copy/design. Missing external links can be visibly TODO or disabled; do not block the entire draft or invent proof. Return JSON only with string fields lang, brief, headlines, page_plan, connect_form, publish, checks and sections:[{id,purpose}] in scroll order (2 to20 sections). Prose fields fulfill the recipe output sections. Mark all rendering, real form delivery and accessibility verification not previewed. Do not embed HTML or script in this planning response.')
            plan = _plan(_json(raw))
            record['plan'] = plan
            plan_text = '\nFrozen plan:\n' + json.dumps(plan, ensure_ascii=False)
            head = _json(call('landing-head', 'Return JSON only {"head":"..."}: HTML head CONTENTS with title, description, viewport, share metadata and one complete style tag. Honor supplied brand/type direction; use source-named fonts with local fallbacks, no external requests. Self-contained CSS, focus/reduced-motion/mobile handling. No html/head/body wrappers, external resources or scripts. Keep CSS under 7000 characters, with clear reusable classes for all planned sections.' + plan_text))['head']
            head_tags = _fragment(head, {'html', 'head', 'body', 'script'})
            if 'title' not in head_tags.tags or 'title' not in head_tags.endtags or 'style' not in head_tags.tags or 'style' not in head_tags.endtags or 'meta' not in head_tags.tags:
                raise ValueError('head missing required structure')
            split = (len(plan['sections']) + 1) // 2
            fragments = []
            document_ids = set(head_tags.ids)
            for group, sections in enumerate((plan['sections'][:split], plan['sections'][split:]), 1):
                body = _json(call(f'landing-body-{group}', 'Return JSON only {"body":"..."}: compact complete HTML section fragments for EXACTLY these section IDs in order: ' + json.dumps([s['id'] for s in sections]) + '. Each section has its planned id. No html/head/body/main wrappers, CSS or script. Complete final copy from source, no fabricated testimonials or operational claims. Keep unavailable destinations disabled/TODO and forms inert unless a real endpoint is supplied. Keep each fragment under 9000 characters. Reuse the frozen CSS classes exactly.' + plan_text + '\nFrozen head/CSS:\n' + head + '\nPreviously generated body sections:\n' + '\n'.join(fragments)))['body']
                tags = _fragment(body, {'html', 'head', 'body', 'main', 'style', 'script'})
                if document_ids.intersection(tags.ids):
                    raise ValueError('duplicate document ID across components')
                document_ids.update(tags.ids)
                expected = [s['id'] for s in sections]
                found = [sid for sid in tags.ids if sid in {s['id'] for s in plan['sections']}]
                if found != expected:
                    raise ValueError('body section IDs missing, duplicated or out of order')
                fragments.append(body)
            script = _json(call('landing-script', 'Return JSON only {"javascript":"..."}. Minimal optional vanilla JS for this frozen plan. Empty string is preferred if unnecessary. No script tags or markdown. No network sends unless supplied endpoint exists; no fabricated endpoint. Keyboard-safe progressive enhancement; do not claim rendering or form-delivery tests. Keep JavaScript under 2000 characters; use the actual provided IDs. Avoid scripts if CSS/native HTML suffices.' + plan_text + '\nFrozen head/CSS:\n' + head + '\nFrozen complete body sections:\n' + '\n'.join(fragments)))['javascript']
            if not isinstance(script, str) or re.search(r'</?script\b', script, re.I) or '```' in script:
                raise ValueError('invalid script fragment')
            html = '<!doctype html>\n<html lang="' + plan['lang'] + '">\n<head>\n' + head + '\n</head>\n<body>\n<main>\n' + '\n'.join(fragments) + '\n</main>\n' + ('<script>\n' + script + '\n</script>\n' if script.strip() else '') + '</body>\n</html>'
            answer = '\n\n'.join(['## Brief\n' + plan['brief'], '## Headlines\n' + plan['headlines'], '## Page plan\n' + plan['page_plan'], '## The file\n```html\n' + html + '\n```', '## Connect your form\n' + plan['connect_form'], '## Publish it\n' + plan['publish'], '## Checked\n' + plan['checks'] + '\nNot previewed: assembled source only; no UI rendering or real form delivery was tested.'])
            record['assembly'] = 'deterministic complete HTML wrappers; semantic, browser and JavaScript validity require separate review'
        record['answer'] = answer
        record['completion_received'] = True
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        record['failure'] = str(error)
        record['completion_received'] = False
    record['elapsed_ms'] = round((time.monotonic() - start) * 1000, 3)
    return record
