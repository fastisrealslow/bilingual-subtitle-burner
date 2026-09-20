"""Do not pay to download rejected sources again, or bypass retry cooldown."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'linyuan/fc'), str(ROOT / 'linyuan')]
import index as fc


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(fc.time, 'time', lambda: 10000)
    monkeypatch.setattr(fc, 'save_state', lambda st: None)
    monkeypatch.setattr(fc, 'log_event', lambda *a: None)
    state = dict(dispatched=[], rejected=[], pending_retry=[], published={})
    row = dict(id='source', source='netease_video', title='林园谈投资的长期回报',
               url='https://example.invalid/interview',
               video_url='https://cdn.invalid/full.mp4')
    candidate = fc.pick([row], state, 1)[0]
    return state, row, candidate


@pytest.mark.parametrize('duration', [17, 28, 119.9, 5401])
def test_measured_bad_download_is_rejected_once_and_not_selected_again(case, monkeypatch, tmp_path, duration):
    st, row, candidate = case
    monkeypatch.setattr(fc, '_curl_download', lambda url, dest, *a: dest.write_bytes(b'x' * 11000))
    monkeypatch.setattr(fc, 'mp4_duration', lambda _: duration)
    st['pending_retry'] = [dict(candidate, ts=1, retries=1)]
    with pytest.raises(fc.SourceDurationRejected) as error:
        fc.download(candidate, tmp_path / 'video.mp4')
    fc._record_failure(st, candidate, error.value)
    fc._record_failure(st, candidate, error.value)
    assert len(st['rejected']) == 1
    assert st['rejected'][0]['duration_sec'] == duration
    assert st['pending_retry'] == []
    assert fc.pick([row, dict(row, id='another-crawler')], st, 10) == []


@pytest.mark.parametrize('duration', [0, -1, float('nan'), float('inf')])
def test_unknown_duration_is_retryable(case, duration):
    st, _, candidate = case
    with pytest.raises(RuntimeError) as error:
        fc.validate_source_duration(duration)
    assert not isinstance(error.value, fc.SourceDurationRejected)
    fc._record_failure(st, candidate, error.value)
    assert not st['rejected']
    assert st['pending_retry'][0]['retries'] == 1


@pytest.mark.parametrize('duration', [120, 120.1, 5400])
def test_accepted_duration_boundaries_are_unchanged(duration):
    fc.validate_source_duration(duration)


@pytest.mark.parametrize('age,ready', [(0, False), (480, False), (1800, False), (1801, True)])
def test_network_retry_obeys_cooldown_for_same_source_across_crawlers(case, monkeypatch, age, ready):
    st, row, candidate = case
    fc._record_failure(st, candidate, TimeoutError('network timeout'))
    monkeypatch.setattr(fc.time, 'time', lambda: 10000 + age)
    assert bool(fc.pick([row, dict(row, id='other-crawler')], st, 10)) is ready
    assert not st['rejected']


def test_network_failures_keep_existing_three_attempt_limit(case, monkeypatch):
    st, row, candidate = case
    for attempt in range(1, 4):
        monkeypatch.setattr(fc.time, 'time', lambda: 10000 + attempt * 1801)
        fc._record_failure(st, candidate, ConnectionError('reset'))
    assert st['pending_retry'] == []
    assert len(st['rejected']) == 1
    assert fc.pick([row], st, 1) == []


def test_stale_retry_cannot_revive_a_terminally_rejected_source(case):
    st, row, candidate = case
    st['rejected'] = [dict(key=candidate['key'], video_id=candidate['video_id'])]
    st['pending_retry'] = [dict(candidate, ts=1, retries=1)]
    assert fc.pick([row], st, 1) == []


def test_regular_deploy_does_not_replay_historical_archive_edits():
    text = (ROOT / '.github/workflows/fc-production-deploy.yml').read_text()
    for name in ('原位更新用户已验收', '原位替换用户追加要求', '原位更新本次用户指出'):
        step = next(s for s in text.split('      - name: ') if s.startswith(name))
        assert "if: ${{ github.event_name == 'workflow_dispatch' && inputs.apply_archive_edits }}" in step
    inputs = text.split('      apply_archive_edits:', 1)[1].split('      refill:', 1)[0]
    assert 'default: false' in inputs
