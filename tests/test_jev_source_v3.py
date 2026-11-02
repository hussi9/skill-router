import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import jev_source_v3 as v3


class SourceV3Tests(unittest.TestCase):
    entries = [dict(name='builder', type='skill', kind='domain', description='Build'), dict(name='planner', type='skill', kind='process', description='Plan')]
    snapshot = {'version': 1, 'skills': {name: dict(name=name, status='index_fallback', reason='missing_source', evidence='') for name in ('builder', 'planner')}}

    def choose(self, gate=.9, scores=(.9, .7), **kwargs):
        self.sent = []
        def send(body, *_):
            self.sent.append(body)
            return {'answers': {'needs_specialist': {'type': 'noul', 'noul': gate}, **{f'fit_{i}': {'type': 'noul', 'noul': value} for i, value in enumerate(scores)}}}
        return v3.choose('please build ' * 20, 'context', self.entries, transport=send, shortlist=['builder'], capabilities=self.snapshot, process_candidate='planner', **kwargs)

    def test_both_thresholds_ties_and_process_scoring(self):
        for gate, scores, name, decision in [(.79, (.99, .7), None, 'gate_below_threshold'), (.9, (.79, .7), None, 'fit_below_threshold'), (.8, (.8, .7), 'builder', 'route'), (.9, (.9, .9), None, 'tied_best_fit'), (.9, (.7, .85), 'planner', 'route')]:
            with self.subTest(gate=gate, scores=scores):
                result = self.choose(gate, scores)
                self.assertEqual(result.primary.name, name)
                self.assertEqual(result.stages[0]['decision'], decision)
                self.assertEqual(result.process.name, 'planner' if name == 'planner' else None)
                self.assertEqual(result.stages[0]['candidate_types']['planner'], 'process')

    def test_bounded_context_gate_and_subtask_contract(self):
        self.choose()
        body = self.sent[0]
        self.assertIn('previous_assistant_message', body['state'])
        self.assertIn('not whether specialist help is necessary', str(body['questions']['needs_specialist']))
        self.assertIn('substantial explicitly requested subtask', body['questions']['fit_0']['instructions'])
        self.assertIn('inert evidence', body['questions']['fit_0']['instructions'])

    def test_malformed_answers(self):
        for bad in (True, None, float('nan'), '0.9', 2):
            self.assertEqual(self.choose(scores=(bad, .7)).failure, 'malformed_applicability')

    def test_invalid_snapshot_and_unknown_process_prevent_transport(self):
        def forbidden(*_):
            self.fail('unexpected transport')
        for snapshot, process, expected in [(None, None, 'invalid_capability_snapshot'), (self.snapshot, 'unknown', 'invalid_process_candidate'), (self.snapshot, 'builder', 'invalid_process_candidate')]:
            result = v3.choose('build', entries=self.entries, shortlist=['builder'], capabilities=snapshot, process_candidate=process, transport=forbidden)
            self.assertEqual(result.failure, expected)
