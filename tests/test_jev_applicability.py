import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import jev_applicability as a


class ApplicabilityTests(unittest.TestCase):
    entries = [{'name': 'one', 'type': 'skill', 'kind': 'domain', 'description': 'Inspect code'},
               {'name': 'two', 'type': 'skill', 'kind': 'domain', 'description': 'Build code'}]

    def run_choice(self, scores=(.9, .89), gate=.99, **kwargs):
        answers = {'needs_specialist': {'type': 'noul', 'noul': gate}}
        answers.update({f'fit_{i}': {'type': 'noul', 'noul': score} for i, score in enumerate(scores)})
        self.calls = []
        def send(body, key, timeout):
            self.calls.append(body)
            return {'answers': answers, 'usage': {'input_tokens': 42}}
        return a.choose(kwargs.pop('prompt', 'inspect this'), entries=self.entries,
                        shortlist=['one', 'two'], transport=send, **kwargs)

    def test_independent_competition_and_current_summary(self):
        out = self.run_choice()
        self.assertEqual(out.primary.name, 'one')
        self.assertEqual(out.primary.confidence, .9)
        self.assertEqual(out.attempts, 1)
        self.assertEqual(out.tokens, 42)
        self.assertEqual(out.stages[0]['decision'], 'route')
        self.assertEqual(len(self.calls[0]['questions']), 3)
        self.assertIn(a.base._summary(self.entries[0], 150), self.calls[0]['questions']['fit_0']['criteria']['true'])

    def test_gate_and_threshold(self):
        for scores, gate, expected in [((.99, .9), .2, None), ((.799, .7), .99, None),
                                       ((.8, .7), .8, 'one'), ((.9, .9), .99, None)]:
            with self.subTest(scores=scores, gate=gate):
                self.assertEqual(self.run_choice(scores, gate).primary.name, expected)

    def test_decision_reasons_are_known_enums(self):
        for scores, gate, reason in [((.99, .9), .2, 'gate_below_threshold'),
                                     ((.799, .7), .99, 'fit_below_threshold'),
                                     ((.9, .9), .99, 'tied_best_fit')]:
            self.assertEqual(self.run_choice(scores, gate).stages[0]['decision'], reason)

    def test_context_policy_and_bounds(self):
        self.run_choice(context='x' * 10000)
        self.assertEqual(len(self.calls[0]['state']['previous_assistant_message']), 4000)
        out = self.run_choice(prompt='word ' * 10000, context='private context')
        self.assertNotIn('previous_assistant_message', self.calls[0]['state'])
        self.assertLessEqual(len(self.calls[0]['state']['request']), 16000)
        self.assertNotIn('private context', str(out.to_dict()))
        self.assertEqual(out.input['context']['sent_chars'], 0)

    def test_invalid_numeric_answers(self):
        for value in (True, False, '0.99', None, float('nan'), float('inf'), -1, 1.1):
            with self.subTest(value=value):
                out = self.run_choice((value, .8))
                self.assertIsNone(out.primary.name)
                self.assertEqual(out.failure, 'malformed_applicability')
                self.assertNotIn('scores', out.stages[0])

    def test_missing_unknown_wrong_type_fail_closed(self):
        valid = {'needs_specialist': {'type': 'noul', 'noul': .99},
                 'fit_0': {'type': 'noul', 'noul': .99}}
        for answers in ({}, valid, {**valid, 'fit_1': {'type': 'choice', 'noul': .9}},
                        {**valid, 'fit_1': {'type': 'noul', 'noul': .9}, 'unknown': 'private'}):
            out = a.choose('inspect', entries=self.entries, shortlist=['one', 'two'],
                           transport=lambda *_: {'answers': answers})
            self.assertEqual(out.failure, 'malformed_applicability')
            self.assertNotIn('private', str(out.to_dict()))

    def test_timeout_no_retry(self):
        def slow(*_):
            time.sleep(.05)
        out = a.choose('inspect', entries=self.entries, shortlist=['one'], transport=slow, timeout=.001)
        self.assertEqual(out.failure, 'timeout')
        self.assertEqual(out.attempts, 1)

    def test_input_guards_prevent_calls(self):
        def forbidden(*_):
            self.fail('must not call')
        for timeout in (True, 0, -1, float('nan')):
            out = a.choose('inspect', entries=self.entries, shortlist=['one'], transport=forbidden, timeout=timeout)
            self.assertEqual(out.failure, 'invalid_timeout')
        with patch.object(a.base, 'ROUTE_AT', .79):
            out = a.choose('inspect', entries=self.entries, shortlist=['one'], transport=forbidden)
            self.assertEqual(out.failure, 'threshold_mismatch')
        for shortlist in (None, ['unknown'], [{}]):
            out = a.choose('inspect', entries=self.entries, shortlist=shortlist, transport=forbidden)
            self.assertEqual(out.failure, 'invalid_shortlist')
        self.assertEqual(a.choose('inspect', entries=self.entries, shortlist=[], transport=forbidden).attempts, 0)
        entries = [{'name': str(i)} for i in range(255)]
        self.assertEqual(a.choose('inspect', entries=entries, shortlist=[str(i) for i in range(255)], transport=forbidden).failure, 'shortlist_overflow')


if __name__ == '__main__':
    unittest.main()
