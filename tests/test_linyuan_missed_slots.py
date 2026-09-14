"""A finished video must not wait a day just because rendering missed its hour."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'linyuan/fc'), str(ROOT/'linyuan')]
import index as fc
import catchup_from_inventory as catchup


TEN = 1789264800


def reserve(monkeypatch, now, *, landscape=True, full=False):
    monkeypatch.setattr(fc.time, 'time', lambda: now)
    entry = dict(slug='ready', source_url='https://www.bilibili.com/video/BVsource')
    part = dict(index=0, status='verified', sha256='a'*64, title='林园：财务为何比股价重要',
                render_mode='crop', resolution=dict(width=1280 if landscape else 720,
                                                     height=720 if landscape else 1280))
    if full:
        entry['weekly_full_week'] = '2026-W37'
        part['content_type'] = 'full_interview'
    state = dict(dispatched=[entry], published={},
                 daily_publish=dict(date='2026-09-13', count=1, published_hours=[10]))
    payload = dict(quality_gate_version=fc.QUALITY_GATE_VERSION,
                   editorial_policy_version=fc.editorial.VERSION, updated_at=now,
                   artifacts=[dict(slug='ready', artifact_id=123, parts=[part])])
    return state, payload


def test_landscape_finished_at_1555_is_immediately_bound_to_missing_fourteen(monkeypatch):
    now = TEN + 5*3600 + 55*60
    state, payload = reserve(monkeypatch, now)
    stock = fc.source_inventory(state, payload)
    assert stock['publishable_now'] == 1
    assert catchup.inventory_action(state, stock) == 'publish-catchup'
    request = fc.inventory_catchup_request(state, payload)
    assert request['makeup_slot'] == '2026-09-13 14'
    assert request['artifact_id'] == 123 and request['expected_sha256'] == 'a'*64
    receipt = dict(status='published', bvid='BV1234567890', ts=now)
    fc.record_publication_slot(state, receipt, now, request['makeup_slot'])
    assert state['daily_publish']['published_hours'] == [10, 14]
    assert not fc.slot_published(state, TEN+6*3600)
    assert fc.inventory_catchup_request(state, payload) is None


def test_sixteen_remains_due_after_twenty_one_has_published(monkeypatch):
    now = TEN + 12*3600
    state, payload = reserve(monkeypatch, now, landscape=False)
    state['daily_publish'].update(count=3, published_hours=[10, 14, 21])
    request = fc.inventory_catchup_request(state, payload)
    assert request['makeup_slot'] == '2026-09-13 16'
    fc.record_publication_slot(state, dict(status='published', bvid='BV1234567890'), now,
                               request['makeup_slot'])
    assert fc.catchup_deficit(state) == 0


def test_0914_off_hour_uploads_do_not_hide_failed_fourteen(monkeypatch):
    now=TEN+5*3600
    state,payload=reserve(monkeypatch,now)
    state['daily_publish'].update(count=3,published_hours=[1,1,10])
    assert fc.catchup_deficit(state)==1
    assert fc.inventory_catchup_request(state,payload)['makeup_slot']=='2026-09-13 14'
    state['daily_publish']['count']=fc.MAX_PUBLISH_PER_DAY
    assert fc.catchup_deficit(state)==0


def test_weekly_full_is_not_consumed_to_cover_a_short_clip_debt(monkeypatch):
    now = TEN + 7*3600
    state, payload = reserve(monkeypatch, now, full=True)
    assert fc.inventory_catchup_request(state, payload) is None
    monkeypatch.setattr(fc.time, 'time', lambda: TEN+11*3600)
    payload['updated_at'] = TEN+11*3600
    assert fc.inventory_catchup_request(state, payload)['makeup_slot'] == '2026-09-13 21'


def test_stale_rejected_processed_or_uploading_media_never_becomes_a_makeup(monkeypatch):
    now = TEN + 7*3600
    for mutation in ('stale', 'rejected', 'processed', 'uploading', 'wrong_hash', 'cap'):
        state, payload = reserve(monkeypatch, now)
        if mutation == 'stale': payload['updated_at'] -= 4*3600
        elif mutation == 'rejected': payload['artifacts'][0]['parts'][0]['status'] = 'rejected'
        elif mutation == 'processed': state['dispatched'][0]['processed_part_indices'] = [0]
        elif mutation == 'uploading': state['dispatched'][0]['uploading'] = True
        elif mutation == 'wrong_hash': payload['artifacts'][0]['parts'][0]['sha256'] = 'invalid'
        else: state['daily_publish']['count'] = fc.MAX_PUBLISH_PER_DAY
        assert fc.inventory_catchup_request(state, payload) is None, mutation


def test_catchup_uses_exact_inspected_artifact_through_normal_publisher(monkeypatch):
    state, payload = reserve(monkeypatch, TEN+7*3600)
    monkeypatch.setattr(fc, 'load_state', lambda: state)
    monkeypatch.setattr(fc, 'gh', lambda *a, **k: json.dumps(payload).encode())
    monkeypatch.setattr(fc, 'log_event', lambda *a: None)
    calls = []
    monkeypatch.setattr(fc, 'publish_handler', lambda request, context: calls.append(request) or {'published': 1})
    assert fc.publish_catchup({}) == {'published': 1}
    assert calls[0]['makeup_slot'] == '2026-09-13 14'
    assert calls[0]['batch_slug'] == 'ready' and calls[0]['batch_remaining'] == 1
    assert fc.makeup_request_error(calls[0], state) is None


def test_no_automatic_debts_from_past_days_or_future_slots(monkeypatch):
    state, payload = reserve(monkeypatch, TEN-1)
    assert fc.catchup_deficit(state) == 0
    request = dict(batch_slug='ready', artifact_id=123, batch_remaining=1, expected_sha256='a'*64)
    for slot in ('2026-09-12 14', '2026-09-13 15', '2026-09-13 14', 'invalid'):
        assert fc.makeup_request_error({**request, 'makeup_slot': slot}, state)


def test_sunday_fourth_slot_prefers_full_but_can_use_verified_portrait(monkeypatch):
    now=TEN+11*3600
    state,payload=reserve(monkeypatch,now,landscape=False)
    state['daily_publish'].update(count=3,published_hours=[10,14,16])
    request=fc.inventory_catchup_request(state,payload)
    assert request['makeup_slot']=='2026-09-13 21' and request['weekly_full_fallback']
    assert fc.source_inventory(state,payload)['publishable_now']==1
    full=dict(slug='full',weekly_full_week='2026-W37',source_url='full-source')
    state['dispatched'].append(full)
    payload['artifacts'].append(dict(slug='full',artifact_id=456,parts=[dict(index=0,
        status='verified',sha256='b'*64,title='完整版',render_mode='crop',content_type='full_interview')]))
    request=fc.inventory_catchup_request(state,payload)
    assert request['batch_slug']=='full' and not request.get('weekly_full_fallback')
    fc.record_publication_slot(state,dict(status='published',bvid='BVfull'),now,request['makeup_slot'])
    assert fc.inventory_catchup_request(state,payload) is None
