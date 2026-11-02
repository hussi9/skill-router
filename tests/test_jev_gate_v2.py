import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import jev_applicability as old
import jev_gate_v2 as v2


class GateV2Tests(unittest.TestCase):
    entries = [dict(name='builder', type='skill', kind='domain', description='Implement requested changes',
                    routing_summary='Build features', use_when=['implementation']),
               dict(name='planner', type='skill', kind='domain', description='Create implementation plans')]

    def choose(self, selector=v2, gate=.9, scores=(.9, .7), **kwargs):
        self.sent = []
        def send(body, *_):
            self.sent.append(body)
            return {'answers': {'needs_specialist': {'type': 'noul', 'noul': gate},
                                **{f'fit_{i}': {'type': 'noul', 'noul': score}
                                   for i, score in enumerate(scores)}}}
        return selector.choose('please make these changes ' * 8, context='x' * 9000,
                               entries=self.entries, shortlist=['builder', 'planner'],
                               transport=send, **kwargs)

    def test_default_selector_preserves_questions_and_short_context(self):
        out = self.choose(old)
        body = self.sent[0]
        self.assertEqual(body['questions'], old.build_questions(self.entries, ['builder', 'planner'])[0])
        self.assertNotIn('previous_assistant_message', body['state'])
        self.assertEqual(out.input['context']['sent_chars'], 0)
        self.assertEqual(out.primary.name, 'builder')

    def test_v2_always_sends_bounded_context_without_logging_it(self):
        out = self.choose()
        self.assertEqual(len(self.sent[0]['state']['previous_assistant_message']), 4000)
        self.assertEqual(out.input['context']['sent_chars'], 4000)
        self.assertNotIn('x' * 100, str(out.to_dict()))

    def test_question_contract_keeps_independent_scores_and_current_authority(self):
        questions, keys = v2.build_questions(self.entries, ['builder', 'planner'])
        self.assertEqual(keys, {'fit_0': 'builder', 'fit_1': 'planner'})
        self.assertTrue(all(q['type'] == 'noul' for q in questions.values()))
        gate = str(questions['needs_specialist'])
        for phrase in ('not whether specialist help is necessary', 'Question-shaped requests',
                       'explicit permissions', 'assistant proposal alone', 'simple fact or explanation'):
            self.assertIn(phrase, gate)
        fit = str(questions['fit_0'])
        for phrase in ('current user message is authoritative', 'do not divide probability',
                       'Skill identity: builder', 'Implement requested changes',
                       'do not invent capabilities', 'explicitly requests a plan',
                       'does not fulfill a request to implement'):
            self.assertIn(phrase, fit)

    def test_capability_bounds_and_source_only(self):
        text = v2.capability(dict(name='one', description='a' * 700,
                                  routing_summary='b' * 400, use_when=['c' * 200] * 4))
        self.assertIn('a' * 600, text)
        self.assertNotIn('a' * 601, text)
        self.assertIn('b' * 300, text)
        self.assertNotIn('b' * 301, text)
        self.assertLessEqual(len(text.split('Use when: ')[1]), 360)
        self.assertEqual(v2.capability(dict(name='one', use_when='bad')).split('Use when: ')[1], '')

    def test_gate_threshold_fit_threshold_and_tie_unchanged(self):
        for gate, scores, decision, name in [(.79, (.99, .7), 'gate_below_threshold', None),
                                           (.9, (.799, .7), 'fit_below_threshold', None),
                                           (.8, (.8, .7), 'route', 'builder'),
                                           (.99, (.9, .9), 'tied_best_fit', None)]:
            with self.subTest(gate=gate, scores=scores):
                out = self.choose(gate=gate, scores=scores)
                self.assertEqual(out.primary.name, name)
                self.assertEqual(out.stages[0]['decision'], decision)
                if name:
                    self.assertEqual(out.primary.confidence, min(gate, max(scores)))

    def test_malformed_scores_fail_closed(self):
        for bad in (True, None, float('nan'), float('inf'), '0.99', -1, 1.1):
            with self.subTest(bad=bad):
                out = self.choose(scores=(bad, .7))
                self.assertEqual(out.failure, 'malformed_applicability')
                self.assertIsNone(out.primary.name)

    def test_invalid_policy_builder_and_threshold_prevent_transport(self):
        def forbidden(*_):
            self.fail('unexpected transport')
        for kwargs, failure in [({'context_policy': 'unknown'}, 'invalid_context_policy'),
                                ({'context_policy': []}, 'invalid_context_policy'),
                                ({'question_builder': False}, 'invalid_question_builder')]:
            out = old.choose('build', entries=self.entries, shortlist=['builder'], transport=forbidden, **kwargs)
            self.assertEqual(out.failure, failure)
        with patch.object(old.base, 'ROUTE_AT', .79):
            out = v2.choose('build', entries=self.entries, shortlist=['builder'], transport=forbidden)
            self.assertEqual(out.failure, 'threshold_mismatch')


if __name__ == '__main__':
    unittest.main()
