"""Sep 16: reserved full interviews, layout starvation and expired ghost stock."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'linyuan/fc'), str(ROOT/'linyuan')]
import index as fc
import catchup_from_inventory as catchup
import source_supply as supply
import production_status
from daily_publication_audit import opening_stock_check, BEIJING

NOW = datetime(2026,9,16,7,tzinfo=timezone.utc).timestamp()


def reserve(kinds, monkeypatch):
    monkeypatch.setattr(fc.time, 'time', lambda: NOW)
    state = dict(dispatched=[], published={}, rejected=[],
                 daily_publish=dict(date='2026-09-16', count=0))
    records = []
    for i, kind in enumerate(kinds):
        slug = f'mother-{i}'
        entry = dict(slug=slug, ts=NOW-8*3600, source_url='https://example.invalid/'+slug,
                     production_rules_version=fc.PRODUCTION_RULES_VERSION)
        part = dict(index=0, status='verified', render_mode='crop_delogo',
                    sha256=hashlib.sha256(slug.encode()).hexdigest(),
                    resolution=dict(width=1280 if kind=='l' else 720,
                                    height=720 if kind=='l' else 1280))
        if kind == 'f':
            entry['weekly_full_week'] = '2026-W38'
            part['content_type'] = 'full_interview'
        state['dispatched'].append(entry)
        records.append(dict(slug=slug, artifact_id=i+1, run_id=i+10, checked_at=NOW, parts=[part]))
    return state, dict(quality_gate_version=fc.QUALITY_GATE_VERSION,
                       editorial_policy_version=fc.editorial.VERSION,
                       updated_at=NOW, artifacts=records)


def test_weekly_full_cannot_satisfy_daily_reserve_or_opening_check(monkeypatch):
    state, payload = reserve('fffl', monkeypatch)
    stock = fc.source_inventory(state, payload)
    assert stock['verified_live'] == 4
    assert stock['verified_weekly_full'] == 3
    assert stock['daily_mix_usable'] == 1 and stock['verified_portrait'] == 0
    opening = opening_stock_check(dict(unique_verified_file_count=0), stock,
                                  datetime(2026,9,16,9,30,tzinfo=BEIJING))
    assert not opening['passed'] and opening['required_portrait'] == 3


def test_many_landscapes_do_not_stop_portrait_refill(monkeypatch):
    state, payload = reserve('l'*12, monkeypatch)
    stock = fc.source_inventory(state, payload)
    monkeypatch.setattr(fc, 'catchup_deficit', lambda _: 0)
    assert stock['daily_mix_usable'] == 12
    assert fc.reserve_deficits(stock) == dict(portrait=10, landscape=0)
    assert catchup.inventory_action(state, stock) == 'dispatch-source-inventory'
    assert not opening_stock_check(dict(unique_verified_file_count=0),stock,
        datetime(2026,9,16,9,30,tzinfo=BEIJING))['passed']
    state, payload = reserve('p'*10+'llf', monkeypatch)
    stock = fc.source_inventory(state, payload)
    assert not any(fc.reserve_deficits(stock).values())
    assert stock['daily_mix_usable'] == 12 and stock['verified_weekly_full'] == 1
    assert catchup.inventory_action(state, stock) is None


def test_one_landscape_slot_cannot_claim_four_publications(monkeypatch):
    state, payload = reserve('llll', monkeypatch)
    assert fc.source_inventory(state, payload)['publishable_now'] == 1
    state, payload = reserve('ppll', monkeypatch)
    # At 15:00 only 10:00 and 14:00 are due.
    assert fc.source_inventory(state, payload)['publishable_now'] == 2


@pytest.mark.parametrize('expiry', ['2026-09-16T06:59:59Z', 'malformed'])
def test_fresh_snapshot_cannot_renew_expired_file(monkeypatch, expiry):
    state, payload = reserve('p', monkeypatch)
    payload['artifacts'][0]['expires_at'] = expiry
    assert fc.source_inventory(state, payload)['daily_mix_usable'] == 0
    assert fc.inventory_catchup_request(state, payload) is None
    assert fc.inventory_part_index(state['dispatched'][0],1,payload['artifacts'],{}) is None
    assert production_status.classify(state['dispatched'][0],state,payload,None,NOW)[0]=='unknown'


def test_upload_in_progress_and_completed_receipt_cannot_fill_reserve(monkeypatch):
    state, payload = reserve('pp', monkeypatch)
    state['dispatched'][0]['uploading'] = True
    state['published']['mother-1'] = dict(bvid='BVposted', parts_total=1)
    assert fc.source_inventory(state,payload)['daily_mix_usable'] == 0


def test_cached_old_file_requires_live_exact_artifact_evidence(monkeypatch):
    state, payload = reserve('p', monkeypatch)
    cached = payload['artifacts']
    candidates = {'mother-0':state['dispatched'][0]}
    artifact = dict(id=1,name='deliver-mother-0',expired=False,
                    expires_at='2026-09-17T00:00:00Z')
    def api(path):
        return artifact if path=='actions/artifacts/1' else dict(artifacts=[])
    assert supply.find_deliveries(candidates,api,{},cached)=={'mother-0':artifact}
    artifact['expired']=True
    assert supply.find_deliveries(candidates,api,{},cached)=={}
    def removed(path):
        if path=='actions/artifacts/1':
            raise subprocess.CalledProcessError(1,['gh'],output=b'{"message":"Not Found","status":"404"}')
        return dict(artifacts=[])
    assert supply.find_deliveries(candidates,removed,{},cached)=={}
    def unavailable(path):
        if path=='actions/artifacts/1':raise TimeoutError('retry without erasing stock')
        return dict(artifacts=[])
    with pytest.raises(TimeoutError):supply.find_deliveries(candidates,unavailable,{},cached)


def test_inventory_worker_removes_expired_previously_verified_cache(monkeypatch,tmp_path):
    state, payload = reserve('p',monkeypatch)
    artifact = dict(id=1,name='deliver-mother-0',expired=False,workflow_run=dict(id=10),
                    expires_at='2026-09-17T00:00:00Z')
    def api(path):
        return artifact if path=='actions/artifacts/1' else dict(artifacts=[artifact])
    def download(aid,path,**kwargs):
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('meta.json',json.dumps(dict(final='final.mp4',render_mode='crop_delogo',
                resolution=dict(width=720,height=1280),fingerprints=dict(sha256='a'*64))))
            z.writestr('final.mp4',b'test-only')
    monkeypatch.setattr(supply,'api',api)
    monkeypatch.setattr(supply,'validate_part',lambda *args:None)
    monkeypatch.setattr(supply,'INVENTORY',tmp_path/'stock.json')
    monkeypatch.setattr(production_status,'STATUS_PATH',tmp_path/'status.json')
    monkeypatch.setattr(production_status,'collect_runs',lambda *a:{})
    monkeypatch.setattr(fc,'load_state',lambda:state)
    monkeypatch.setattr(fc,'download_reviewed_zip',download)
    monkeypatch.setattr(fc,'source_admission_audit',lambda *a:{})
    monkeypatch.setattr(sys,'argv',['source_supply.py'])
    supply.main()
    assert json.loads(supply.INVENTORY.read_text())['inventory']['daily_mix_usable']==1
    artifact['expired']=True
    supply.main()
    actual=json.loads(supply.INVENTORY.read_text())
    assert actual['inventory']['daily_mix_usable']==0 and actual['artifacts']==[]


def test_rejected_old_delivery_never_becomes_six_hour_missing_file(monkeypatch):
    state, payload = reserve('p',monkeypatch)
    payload['artifacts'][0]['parts'][0].update(status='rejected',reason='实际画面存在来源水印')
    def api(method,path,*args,**kwargs):
        if path.startswith('/actions/workflows/'):
            return dict(workflow_runs=[])
        if path.startswith('/contents/'+fc.SOURCE_INVENTORY_KEY):
            return json.dumps(payload).encode()
        raise AssertionError((method,path))
    monkeypatch.setattr(fc,'gh',api)
    monkeypatch.setattr(fc,'load_state',lambda:state)
    monkeypatch.setattr(fc,'save_state',lambda st:None)
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    monkeypatch.setattr(fc,'_collect_source_rejections',lambda st:0)
    assert fc.publish_handler(dict(force_publish=True))['published']==0
    assert not state['dispatched'][0].get('failed')
    assert not state['dispatched'][0].get('retries')
    assert payload['artifacts'][0]['parts'][0]['reason']=='实际画面存在来源水印'


def test_unknown_inventory_requests_bounded_refresh_instead_of_producing_more(monkeypatch):
    state, _ = reserve('p',monkeypatch)
    monkeypatch.setattr(fc,'load_state',lambda:state)
    monkeypatch.setattr(fc,'source_inventory',lambda _:dict(inventory_fresh=False,daily_mix_usable=0))
    monkeypatch.setattr(fc,'_collect_source_rejections',lambda _:0)
    monkeypatch.setattr(fc,'save_state',lambda _:None)
    monkeypatch.setattr(fc,'log_event',lambda *args:None)
    calls=[]
    def api(method,path,body):
        calls.append((method,path,body))
        assert method=='POST' and path=='/actions/workflows/linyuan-source-inventory.yml/dispatches'
    monkeypatch.setattr(fc,'gh',api)
    for _ in range(2):
        result=fc._dispatch_admitted()
        assert result['dispatched']==0 and result['inventory_unavailable']==1
    assert len(calls)==1 and state['inventory_refresh_requested_at']==NOW
