"""A verified public-page adapter must not pay for whole-source FC staging."""
from pathlib import Path
import sys
from copy import deepcopy
import pytest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'linyuan/fc'),str(Path(__file__).resolve().parents[1]/'linyuan')]
import index as fc
import ci_fetch_netease as native


@pytest.mark.parametrize('page',[
    'https://www.163.com/v/video/VGAM89A1G.html',
    'https://m.163.com/v/video/VGAFEFH3T.html',
])
def test_public_pages_use_the_existing_exact_native_page_contract(page):
    c=dict(page_url=page,video_url='https://legacy.invalid/low.mp4')
    assert native.page_key(page)
    assert fc.ci_public_source_page(c)==page and not fc.dispatch_requires_mainland(c)


@pytest.mark.parametrize('page',[
    'http://www.163.com/v/video/VGAM89A1G.html',
    'https://evil.163.com/v/video/VGAM89A1G.html',
    'https://www.163.com.example.test/v/video/VGAM89A1G.html',
    'https://user:password@www.163.com/v/video/VGAM89A1G.html',
    'https://www.163.com:444/v/video/VGAM89A1G.html',
    'https://www.163.com/v/video/VGAM89A1G.html?token=unknown',
    'https://www.163.com/v/video/VGAM89A1G.html#player',
    'https://www.163.com/private/story',
    'https://news.qq.com/rain/a/source',
])
def test_unverified_page_shapes_keep_the_existing_domestic_route(page):
    c=dict(page_url=page,video_url='https://legacy.invalid/video.mp4')
    assert fc.ci_public_source_page(c) is None
    assert fc.dispatch_requires_mainland(c)


@pytest.mark.parametrize('native_available',[True,False])
def test_native_dispatch_preserves_domestic_fallback_without_rejecting_unknown_reads(monkeypatch,native_available):
    import json
    state=dict(dispatched=[],published={},pending_retry=[],rejected=[])
    # Shared tests default to legacy120; exercise the deployed reference_v1 policy.
    monkeypatch.setattr(fc,'MIN_DUR',20)
    page='https://www.163.com/v/video/VGAM89A1G.html'
    c=dict(page_url=page,video_url='https://legacy.invalid/stale.mp4',key='netease:source',video_id='source',
           title='林园：公开访谈',source='netease_video',author='公开账号',extra=dict(duration=24))
    stock=dict(inventory_fresh=True,verified_portrait=0,verified_landscape=0,daily_mix_usable=0)
    monkeypatch.setattr(fc,'load_state',lambda:state)
    monkeypatch.setattr(fc,'_collect_source_rejections',lambda _:0)
    monkeypatch.setattr(fc,'source_inventory',lambda *a:stock)
    monkeypatch.setattr(fc,'staging_release_id',lambda:1)
    monkeypatch.setattr(fc,'obsolete_review_candidates',lambda *a:[])
    monkeypatch.setattr(fc,'save_state',lambda *a:True)
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    monkeypatch.setattr(fc,'_process_retries',lambda *a:None)
    monkeypatch.setattr(fc,'weekly_full_request',lambda *a:None)
    def resolve(url):
        assert url==page
        if not native_available:raise TimeoutError('public-player read not confirmed')
        return dict(declared_duration=24)
    monkeypatch.setattr(native,'resolve',resolve)
    monkeypatch.setattr(fc,'experiment_duration_probe_allowed',lambda *a,**k:False)
    def pick(items,st,limit,audit):audit.update(records=1,candidate_count=1);return [deepcopy(c)]
    monkeypatch.setattr(fc,'pick',pick)
    calls=[]
    def api(method,path,payload=None,**kwargs):
        if method=='POST':calls.append((path,payload));return {}
        if fc.DATA_JSON in path:return b'[]'
        if fc.SOURCE_INVENTORY_KEY in path:return b'{}'
        return dict(workflow_runs=[])
    monkeypatch.setattr(fc,'gh',api)
    def forbidden(*a,**kwargs):pytest.fail('Native CI source must not download/stage on FC')
    for name in ['download','upload_asset','tencent_resolve_url','_record_failure']:
        monkeypatch.setattr(fc,name,forbidden)
    r=fc._dispatch_admitted(dict(execution_backend='github'))
    if native_available:
        assert r['dispatched']==1 and len(calls)==1
        assert calls[0][1]['inputs']['source']==page
        assert calls[0][1]['inputs']['auto_publish']=='false'
        assert state['dispatched'][0]['source_url']==state['dispatched'][0]['asset_url']==page
    else:
        assert r==dict(dispatched=0,requires_mainland_transfer=True)
        assert calls==[] and state['dispatched']==[] and state['rejected']==[]
