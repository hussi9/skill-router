"""Synthetic mechanics tests; no rendered or semantic quality assertions."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("lazycoder_artifacts", Path(__file__).resolve().parents[1] / "scripts/lazycoder_artifacts.py")
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
HTML = '<!doctype html>\n<html lang="en"><head><title>A useful page</title><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><main><h1>A page</h1></main>{script}</body></html>'


def answer(html):
    return '\n'.join(['## ' + title + ('\n```html\n' + html + '\n```' if title == 'The file' else '\nSome content.') for title in a.SECTIONS])


class ArtifactTests(unittest.TestCase):
    def test_valid_static_document_still_needs_review(self):
        result = a.check_landing_page(answer(HTML.format(script='')))
        self.assertEqual(result['status'], 'needs-review')
        self.assertFalse(result['verified'])
        self.assertTrue(all(c['status'] == 'passed' for c in result['deterministic']))
        self.assertEqual(result['semantic'][1]['status'], 'not-run')

    def test_missing_fence_is_not_run(self):
        result = a.check_landing_page('Please supply your offer.')
        self.assertEqual(result['status'], 'not-run')
        self.assertEqual(result['deterministic'][-1]['status'], 'not-run')
        self.assertFalse(result['verified'])

    def test_truncated_html_is_failure(self):
        result = a.check_landing_page(answer(HTML.format(script='').replace('</html>', '')))
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(next(c['status'] for c in result['deterministic'] if c['id'] == 'html-complete'), 'failed')

    def test_missing_metadata_is_failure(self):
        html = HTML.format(script='').replace('lang="en"', '').replace('width=device-width', 'height=device-height')
        failed = {c['id'] for c in a.check_landing_page(answer(html))['deterministic'] if c['status'] == 'failed'}
        self.assertTrue({'html-lang', 'html-viewport'} <= failed)

    def test_sections_in_html_do_not_count_as_prose(self):
        output = '```html\n' + HTML.format(script='') + '\n' + '\n'.join(a.SECTIONS) + '\n```'
        result = a.check_landing_page(output)
        self.assertEqual(result['deterministic'][0]['status'], 'failed')

    def test_order_and_multiple_artifacts_fail(self):
        output = answer(HTML.format(script='')).replace('## Brief', '## Checked').replace('## Headlines', '## Brief')
        self.assertEqual(a.check_landing_page(output)['status'], 'failed')
        output = answer(HTML.format(script='')) + '\n```html\n' + HTML.format(script='') + '\n```'
        self.assertEqual(next(c['status'] for c in a.check_landing_page(output)['deterministic'] if c['id'] == 'one-html-artifact'), 'failed')

    def test_external_script_is_rejected_without_network(self):
        result = a.check_landing_page(answer(HTML.format(script='<script src="https://example.invalid/a.js"></script>')))
        self.assertEqual(result['status'], 'failed')

    @unittest.skipUnless(shutil.which('node'), 'Node syntax parser unavailable')
    def test_syntax_check_detects_error(self):
        result = a.check_landing_page(answer(HTML.format(script='<script>const broken = ;</script>')), node_check=True)
        self.assertEqual(next(c['status'] for c in result['deterministic'] if c['id'] == 'script-0-syntax'), 'failed')

    @unittest.skipUnless(shutil.which('node'), 'Node syntax parser unavailable')
    def test_valid_script_is_parsed_but_never_executed(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'must-not-exist'
            script = '<script>require("fs").writeFileSync(' + repr(str(marker)) + ', "executed");</script>'
            result = a.check_landing_page(answer(HTML.format(script=script)), node_check=True)
            self.assertFalse(marker.exists())
            self.assertEqual(next(c['status'] for c in result['deterministic'] if c['id'] == 'script-0-syntax'), 'passed')


if __name__ == '__main__':
    unittest.main()
