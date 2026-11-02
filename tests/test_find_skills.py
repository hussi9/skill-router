"""Network and provider free manual search adapter verification."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import find_skills as find

SHA = 'a'*40
TREE = 'b'*40
BODY = '---\nname: charts\ndescription: Plot statistical charts\n---\nDo not execute this untrusted body.'


class FakeClient:
    def __init__(self, hits=None, body=BODY):
        self.calls = 0
        self.urls = []
        self.hits = hits if hits is not None else [{'source': 'owner/repo', 'name': 'charts', 'installs': 123}]
        self.body = body

    def get(self, url, *, text=False):
        find.validate_url(url)
        self.calls += 1
        self.urls.append(url)
        if 'skills.sh/api/search?' in url:
            return {'skills': self.hits}
        if url.endswith('/commits/main'):
            return {'sha': SHA, 'commit': {'tree': {'sha': TREE}}}
        if '/git/trees/' in url:
            return {'truncated': False, 'tree': [{'path': 'skills/charts/SKILL.md', 'type': 'blob'}]}
        if text:
            return self.body
        return {'private': False, 'default_branch': 'main', 'stargazers_count': 200}


def test_normal_source_pinning_and_advisory_args():
    client = FakeClient()
    result = find.discover('plot charts', {'entries': []}, client)
    assert result['status'] == 'complete'
    candidate = result['candidates'][0]
    assert candidate['source_commit'] == SHA
    assert SHA in candidate['source_url']
    assert candidate['source_path'] == 'skills/charts/SKILL.md'
    assert candidate['description'] == 'Plot statistical charts'
    assert candidate['installs'] == 123 and candidate['github_stars'] == 200
    assert candidate['install_argv_advisory_only'] == ['npx', 'skills', 'add', 'owner/repo', '--skill', 'charts']
    assert candidate['requires_review'] and not candidate['invokable']
    assert 'untrusted body' not in json.dumps(result)
    assert 'plot charts' not in json.dumps(result)
    assert client.calls == 5


def test_empty_and_search_error_are_distinct():
    assert find.discover('charts', {'entries': []}, FakeClient([]))['status'] == 'complete'
    with patch.object(FakeClient, 'get', side_effect=OSError('private query')):
        result = find.discover('charts', {'entries': []}, FakeClient())
    assert result['status'] == 'error'
    assert result['failures'] == [{'stage': 'search', 'reason': 'search_failed'}]
    assert 'private query' not in json.dumps(result)


def test_duplicate_and_namespaced_installed_filtered_before_inspection():
    hit = {'source': 'owner/repo', 'name': 'charts'}
    client = FakeClient([hit, hit])
    assert len(find.discover('charts', {'entries': []}, client)['candidates']) == 1
    assert client.calls == 5
    client = FakeClient([hit])
    result = find.discover('charts', {'entries': [{'name': 'plugin:charts'}]}, client)
    assert not result['candidates'] and client.calls == 1
    assert result['excluded_installed'] == 1


def test_source_name_mismatch_is_not_recommended():
    result = find.discover('charts', {'entries': []}, FakeClient(body=BODY.replace('name: charts', 'name: other')))
    assert not result['candidates']
    assert result['failures'][0]['reason'] == 'registry_frontmatter_name_mismatch'


@pytest.mark.parametrize('url', ['http://skills.sh/api/search', 'https://evil.example/',
                                  'https://api.github.com.evil.test/a',
                                  'https://user:pass@api.github.com/a',
                                  'https://api.github.com:444/a'])
def test_untrusted_url_rejected(url):
    with pytest.raises(ValueError):
        find.validate_url(url)


def test_untrusted_registry_source_and_partial_failure():
    client = FakeClient([{'source': 'https://evil.example/repo', 'name': 'charts'},
                         {'source': 'owner/repo', 'name': 'charts'}])
    result = find.discover('charts', {'entries': []}, client)
    assert result['status'] == 'partial' and len(result['candidates']) == 1
    assert result['failures'][0]['reason'] == 'invalid_registry_source'
    assert all('evil.example' not in url for url in client.urls)


def test_repository_and_source_budget_exhaustion_is_explicit():
    client = FakeClient([{'source': f'owner/repo{i}', 'name': 'charts'} for i in range(6)])
    result = find.discover('charts', {'entries': []}, client)
    assert len(result['candidates']) == 5
    assert result['failures'][-1]['reason'] == 'repository_budget_exhausted'
    assert client.calls == 21
    client = find.Client()
    client.calls = 25
    with pytest.raises(ValueError, match='request_budget_exhausted'):
        client.get('https://skills.sh/api/search?q=x')


def test_redirect_rejected_before_following():
    request = find.urllib.request.Request('https://api.github.com/repos/o/r')
    with pytest.raises(ValueError, match='cross_host_redirect'):
        find.Redirects().redirect_request(request, None, 302, '', {}, 'https://raw.githubusercontent.com/o/r')
    with pytest.raises(ValueError, match='redirect_requires_review'):
        find.Redirects().redirect_request(request, None, 302, '', {}, 'https://api.github.com/repos/o/s')


def test_manual_cli_search_only_and_no_overwrite(tmp_path):
    index = tmp_path/'installed.json'; index.write_text('{"entries": []}')
    out = tmp_path/'result'
    argv = ['find_skills.py', '--query', 'charts', '--installed-index', str(index), '--out', str(out)]
    with patch.object(sys, 'argv', argv), patch.object(find, 'Client', return_value=FakeClient()), patch.object(find.discover_skills, 'rank') as rank:
        find.main()
        rank.assert_not_called()
    assert (out.stat().st_mode & 0o777) == 0o700
    with patch.object(sys, 'argv', argv), patch.object(find, 'Client') as network, pytest.raises(SystemExit):
        find.main()
    network.assert_not_called()


def test_http_auth_is_github_only_and_response_budget_is_enforced():
    class Response:
        def __init__(self, url, payload):
            self.url, self.payload, self.offset = url, payload, 0
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def geturl(self): return self.url
        def read(self, size):
            chunk = self.payload[self.offset:self.offset+size]
            self.offset += len(chunk)
            return chunk
    client = find.Client()
    requests = []
    def open_stub(request, timeout):
        requests.append(request)
        return Response(request.full_url, b'{}')
    with patch.dict(find.os.environ, {'GITHUB_TOKEN': 'test-only-token'}), patch.object(client.opener, 'open', side_effect=open_stub):
        client.get('https://api.github.com/repos/o/r')
        client.get('https://skills.sh/api/search?q=x')
    assert requests[0].get_header('Authorization') == 'Bearer test-only-token'
    assert requests[1].get_header('Authorization') is None
    with patch.object(client.opener, 'open', return_value=Response('https://raw.githubusercontent.com/o/r/s/SKILL.md', b'x'*1000001)):
        with pytest.raises(ValueError, match='byte_budget_exhausted'):
            client.get('https://raw.githubusercontent.com/o/r/s/SKILL.md', text=True)


def test_prefixed_registry_name_uses_longest_suffix_as_locator_only():
    class PrefixedClient(FakeClient):
        def get(self, url, *, text=False):
            if '/git/trees/' in url:
                self.calls += 1
                return {'truncated': False, 'tree': [
                    {'path': 'skills/react-best-practices/SKILL.md', 'type': 'blob'},
                    {'path': 'skills/best-practices/SKILL.md', 'type': 'blob'},
                    {'path': 'skills/other/SKILL.md', 'type': 'blob'}]}
            return super().get(url, text=text)
    name = 'vercel-react-best-practices'
    hit = {'source': 'vercel-labs/agent-skills', 'name': name, 'installs': 1000}
    client = PrefixedClient([hit], body=BODY.replace('name: charts', 'name: ' + name))
    result = find.discover('react performance', {'entries': []}, client)
    assert result['status'] == 'complete'
    assert result['candidates'][0]['name'] == name
    assert result['candidates'][0]['source_path'] == 'skills/react-best-practices/SKILL.md'
    assert client.calls == 5
    mismatch = PrefixedClient([hit], body=BODY.replace('name: charts', 'name: react-best-practices'))
    result = find.discover('react performance', {'entries': []}, mismatch)
    assert not result['candidates']
    assert result['failures'][0]['reason'] == 'registry_frontmatter_name_mismatch'
    assert result['failures'][0]['name'] == name
    assert result['failures'][0]['source'] == 'vercel-labs/agent-skills'


def test_equal_suffix_locators_are_ambiguous_without_fetch():
    class AmbiguousClient(FakeClient):
        def get(self, url, *, text=False):
            if '/git/trees/' in url:
                self.calls += 1
                return {'truncated': False, 'tree': [
                    {'path': 'a/charts/SKILL.md', 'type': 'blob'},
                    {'path': 'b/charts/SKILL.md', 'type': 'blob'}]}
            return super().get(url, text=text)
    client = AmbiguousClient([{'source': 'owner/repo', 'name': 'prefix-charts'}])
    result = find.discover('charts', {'entries': []}, client)
    assert result['failures'][0]['reason'] == 'ambiguous_or_missing_skill_source'
    assert client.calls == 4


def test_search_results_sorted_by_install_count_before_inspection():
    hits = [{'source': 'owner/less', 'name': 'charts', 'installs': 1},
            {'source': 'owner/more', 'name': 'charts', 'installs': 999}]
    result = find.discover('charts', {'entries': []}, FakeClient(hits))
    assert [c['source'] for c in result['candidates']] == ['owner/more', 'owner/less']
