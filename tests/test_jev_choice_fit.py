import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import jev_choice_fit as choice
import jev_source_v3 as source


class ChoiceFitTests(unittest.TestCase):
    entries = [dict(name='builder', type='skill', kind='domain', description='Build'),
               dict(name='planner', type='skill', kind='process', description='Plan')]
    snapshot = {'version': 1, 'skills': {name: dict(name=name, status='index_fallback',
        reason='missing_source', evidence='') for name in ('builder', 'planner')}}

    def answer(self, score=.9, confidence=.9):
        return {'type': 'choice', 'choice': 'supported' if score >= .5 else 'unsupported',
                'confidence': confidence, 'probabilities': {'supported': score, 'unsupported': 1-score}}

    def run_choice(self, score=.9, gate=.9, confidence=.9, mutate=None):
        self.sent = []
        def send(body, *_):
            self.sent.append(body)
            answers = {'needs_specialist': {'type': 'noul', 'noul': gate},
                       'fit_0': self.answer(score, confidence), 'fit_1': self.answer(.2)}
            if mutate:
                mutate(answers)
            return {'answers': answers, 'usage': {'input_tokens': 31}}
        return choice.choose('please build this ' * 20, 'x' * 5000, self.entries,
            transport=send, shortlist=['builder'], capabilities=self.snapshot, process_candidate='planner')

    def test_questions_only_change_fit_type_and_criteria_keys(self):
        original, keys = source.build_questions(self.entries, ['builder'], self.snapshot)
        changed, changed_keys = choice.build_questions(self.entries, ['builder'], self.snapshot)
        self.assertEqual(keys, changed_keys)
        self.assertEqual(original['needs_specialist'], changed['needs_specialist'])
        for qid in keys:
            self.assertEqual(original[qid]['instructions'], changed[qid]['instructions'])
            self.assertEqual(original[qid]['criteria']['true'], changed[qid]['criteria']['supported'])
            self.assertEqual(original[qid]['criteria']['false'], changed[qid]['criteria']['unsupported'])
            self.assertEqual(changed[qid]['type'], 'choice')

    def test_boundaries_context_pool_and_usage(self):
        for score, confidence, expected in [(.79,.99,None),(.8,.8,'builder'),(.9,.79,None),(.5,1,None),(.1,.99,None)]:
            with self.subTest(score=score, confidence=confidence):
                result = self.run_choice(score, confidence=confidence)
                self.assertEqual(result.primary.name, expected)
                self.assertEqual(result.attempts, 1)
                self.assertEqual(result.tokens, 31)
                self.assertEqual(len(self.sent), 1)
                self.assertEqual(result.shortlist, ['builder', 'planner'])
                self.assertEqual(len(self.sent[0]['state']['previous_assistant_message']), 4000)
                self.assertEqual(self.sent[0]['questions']['needs_specialist']['type'], 'noul')
                self.assertEqual(result.stages[0]['candidate_types']['planner'], 'process')

    def test_gate_still_blocks(self):
        result = self.run_choice(.99, gate=.79, confidence=.99)
        self.assertIsNone(result.primary.name)
        self.assertEqual(result.stages[0]['decision'], 'gate_below_threshold')

    def test_malformed_choice_fails_closed(self):
        mutations = [lambda a: a['fit_0'].pop('probabilities'),
            lambda a: a['fit_0'].update(type='noul'),
            lambda a: a['fit_0'].update(choice='unsupported'),
            lambda a: a['fit_0'].update(choice=[]),
            lambda a: a['fit_0']['probabilities'].update(extra=0),
            lambda a: a['fit_0']['probabilities'].update(supported=.8),
            lambda a: a.update(unexpected=self.answer()),
            lambda a: a.pop('fit_1')]
        for bad in (True, None, float('nan'), float('inf'), '0.9', 2):
            mutations.append(lambda a, bad=bad: a['fit_0'].update(confidence=bad))
            mutations.append(lambda a, bad=bad: a['fit_0']['probabilities'].update(supported=bad))
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                result = self.run_choice(mutate=mutate)
                self.assertEqual(result.failure, 'malformed_applicability')
                self.assertIsNone(result.primary.name)
                self.assertEqual(result.tokens, 31)
                self.assertEqual(result.attempts, 1)

    def test_process_can_win_and_ties_abstain(self):
        def send(body, *_):
            return {'answers': {'needs_specialist': {'type':'noul','noul':.9},
                'fit_0': self.answer(.8), 'fit_1':self.answer(.9)}}
        result = choice.choose('plan', entries=self.entries, transport=send,
            shortlist=['builder'], capabilities=self.snapshot, process_candidate='planner')
        self.assertEqual(result.primary.name, 'planner')
        self.assertEqual(result.process.name, 'planner')
        self.assertEqual(choice._score(self.answer(.5, 1)), .5)
        rounded = self.answer(.5, 1)
        rounded['probabilities'] = {'supported': .5000001, 'unsupported': .5000001}
        self.assertEqual(choice._score(rounded), .5)
        rounded['choice'] = 'unsupported'
        self.assertEqual(choice._score(rounded), .5)
