import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

SPEC = importlib.util.spec_from_file_location('outcome_pilot', Path(__file__).resolve().parents[1] / 'scripts/outcome_pilot.py')
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.inputs = {'product_name': 'Canvas tote', 'description': 'A cotton shopping bag.', 'audience': 'Market shoppers', 'features': ['Two handles', 'Inside pocket'], 'cta_url': 'https://example.com/tote'}

    def test_all_commands_render_only_supplied_facts(self):
        for command in pilot.COMMANDS:
            with self.subTest(command=command):
                result = pilot.run(command, self.inputs)
                self.assertEqual(result['status'], 'complete')
                content = result['artifact']['content']
                for text in ['Canvas tote', 'Market shoppers', 'Inside pocket']:
                    self.assertIn(text, content)
                self.assertNotIn('Price:', content)
                self.assertFalse(result['checks']['human_verified'])

    def test_explicit_adapter(self):
        self.assertEqual(pilot.resolve_explicit('/sr-listing')['selection'], 'explicit')
        self.assertIsNone(pilot.resolve_explicit('write a listing'))
        self.assertIsNone(pilot.resolve_explicit('/sr-listing extra'))

    def test_missing_and_unknown(self):
        self.assertEqual(pilot.run('/sr-productsheet', {})['missing'], list(pilot.REQUIRED))
        self.assertEqual(pilot.run('/sr-unknown', self.inputs)['status'], 'error')
        self.assertEqual(pilot.run([], self.inputs)['status'], 'error')
        self.assertEqual(pilot.run('/sr-listing', {'features': 'wrong'})['status'], 'error')

    def test_html_escape_and_url(self):
        self.inputs['product_name'] = '<script>alert(1)</script>'
        content = pilot.run('/sr-productsheet', self.inputs)['artifact']['content']
        self.assertNotIn('<script>', content)
        self.assertIn('&lt;script&gt;', content)
        for url in ['javascript:alert(1)', 'https://user:pass@example.com', 'https://a b.com', 'https://example.com/<svg/onload=alert(1)>', 'https://example.com/`bad`']:
            self.inputs['cta_url'] = url
            self.assertEqual(pilot.run('/sr-productsheet', self.inputs)['status'], 'error')

    def test_limits_and_empty_features(self):
        self.inputs['description'] = 'a' * 5001
        self.assertEqual(pilot.run('/sr-listing', self.inputs)['status'], 'error')
        self.inputs['description'] = 'Description'
        self.inputs['features'] = ['  ']
        self.assertEqual(pilot.run('/sr-listing', self.inputs)['missing'], ['features'])

    def test_export_is_sandboxed(self):
        with tempfile.TemporaryDirectory() as directory:
            files = pilot.export_skills(directory)
            self.assertEqual(len(files), 3)
            self.assertIn('disable-model-invocation: true', Path(files[0]).read_text())
            self.assertIn('python3 ', Path(files[0]).read_text())
            self.assertIn('--output-dir ./outcome-output', Path(files[0]).read_text())
            self.assertNotIn('$(mktemp', Path(files[0]).read_text())
            self.assertIn('do not run diagnostic ls', Path(files[0]).read_text())
            with self.assertRaises(ValueError):
                pilot.export_skills(directory)
        with self.assertRaises(ValueError):
            pilot.export_skills(Path.home() / '.claude/skills')

    def test_plugin_export(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'plugin'
            result = pilot.export_plugin(target)
            self.assertEqual(len(result['skills']), 3)
            self.assertIn('/sr-pilot:sr-productsheet', result['commands'])
            self.assertEqual(json.loads((target / '.claude-plugin/plugin.json').read_text())['name'], 'sr-pilot')
            with self.assertRaises(ValueError):
                pilot.export_plugin(target)
        with self.assertRaises(ValueError):
            pilot.export_plugin(Path.home() / '.claude/plugins/outcome-pilot-forbidden')

    def test_export_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            Path(directory, 'sr-productsheet').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                pilot.export_skills(directory)
            self.assertFalse(Path(outside, 'SKILL.md').exists())
            self.assertFalse(Path(directory, 'sr-listing').exists())

    def test_server_and_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'index.html').write_text('<h1>Local pilot</h1>')
            server = ThreadingHTTPServer(('127.0.0.1', 0), pilot.handler(directory))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                self.assertIn(b'Local pilot', urlopen(base).read())
                req = Request(base + '/api/run', data=json.dumps({'command': '/sr-listing', 'inputs': self.inputs}).encode(), headers={'Content-Type': 'application/json'})
                self.assertEqual(json.load(urlopen(req))['status'], 'complete')
                req.add_header('Origin', 'https://evil.example')
                with self.assertRaises(HTTPError) as caught:
                    urlopen(req)
                self.assertEqual(caught.exception.code, 403)
                caught.exception.close()
                for path in ['/../etc/passwd', '/%2e%2e/private.json']:
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(base + path)
                    caught.exception.close()
                req = Request(base + '/api/run', data=b'x' * (pilot.MAX_BODY + 1))
                with self.assertRaises(HTTPError) as caught:
                    urlopen(req)
                self.assertEqual(caught.exception.code, 413)
                caught.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
