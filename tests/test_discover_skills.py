"""Offline advisory discovery contracts; provider calls are always stubbed."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import discover_skills as discovery
from jev_choose import Choice, Pick


def candidate(name='charts', source='registry', url='https://example.com/charts'):
    return dict(name=name, description='Plot statistical charts', source=source, source_url=url)


def choice(name, confidence):
    return Choice(Pick(name, confidence, 'route'), Pick(None, .9, 'silent'), '', 0, 1, 1)


@pytest.mark.parametrize('confidence,status', [(.79, 'abstain'), (.8, 'candidate_to_review')])
def test_threshold_and_non_executable_output(tmp_path, confidence, status):
    original_cache = discovery.jev_choose.CACHE
    def answer(query, *, entries):
        assert discovery.jev_choose.CACHE == tmp_path / 'run' / 'cache'
        assert len(entries) == 1
        return choice(entries[0]['name'], confidence)
    with patch.object(discovery.jev_choose, 'choose', side_effect=answer):
        result = discovery.rank('plot charts', [candidate()], {'entries': []}, tmp_path / 'run')
    assert result['status'] == status
    assert result['requires_review'] is True and result['invokable'] is False
    assert 'description' not in result and 'install' not in result and 'route' not in result
    assert discovery.jev_choose.CACHE == original_cache
    assert json.loads((tmp_path / 'run' / 'result.json').read_text()) == result


@pytest.mark.parametrize('answer,reason', [(choice('invented', .99), 'invalid_chooser_result'),
                                         (choice(None, .95), 'no_fit'), (None, 'chooser_error')])
def test_abstentions_distinguish_no_fit_errors_and_invented_ids(tmp_path, answer, reason):
    with patch.object(discovery.jev_choose, 'choose', return_value=answer):
        result = discovery.rank('charts', [candidate()], {'entries': []}, tmp_path / 'run')
    assert result['status'] == 'abstain' and result['reason'] == reason


def test_all_installed_never_calls_provider_and_overwrite_rejected(tmp_path):
    with patch.object(discovery.jev_choose, 'choose') as chooser:
        result = discovery.rank('charts', [candidate()], {'entries': [{'name': 'charts'}]}, tmp_path / 'run')
        chooser.assert_not_called()
    assert result['reason'] == 'no_eligible_candidates'
    with pytest.raises(FileExistsError):
        discovery.rank('charts', [], {'entries': []}, tmp_path / 'run')


def test_duplicate_names_are_source_qualified_and_exact_duplicates_removed():
    first = candidate()
    other = candidate(source='other', url='https://other.example/charts')
    normalized = discovery.normalize([first, first, other], {'entries': []})
    assert len(normalized) == 2
    assert all(key.startswith('external-') for key in normalized)
    assert {c['source'] for c in normalized.values()} == {'registry', 'other'}


@pytest.mark.parametrize('field,value', [('description', ''), ('source', None),
                                        ('source_url', 'file:///tmp/skill'),
                                        ('source_url', 'https://user:pass@example.com/skill')])
def test_invalid_candidates_fail_before_provider(tmp_path, field, value):
    entry = candidate(); entry[field] = value
    with patch.object(discovery.jev_choose, 'choose') as chooser, pytest.raises(ValueError):
        discovery.rank('charts', [entry], {'entries': []}, tmp_path / 'run')
    chooser.assert_not_called()
    assert not (tmp_path / 'run').exists()


def test_over_twenty_rejected_without_truncation_or_provider(tmp_path):
    entries = [candidate(name=f'charts-{i}', url=f'https://example.com/{i}') for i in range(21)]
    with patch.object(discovery.jev_choose, 'choose') as chooser, pytest.raises(ValueError, match='20'):
        discovery.rank('charts', entries, {'entries': []}, tmp_path / 'run')
    chooser.assert_not_called()
