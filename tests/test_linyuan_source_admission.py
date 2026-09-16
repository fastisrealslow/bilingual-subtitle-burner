"""Real 2026-09-16 starvation: long compilations erased every eligible source."""
import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'linyuan/fc'), str(ROOT / 'linyuan')]
import index as fc


def state():
    return dict(dispatched=[], rejected=[], published={})


def row(key, duration, title='林园经典采访完整版：投资的长期回报'):
    return dict(id=key, source='bilibili_search', title=title, author='采访档案',
                url='https://www.bilibili.com/video/' + key, extra=dict(duration=duration))


def test_real_archives_survive_overlong_compilations_in_both_input_orders():
    rows = json.loads((ROOT / 'tests/fixtures/linyuan_0916_source_admission.json').read_text())
    expected = {r['id'] for r in rows if 120 <= r['extra']['duration'] <= 5400}
    assert len(expected) == 30
    for items in (rows, rows[::-1]):
        audit = fc.source_admission_audit(items, state())
        assert {r['key'] for r in audit['candidates']} == expected
        assert audit['excluded']['over_limit'] == 9
        assert audit['known_duration_candidates'] == 30
        assert sum(audit['excluded'].values()) + audit['candidate_count'] == len(rows)


def test_same_title_and_prefix_cannot_erase_different_recordings():
    rows = [row('BVone', 1500), row('BVtwo', 2100), row('BVlong', 34444)]
    assert {r['key'] for r in fc.pick(rows, state(), 10)} == {'BVone', 'BVtwo'}


def test_duplicate_url_tracking_is_removed_but_collection_pages_remain():
    rows = [row('BVsource', 240), row('BVsource', 241), row('BVsource', 300)]
    rows[1]['id'] = 'other-crawler'
    rows[1]['url'] += '?p=1&spm_id_from=tracking'
    rows[2]['id'] = 'second-page'
    rows[2]['url'] += '?p=2'
    audit = fc.source_admission_audit(rows, state())
    assert audit['candidate_count'] == 2
    assert audit['excluded']['duplicate_source'] == 1


@pytest.mark.parametrize('duration', [None, 'unknown', 'NaN', float('inf'), -1, []])
def test_malformed_duration_never_crashes_admission_or_claims_verified_length(duration):
    audit = fc.source_admission_audit([row('BVunknown', duration)], state())
    assert audit['candidate_count'] == audit['unknown_duration_candidates'] == 1
    assert audit['known_duration_candidates'] == 0


def test_duration_filter_runs_before_exact_copy_selection():
    good, bad = row('BVsame', '120.5'), row('BVsame', 10000)
    bad['id'] = 'other-crawler'
    bad['video_url'] = 'https://example.invalid/preferred-direct.mp4'
    assert [r['key'] for r in fc.pick([bad, good], state(), 10)] == ['BVsame']


def test_audit_is_read_only_uncapped_and_matches_dispatch_gate(monkeypatch):
    rows = [row(f'BV{i:03d}', 500) for i in range(40)]
    st = state()
    st['dispatched'] = [dict(key='BV000', slug='failed', failed=True)]
    rows[1]['extra']['source_role'] = 'reference'
    before = copy.deepcopy((rows, st))
    def no_write(*args, **kwargs):
        raise AssertionError('read-only audit wrote an event')
    monkeypatch.setattr(fc, 'log_event', no_write)
    audit = fc.source_admission_audit(rows, st)
    assert audit['candidate_count'] == 38
    assert len(fc.pick(rows, st, 6)) == 6
    assert audit['excluded']['already_attempted_or_cooling'] == 1
    assert audit['excluded']['reference_only'] == 1
    assert (rows, st) == before


def test_topic_cooldown_audit_is_read_only(monkeypatch):
    monkeypatch.setattr(fc, 'find_recent_topic', lambda *a, **kw: dict(bvid='BVpublished', score=1))
    def no_write(*args, **kwargs):
        raise AssertionError('audit cannot write logs')
    monkeypatch.setattr(fc, 'log_event', no_write)
    audit = fc.source_admission_audit([row('BVwaiting', 500)], state())
    assert audit['candidate_count'] == 0
    assert audit['excluded']['published_topic_cooldown'] == 1
