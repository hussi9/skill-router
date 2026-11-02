"""Input safety and decision telemetry; all provider requests are mocked."""
import json
import math
import unittest
from unittest.mock import patch
from test_jev_choose import JevCase, ENTRIES, answer, with_tier
import jev_choose as jev


class InputTelemetryTests(JevCase):
    def setUp(self):
        super().setUp()
        self.events = []
        self.log_patch = patch.object(jev, '_log_event', self.events.append)
        self.log_patch.start()

    def tearDown(self):
        self.log_patch.stop()
        super().tearDown()

    def test_constraints_after_old_boundary_arrive_intact(self):
        prompt = 'list files ' + 'x' * 900 + ' security review and migration required'
        self.transport(with_tier(answer('none', .9, 'none', .9), 'heavy', .99))
        jev.choose(prompt, entries=ENTRIES)
        self.assertEqual(self.calls[0]['state']['request'], prompt)

    def test_oversized_tier_preserves_tail_and_inherits(self):
        prompt = 'list files ' + 'x' * (jev.PROMPT_CHARS * 2) + ' audit auth migration'
        self.transport(with_tier({'answers': {}}, 'light', .99))
        pick = jev.tier_only(prompt)
        self.assertTrue(self.calls[0]['state']['request'].endswith('audit auth migration'))
        self.assertLessEqual(len(self.calls[0]['state']['request']), jev.PROMPT_CHARS)
        self.assertEqual(jev.model_for(pick), 'inherit')
        self.assertTrue(self.events[-1]['input']['request']['truncated'])

    def test_choose_truncated_context_also_inherits(self):
        self.transport(with_tier(answer('none', .9, 'none', .9), 'light', .99))
        out = jev.choose('yes', context='x' * (jev.CONTEXT_CHARS + 1), entries=ENTRIES)
        self.assertEqual(out.model, 'inherit')

    def test_cache_and_failure_events_contain_no_input(self):
        prompt = 'secret-content-in-request'
        self.transport(with_tier(answer('none', .9, 'none', .9), 'light', .99))
        jev.choose(prompt, entries=ENTRIES)
        jev.choose(prompt, entries=ENTRIES)
        self.assertEqual([e['cache_hit'] for e in self.events], [False, True])
        self.assertNotIn(prompt, json.dumps(self.events))
        self.assertEqual(self.events[0]['input_tokens'], 1234)
        self.assertEqual(self.events[1]['input_tokens'], 0)
        self.assertEqual(self.events[1]['cached_input_tokens'], 1234)
        jev._key = lambda: ''
        jev.tier_only('missing key')
        self.assertEqual(self.events[-1]['failure'], 'no_key')

    def test_nonfinite_confidence_cannot_downgrade(self):
        for value in (math.nan, math.inf, -math.inf, 1.01):
            self.assertEqual(jev._conf({'confidence': value}), 0)

    def test_malformed_and_transport_failures_are_logged(self):
        self.transport(None)
        self.assertIsNone(jev.tier_only('transport fails'))
        self.assertEqual(self.events[-1]['failure'], 'error')
        self.transport({'answers': {}})
        self.assertIsNone(jev.choose('malformed', entries=ENTRIES))
        self.assertEqual(self.events[-1]['failure'], 'malformed_answers')

    def test_chunk_conflict_keeps_cap_and_logs_raw_decisions(self):
        entries = [{'name':f'skill-{n}', 'kind':'domain', 'type':'skill'} for n in range(300)]
        self.transport({'answers': {
            'domain_0': {'choice':'d0', 'confidence':.95},
            'domain_1': {'choice':'d254', 'confidence':.6},
            'process': {'choice':'none', 'confidence':.9}}})
        out = jev.choose('ambiguous domain selection', entries=entries)
        self.assertEqual(out.domain.confidence, .79)
        self.assertEqual(out.domain.tier, 'suggest')
        self.assertTrue(self.events[-1]['chunk_conflict'])
        self.assertEqual(self.events[-1]['domain_chunks'], [
            {'question_id':'domain_0', 'selected_skill':'skill-0', 'confidence':.95},
            {'question_id':'domain_1', 'selected_skill':'skill-254', 'confidence':.6}])

    def test_uncontested_chunk_is_not_conflict(self):
        self.transport(answer('d0', .95, 'none', .9))
        out = jev.choose('uncontested domain', entries=ENTRIES)
        self.assertEqual(out.domain.confidence, .95)
        self.assertFalse(self.events[-1]['chunk_conflict'])
