import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import skill_capabilities as capabilities


class CapabilityTests(unittest.TestCase):
    def test_source_identity_hash_bounds_and_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'SKILL.md'
            path.write_text('---\nname: one\n---\n# Skill\nIntroduction\n## When to use\n' + 'a' * 9000)
            entries = [dict(name='one', type='skill', kind='domain'), dict(name='missing', type='skill', kind='process')]
            snapshot = capabilities.build_snapshot(entries, {'entries': [dict(name='one', source_path=str(path))]})
            capabilities.validate_snapshot(snapshot)
            record = snapshot['skills']['one']
            self.assertEqual(record['status'], 'source')
            self.assertEqual(len(record['source_sha256']), 64)
            self.assertLessEqual(len(record['evidence']), 6000)
            self.assertIn('Introduction', record['evidence'])
            self.assertEqual(snapshot['skills']['missing']['reason'], 'missing_source')

    def test_ambiguous_sources_fail_to_index(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / name for name in ('one.md', 'two.md')]
            for path in paths:
                path.write_text('body')
            snapshot = capabilities.build_snapshot([dict(name='one', type='skill')], {'entries': [dict(name='one', source_path=str(p)) for p in paths]})
            self.assertEqual(snapshot['skills']['one']['reason'], 'ambiguous_source')

    def test_missing_hash_rejected(self):
        with self.assertRaisesRegex(ValueError, 'invalid_capability_snapshot'):
            capabilities.validate_snapshot({'version': 1, 'skills': {'one': dict(name='one', status='source', evidence='', source_path='x')}})

    def test_operational_preamble_and_fences_do_not_hide_overview(self):
        text = """Operational installation instructions
# Browser Automation

General-purpose browser automation skill.

```bash
secret_command
```
## When to use
Automate browser flows.
"""
        evidence = capabilities.source_evidence(text)
        self.assertIn('General-purpose browser automation skill.', evidence)
        self.assertNotIn('Operational installation', evidence)
        self.assertNotIn('secret_command', evidence)
        self.assertLess(evidence.index('Automate browser'), evidence.index('General-purpose'))
