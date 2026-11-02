#!/usr/bin/env python3
"""Isolated, deterministic outcome pilot; does not configure live skill routing."""
import argparse
import html
import json
import re
import shlex
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 32768
COMMANDS = {
    '/sr-productsheet': ('product-sheet.html', 'text/html', 'Product sheet'),
    '/sr-listing': ('product-listing.md', 'text/markdown', 'Product listing'),
    '/sr-launch-email': ('launch-email.md', 'text/markdown', 'Launch email'),
}
FIELDS = {'product_name', 'description', 'audience', 'features', 'price', 'cta_url', 'brand'}
REQUIRED = ('product_name', 'description', 'audience', 'features', 'cta_url')


def resolve_explicit(command):
    """Opt-in exact command adapter. Never evaluates or changes natural routing."""
    if not isinstance(command, str) or command not in COMMANDS:
        return None
    return {"command": command, "skill": command[1:], "selection": "explicit", "execution": "deterministic_template"}


def run(command, inputs):
    """Render only caller-supplied facts. No model, network, or routing mutation."""
    base = {'command': command, 'execution': 'deterministic_template', 'missing': []}
    if resolve_explicit(command) is None:
        return {**base, 'status': 'error', 'error': 'Unknown command'}
    if not isinstance(inputs, dict) or set(inputs) - FIELDS:
        return {**base, 'status': 'error', 'error': 'Inputs must contain supported fields only'}
    if len(json.dumps(inputs).encode()) > MAX_BODY:
        return {**base, 'status': 'error', 'error': 'Inputs too large'}
    for key, value in inputs.items():
        if key == 'features':
            if not isinstance(value, list) or len(value) > 20 or any(not isinstance(x, str) or len(x) > 1000 for x in value):
                return {**base, 'status': 'error', 'error': 'features must be a list of up to 20 short strings'}
        elif not isinstance(value, str) or len(value) > 5000:
            return {**base, 'status': 'error', 'error': f'{key} must be a short string'}
    data = {k: ([x.strip() for x in v if x.strip()] if k == 'features' else v.strip()) for k, v in inputs.items()}
    missing = [key for key in REQUIRED if not data.get(key)]
    if missing:
        return {**base, 'status': 'needs_input', 'missing': missing}
    url = urlparse(data['cta_url'])
    if url.scheme not in ('https', 'http') or not url.netloc or any(c.isspace() for c in data['cta_url']) or url.username or url.password or any(c in data['cta_url'] for c in '<>\"`\\'):
        return {**base, 'status': 'error', 'error': 'cta_url must be an HTTP(S) URL without credentials or whitespace'}
    filename, mime, title = COMMANDS[command]
    if command == '/sr-productsheet':
        e = lambda key: html.escape(data.get(key, ''), quote=True)
        features = ''.join('<li>' + html.escape(x) + '</li>' for x in data['features'])
        brand = '<p class="brand">' + e('brand') + '</p>' if data.get('brand') else ''
        price = '<p><strong>Price:</strong> ' + e('price') + '</p>' if data.get('price') else ''
        content = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                   '<meta name="viewport" content="width=device-width, initial-scale=1">'
                   '<title>' + e('product_name') + ' — Product sheet</title>'
                   '<style>body{font:18px/1.6 system-ui,sans-serif;color:#17212b;max-width:760px;margin:48px auto;padding:24px}h1{font-size:42px;line-height:1.1}.brand{letter-spacing:.08em}a{color:#15504c}@media print{body{margin:0;max-width:none}a{color:inherit}}</style>'
                   '</head><body><main>' + brand + '<h1>' + e('product_name') + '</h1><p>' + e('description') + '</p><p><strong>For:</strong> ' + e('audience') + '</p><h2>Features</h2><ul>' + features + '</ul>' + price + '<p><a href="' + e('cta_url') + '">View product</a></p></main></body></html>')
    else:
        # Escape Markdown so supplied facts cannot introduce HTML or active links.
        def md(value):
            return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', html.escape(value)).replace('\n', ' ')
        name, description, audience = (md(data[k]) for k in ('product_name', 'description', 'audience'))
        features = '\n'.join('- ' + md(x) for x in data['features'])
        price = '\nPrice: ' + md(data['price']) + '\n' if data.get('price') else ''
        brand = md(data.get('brand', ''))
        if command == '/sr-listing':
            content = f'# {name}\n\n{description}\n\nFor: {audience}\n\n## Features\n\n{features}\n{price}\nView product: {data["cta_url"]}\n'
            if brand:
                content += '\nBrand: ' + brand + '\n'
        else:
            content = f'Subject: Introducing {name}\n\n{description}\n\nFor: {audience}\n\n{features}\n{price}\nExplore {name}: {data["cta_url"]}\n'
            if brand:
                content += '\n' + brand + '\n'
    return {**base, 'status': 'complete', 'artifact': {'filename': filename, 'mime_type': mime, 'content': content}, 'checks': {'required_inputs_present': True, 'source_fields_only': True, 'template_rendered': True, 'human_verified': False}, 'notice': 'Deterministic template using your supplied facts. Review before publishing; no AI or fresh-user validation is implied.'}


