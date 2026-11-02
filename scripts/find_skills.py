#!/usr/bin/env python3
"""Manual skills.sh discovery; external source remains untrusted review material."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from build_catalog import parse_frontmatter
import discover_skills

HOSTS = {'skills.sh', 'api.github.com', 'raw.githubusercontent.com'}
PART = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
SHA = re.compile(r'^[a-f0-9]{40}$')


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username
            or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('untrusted_url')
    return parsed


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        if urllib.parse.urlsplit(req.full_url).hostname != urllib.parse.urlsplit(newurl).hostname:
            raise ValueError('cross_host_redirect')
        # Fail closed rather than spend uncounted redirect requests.
        raise ValueError('redirect_requires_review')


class Client:
    """Fixed HTTP/read budgets; authentication only for GitHub API requests."""
    def __init__(self):
        self.calls = 0
        self.bytes = 0
        self.deadline = time.monotonic() + 45
        self.opener = urllib.request.build_opener(Redirects())

    def get(self, url, *, text=False):
        parsed = validate_url(url)
        if self.calls >= 25 or time.monotonic() >= self.deadline:
            raise ValueError('request_budget_exhausted')
        self.calls += 1
        headers = {'User-Agent': 'skill-router-manual-discovery', 'Accept': 'application/vnd.github+json'}
        token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
        if parsed.hostname == 'api.github.com' and token:
            headers['Authorization'] = 'Bearer ' + token
        request = urllib.request.Request(url, headers=headers)
        with self.opener.open(request, timeout=min(5, max(.01, self.deadline-time.monotonic()))) as response:
            validate_url(response.geturl())
            chunks, size = [], 0
            while True:
                if time.monotonic() >= self.deadline:
                    raise ValueError('time_budget_exhausted')
                chunk = response.read(min(65536, 1000001-size))
                if not chunk:
                    break
                size += len(chunk)
                self.bytes += len(chunk)
                if size > 1000000 or self.bytes > 5000000:
                    raise ValueError('byte_budget_exhausted')
                chunks.append(chunk)
        value = b''.join(chunks).decode('utf-8')
        return value if text else json.loads(value)


def aliases(name):
    lowered = str(name).strip().casefold()
    return {lowered, lowered.rsplit(':', 1)[-1], lowered.rsplit('@', 1)[-1]}


def installed_names(index):
    if not isinstance(index, dict) or not isinstance(index.get('entries'), list):
        raise ValueError('invalid_installed_index')
    return set().union(*(aliases(e.get('name', '')) for e in index['entries'] if isinstance(e, dict))) if index['entries'] else set()


def discover(query, installed, client, *, owner=None):
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
        raise ValueError('explicit_query_requires_2_to_200_characters')
    if owner and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', owner):
        raise ValueError('invalid_owner')
    installed_set = installed_names(installed)
    result = {'status': 'complete', 'candidates': [], 'failures': [], 'excluded_installed': 0,
              'requires_review': True, 'invokable': False, 'experimental': True,
              'query_sha256': hashlib.sha256(query.encode()).hexdigest(),
              'bounds': {'http_requests': 25, 'repositories': 5, 'source_files': 5,
                         'bytes_per_response': 1000000, 'total_bytes': 5000000, 'seconds': 45}}
    params = {'q': query.strip(), 'limit': 20}
    if owner:
        params['owner'] = owner
    try:
        payload = client.get('https://skills.sh/api/search?' + urllib.parse.urlencode(params))
        hits = payload['skills']
        if not isinstance(hits, list) or len(hits) > 20:
            raise ValueError('invalid_search_response')
    except Exception:
        result.update(status='error', failures=[{'stage': 'search', 'reason': 'search_failed'}])
        return result
    hits.sort(key=lambda hit: hit.get('installs', 0) if isinstance(hit, dict) and isinstance(hit.get('installs'), int) else 0, reverse=True)
    repositories, seen, fetched = {}, set(), 0
    for hit in hits:
        review_identity = {}
        try:
            source, name = hit['source'], hit['name']
            if not isinstance(source, str) or not isinstance(name, str) or not PART.fullmatch(name):
                raise ValueError('invalid_registry_identity')
            parts = source.split('/')
            if len(parts) != 2 or not all(PART.fullmatch(part) for part in parts):
                raise ValueError('invalid_registry_source')
            review_identity = {'source': source, 'name': name}
            identity = (source.casefold(), name.casefold())
            if identity in seen:
                continue
            seen.add(identity)
            if aliases(name) & installed_set:
                result['excluded_installed'] += 1
                continue
            if source not in repositories:
                if len(repositories) >= 5:
                    raise ValueError('repository_budget_exhausted')
                repositories[source] = None
                base = 'https://api.github.com/repos/' + source
                metadata = client.get(base)
                if metadata.get('private') is not False:
                    raise ValueError('repository_not_verified_public')
                branch = metadata['default_branch']
                commit = client.get(base + '/commits/' + urllib.parse.quote(branch, safe=''))
                sha, tree_sha = commit['sha'], commit['commit']['tree']['sha']
                if not SHA.fullmatch(sha) or not SHA.fullmatch(tree_sha):
                    raise ValueError('invalid_commit_identity')
                tree = client.get(base + '/git/trees/' + tree_sha + '?recursive=1')
                if tree.get('truncated'):
                    raise ValueError('repository_tree_truncated')
                paths = [e['path'] for e in tree['tree'] if e.get('type') == 'blob'
                         and (e.get('path') == 'SKILL.md' or e.get('path', '').endswith('/SKILL.md'))]
                repositories[source] = (sha, paths, metadata.get('stargazers_count'))
            if repositories[source] is None:
                raise ValueError('repository_inspection_failed')
            sha, paths, stars = repositories[source]
            matching = [p for p in paths if PurePosixPath(p).parent.name.casefold() == name.casefold()]
            if not matching:
                # Registry names may carry a publisher prefix absent from the
                # directory. This locates source only: frontmatter still must
                # equal the complete registry name before accepting identity.
                suffixes = [p for p in paths if PurePosixPath(p).parent.name
                            and name.casefold().endswith('-' + PurePosixPath(p).parent.name.casefold())]
                if suffixes:
                    longest = max(len(PurePosixPath(p).parent.name) for p in suffixes)
                    matching = [p for p in suffixes if len(PurePosixPath(p).parent.name) == longest]
            if not matching and len(paths) == 1:
                matching = paths
            if len(matching) != 1:
                raise ValueError('ambiguous_or_missing_skill_source')
            path = matching[0]
            if any(part in ('.', '..') for part in path.split('/')):
                raise ValueError('invalid_source_path')
            if fetched >= 5:
                raise ValueError('source_budget_exhausted')
            fetched += 1
            raw_url = f'https://raw.githubusercontent.com/{source}/{sha}/' + urllib.parse.quote(path, safe='/')
            body = client.get(raw_url, text=True)
            front = parse_frontmatter(body)
            actual, description = front.get('name'), front.get('description')
            if not isinstance(actual, str) or not PART.fullmatch(actual) or not isinstance(description, str) or not description.strip():
                raise ValueError('missing_skill_frontmatter')
            alias_matches = actual.casefold() == name.casefold()
            if not alias_matches:
                raise ValueError('registry_frontmatter_name_mismatch')
            if aliases(actual) & installed_set:
                result['excluded_installed'] += 1
                continue
            key = hashlib.sha256(f'{source}:{actual}'.encode()).hexdigest()
            result['candidates'].append({
                'id': 'external-' + key, 'name': actual, 'registry_name': name,
                'description': description.strip(), 'source': source, 'source_url': raw_url,
                'source_commit': sha, 'source_path': path,
                'source_sha256': hashlib.sha256(body.encode()).hexdigest(),
                'installs': hit.get('installs') if isinstance(hit.get('installs'), int) else None,
                'github_stars': stars if isinstance(stars, int) else None,
                'registry_url': 'https://skills.sh/' + source + '/' + name,
                'requires_review': True, 'invokable': False,
                'install_argv_advisory_only': ['npx', 'skills', 'add', source, '--skill', actual],
                'provenance': 'registry metadata and pinned public GitHub source; not a trust assessment'})
        except Exception as error:
            # Fixed internal reason codes only; never provider text or query.
            reason = str(error) if isinstance(error, ValueError) else 'inspection_failed'
            if not re.fullmatch(r'[a-z_]+', reason):
                reason = 'inspection_failed'
            result['failures'].append({'stage': 'inspection', 'result_number': len(seen), 'reason': reason,
                                       **review_identity})
    if result['failures']:
        result['status'] = 'partial' if result['candidates'] else 'error'
    result['http_requests'] = client.calls
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', required=True, help='Explicit public search terms; never full conversation')
    parser.add_argument('--out', required=True, type=Path, help='New private artifact directory')
    parser.add_argument('--installed-index', type=Path, default=Path.home()/'.claude'/'skill_index.json')
    parser.add_argument('--owner')
    parser.add_argument('--rank', action='store_true', help='Opt-in external JEV advisory reranking')
    args = parser.parse_args()
    try:
        installed = json.loads(args.installed_index.read_text())
        installed_names(installed)
        args.out.mkdir(parents=True, exist_ok=False, mode=0o700)
        result = discover(args.query, installed, Client(), owner=args.owner)
        if args.rank and result['candidates']:
            result['ranking'] = discover_skills.rank(args.query, result['candidates'], installed, args.out/'ranking')
        (args.out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(result, indent=2))
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
