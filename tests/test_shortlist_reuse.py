import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import shortlist_reuse as reuse


class ShortlistReuseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'index.json'
        self.entries = [dict(name=f'skill-{i}', kind='domain', type='skill',
                             description=f'uniqueterm{i}') for i in range(25)]
        self.entries += [dict(name='process', kind='process', type='skill'),
                         dict(name='hidden', kind='domain', type='skill')]
        self.path.write_text(json.dumps({'entries': self.entries[:-1]}))

    def test_stable_union_frozen_path_and_context_bounds(self):
        def rank(text, **kwargs):
            self.assertEqual(kwargs['index_path'], str(self.path))
            self.assertEqual(kwargs['limit'], 26)
            self.assertNotIn('process', kwargs['kinds'])
            self.assertLessEqual(len(text), 16000 if len(calls) == 0 else 4000)
            calls.append(text)
            return [SimpleNamespace(name=n) for n in
                    (['skill-1', 'skill-2', 'process', 'hidden'] if len(calls) == 1
                     else ['skill-2', 'skill-3'])]
        calls = []
        with patch.object(reuse.index_match, 'rank', side_effect=rank):
            out = reuse.expand('x' * 20000, 'y' * 6000, self.entries, self.path,
                               ['skill-0', 'skill-1', 'skill-0', 'process', 'unknown'])
        self.assertEqual(out, ['skill-0', 'skill-1', 'skill-2', 'skill-3'])
        self.assertEqual(len(calls), 2)

    def test_pool_cap_preserves_baseline_without_rank(self):
        with patch.object(reuse.index_match, 'rank') as rank:
            out = reuse.expand('request', 'context', self.entries, self.path,
                               [f'skill-{i}' for i in range(18)])
        self.assertEqual(out, [f'skill-{i}' for i in range(18)])
        rank.assert_not_called()

    def test_oversized_baseline_rejected_without_removing_candidates(self):
        with patch.object(reuse.index_match, 'rank') as rank:
            with self.assertRaisesRegex(ValueError, 'refusing candidate removal'):
                reuse.expand('request', 'context', self.entries, self.path,
                             [f'skill-{i}' for i in range(25)])
        rank.assert_not_called()

    def test_real_rank_reuses_existing_synonym_expansion(self):
        entries = [dict(name='release-mobile', kind='domain', type='skill',
                        description='deploy mobile release', use_when=['deploy'])]
        entries += [dict(name=f'other-{i}', kind='domain', type='skill',
                         description=f'unique{i}') for i in range(10)]
        self.path.write_text(json.dumps({'entries': entries}))
        self.assertEqual(reuse.expand('ship', '', entries, self.path, []), ['release-mobile'])

    def test_missing_frozen_index_preserves_valid_baseline_and_path_required(self):
        self.assertEqual(reuse.expand('request', '', self.entries,
                                     self.path.parent / 'missing',
                                     ['skill-0', 'skill-0', 'unknown', 'process']), ['skill-0'])
        with self.assertRaises(ValueError):
            reuse.expand('request', '', self.entries, None, [])


if __name__ == '__main__':
    unittest.main()