def handler(directory):
    root = Path(directory).resolve()
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, payload):
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(encoded)

        def valid_host(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}

        def do_POST(self):
            if not self.valid_host():
                return self.reply(403, {'status': 'error', 'error': 'Invalid host'})
            # Block cross-origin browser requests, even on a localhost listener.
            origin = self.headers.get('Origin')
            allowed = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
            if origin and origin not in allowed:
                return self.reply(403, {'status': 'error', 'error': 'Cross-origin request rejected'})
            if self.path != '/api/run':
                return self.reply(404, {'status': 'error', 'error': 'Not found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY:
                    return self.reply(413, {'status': 'error', 'error': 'Invalid body size'})
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or set(payload) != {'command', 'inputs'}:
                    raise ValueError('Expected command and inputs')
                result = run(payload['command'], payload['inputs'])
            except (ValueError, UnicodeError):
                return self.reply(400, {'status': 'error', 'error': 'Invalid JSON request'})
            self.reply(400 if result['status'] == 'error' else 200, result)

        def do_GET(self):
            if not self.valid_host():
                return self.reply(403, {'status': 'error', 'error': 'Invalid host'})
            target = root / unquote(urlparse(self.path).path).lstrip('/')
            try:
                target = target.resolve()
                if not target.is_relative_to(root):
                    raise ValueError()
                if target.is_dir():
                    target = (target / 'index.html').resolve()
                if not target.is_relative_to(root):
                    raise ValueError()
                if target.suffix not in {'.html', '.css', '.js', '.json', '.svg'} or not target.is_file():
                    raise ValueError()
                body = target.read_bytes()
            except (ValueError, OSError):
                return self.reply(404, {'status': 'error', 'error': 'Not found'})
            mime = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json', '.svg': 'image/svg+xml'}[target.suffix]
            self.send_response(200)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass  # Do not log submitted customer details.
    return Handler


def export_skills(sandbox):
    target = Path(sandbox).expanduser().resolve()
    forbidden = [p.resolve() for p in (Path.home() / '.claude', Path.home() / '.codex', ROOT / 'skills')]
    if any(target == p or target.is_relative_to(p) for p in forbidden) or target == Path.home().resolve() or target == ROOT.resolve():
        raise ValueError('Choose a dedicated sandbox, not a live skill directory')
    target.mkdir(parents=True, exist_ok=True)
    paths = []
    for command, (_, _, title) in COMMANDS.items():
        path = target / command[1:] / 'SKILL.md'
        if not path.resolve().is_relative_to(target):
            raise ValueError('Sandbox skill path escapes sandbox')
        if path.exists():
            raise ValueError('Sandbox skill already exists: ' + str(path))
        paths.append((path, command, title))
    for path, command, title in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'---\nname: {command[1:]}\ndescription: Produce a source-grounded {title.lower()} using the local outcome pilot.\ndisable-model-invocation: true\n---\n\n# {title}\n\nCollect product_name, description, audience, features (list), and cta_url. Optional: brand, price. Ask for missing fields; never invent facts. This package uses deterministic templates, not model routing.\n\nInterpret $ARGUMENTS as the input JSON file path when supplied. Execute the renderer directly with Bash; do not run diagnostic ls, find, or shell probes first. The renderer validates inputs and refuses existing outputs. Read that file and ask for any missing required fields before execution. If no path was supplied, collect the fields and write supplied inputs to a temporary JSON file. Use the literal output directory ./outcome-output relative to the current working directory. Do not use mktemp, command substitution, or shell helpers. The renderer creates the directory and refuses to overwrite files. If ./outcome-output already contains this artifact, report the collision and ask the user for a different literal directory; never overwrite an existing output. Shell-quote user supplied paths. Then run:\n\n```sh\npython3 {shlex.quote(str(ROOT / 'scripts/outcome_pilot.py'))} run {command} --inputs /absolute/path/to/inputs.json --output-dir ./outcome-output\n```\n\nReturn the artifact content and notice. A complete template is not a verified business outcome. Do not publish automatically.\n')
    return [str(path) for path, _, _ in paths]


def export_plugin(sandbox):
    """Package only the pilot skills for explicit session-only --plugin-dir use."""
    target = Path(sandbox).expanduser().resolve()
    if target.exists():
        raise ValueError('Choose a new dedicated plugin sandbox directory')
    forbidden = [p.resolve() for p in (Path.home() / '.claude', Path.home() / '.codex', ROOT / 'skills')]
    if any(target == p or target.is_relative_to(p) for p in forbidden):
        raise ValueError('Choose a sandbox outside live configuration')
    files = export_skills(target / 'skills')
    manifest = target / '.claude-plugin' / 'plugin.json'
    manifest.parent.mkdir()
    manifest.write_text(json.dumps({'name': 'sr-pilot', 'version': '0.1.0', 'description': 'Isolated local deterministic outcome pilot'}, indent=2))
    return {'plugin_dir': str(target), 'skills': files, 'commands': ['/sr-pilot:' + command[1:] for command in COMMANDS]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    serve = sub.add_parser('serve')
    serve.add_argument('--port', type=int, default=8768)
    execute = sub.add_parser('run')
    execute.add_argument('command')
    execute.add_argument('--inputs', type=Path, required=True)
    execute.add_argument('--output-dir', type=Path)
    export = sub.add_parser('export-skills')
    export.add_argument('--sandbox', type=Path, required=True, help='Dedicated local test workspace/.claude/skills directory; never your global skill directory')
    plugin = sub.add_parser('export-plugin')
    plugin.add_argument('--sandbox', type=Path, required=True, help='New dedicated directory for session-only --plugin-dir loading')
    args = parser.parse_args()
    if args.action == 'serve':
        server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(ROOT / 'pilots/outcomes'))
        print(f'Local outcome pilot: http://127.0.0.1:{server.server_port}', flush=True)
        server.serve_forever()
    elif args.action == 'export-plugin':
        print(json.dumps(export_plugin(args.sandbox)))
    elif args.action == 'export-skills':
        print(json.dumps({'paths': export_skills(args.sandbox)}))
    else:
        if args.inputs.stat().st_size > MAX_BODY:
            parser.error('Inputs too large')
        result = run(args.command, json.loads(args.inputs.read_text()))
        if args.output_dir and result['status'] == 'complete':
            args.output_dir.mkdir(parents=True, exist_ok=True)
            path = args.output_dir / result['artifact']['filename']
            with path.open('x') as output:
                output.write(result['artifact']['content'])
        print(json.dumps(result))
        return 1 if result['status'] == 'error' else 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
