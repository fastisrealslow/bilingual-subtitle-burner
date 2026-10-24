"""Real first-party page/playlist contract, with no source-quality approval."""
from copy import deepcopy
import json
from pathlib import Path
import importlib.util
import sqlite3
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import p5w_source as P
import monitor_v2 as M
import ci_fetch_generic as G


def actual():
    return json.loads((Path(__file__).parent/'fixtures/p5w_tianyu_20251121.json').read_text())


def reader(f,calls):
    def read(url,referer):
        calls.append((url,referer))
        if url==f['page_url']:return f['page_excerpt']
        if '/adaptive/' in url:return f['master_playlist']
        assert '/0x0/' in url
        return f['media_playlist']
    return read


def test_actual_official_full_episode_has_native_hd_and_separate_publication_date():
    f=actual();before=deepcopy(f);calls=[];r=P.extract(f['page_url'],reader(f,calls))
    assert r['duration']==3316.48 and r['declared_resolution']==dict(width=1920,height=1080)
    assert r['title']=='2025林园：把好资产装进兜里' and r['published_at']=='2025-11-21T17:37:00'
    assert r['source_role']=='mother_candidate' and r['direct_dispatch'] is True
    assert r['has_video'] is True
    assert not r['recording_date_verified'] and not r['media_quality_approved'] and not r['rights_clearance']
    assert len(calls)==3 and calls[0][1]=='https://www.p5w.net/'
    assert all(ref==f['page_url'] for _,ref in calls[1:]) and f==before


@pytest.mark.parametrize('body',[
    '#EXTM3U\n#EXTINF:3,\na.ts\n',
    '#EXTM3U\n#EXT-X-ENDLIST\n',
    '#EXTM3U\n#EXTINF:nan,\na.ts\n#EXT-X-ENDLIST\n',
    '#EXTM3U\n#EXTINF:-3,\na.ts\n#EXT-X-ENDLIST\n',
    '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:3,\na.ts\n#EXT-X-ENDLIST\n',
])
def test_live_missing_or_protected_playlist_is_not_a_complete_public_programme(body):
    with pytest.raises(ValueError):P.finite_duration(body)


@pytest.mark.parametrize('url',[
    'https://example.com/v/ip/typyq/202511/t20251121_6424547.htm',
    'https://www.p5w.net/v/ip/typyq/202510/t20251121_6424547.htm',
    'https://www.p5w.net/v/ip/typyq/202511/t20251121_6424547.htm?p=2',
    'http://www.p5w.net/v/ip/typyq/202511/t20251121_6424547.htm',
])
def test_only_exact_official_programme_pages_can_supply_metadata(url):
    with pytest.raises(ValueError):P.page_identity(url)


def test_external_attachment_is_rejected_before_request_and_denial_never_fetches_media():
    f=actual();f['page_excerpt']=f['page_excerpt'].replace('https://qlong202506.p5w.net/','https://example.com/')
    calls=[]
    with pytest.raises(ValueError):P.extract(f['page_url'],reader(f,calls))
    assert len(calls)==1
    f=actual();f['page_excerpt']+='<p>未经授权禁止转载</p>';calls=[]
    r=P.extract(f['page_url'],reader(f,calls))
    assert r['no_reprint']==1 and r['direct_dispatch'] is False and r['source_role']=='catalog_only'
    assert len(calls)==1


def test_normal_monitor_exports_one_candidate_without_claiming_stock(tmp_path,monkeypatch):
    f=actual();calls=[];read=reader(f,calls)
    monkeypatch.setattr(M,'http_get',lambda u,referer=None,**kw:read(u,referer))
    seed=dict(url=f['page_url'],source_family='tianyu_linyuan_2025_11')
    rows=M.P5wVideoSource(dict(seeds=[seed]),{}).fetch(None)
    assert len(rows)==1 and rows[0]['id']=='p5w_video:20251121_6424547'
    r=json.loads(rows[0]['extra']);assert r['duration']==3316.48 and r['source_family']==seed['source_family']
    assert not r['recording_date_verified'] and not r['media_quality_approved']
    monkeypatch.setattr(M,'DB_PATH',tmp_path/'catalog.db');M.init_db()
    assert len(M.upsert_items(rows))==1 and M.upsert_items(rows)==[]
    with sqlite3.connect(M.DB_PATH) as c:assert c.execute('SELECT count(*) FROM items').fetchone()[0]==1
    denied=deepcopy(rows[0]);extra=json.loads(denied['extra']);extra.update(no_reprint=1,source_role='catalog_only',direct_dispatch=False)
    denied['extra']=json.dumps(extra);M.upsert_items([denied]);M.upsert_items(rows)
    with sqlite3.connect(M.DB_PATH) as c:
        after=json.loads(c.execute('SELECT extra FROM items').fetchone()[0])
    assert after['no_reprint']==1 and after['direct_dispatch'] is False


def test_transient_monitor_failure_retries_before_recording_success(monkeypatch):
    f=actual();calls=[];read=reader(f,calls);attempts=[];state={}
    def get(url,referer=None,**kw):
        attempts.append(url)
        if len(attempts)==1:raise TimeoutError('temporary request failure')
        return read(url,referer)
    monkeypatch.setattr(M,'http_get',get)
    monkeypatch.setattr(M.Source,'backoff',lambda *args:None)
    rows=M.run_source(M.P5wVideoSource,dict(seeds=[dict(url=f['page_url'])]),state,None)
    assert len(rows)==1 and len(attempts)==4
    assert state['p5w_video']['last_fetch']>0


def test_failed_monitor_requests_never_start_the_success_cooldown(monkeypatch):
    f=actual();attempts=[];state={}
    def get(*args,**kw):
        attempts.append(True);raise TimeoutError('temporary request failure')
    monkeypatch.setattr(M,'http_get',get)
    monkeypatch.setattr(M.Source,'backoff',lambda *args:None)
    assert M.run_source(M.P5wVideoSource,dict(seeds=[dict(url=f['page_url'])]),state,None)==[]
    assert len(attempts)==3 and 'last_fetch' not in state['p5w_video']


def test_partial_source_results_survive_another_seed_failure(monkeypatch):
    f=actual();info=P.extract(f['page_url'],reader(f,[]))
    def extract(url,read):
        if url!=f['page_url']:raise TimeoutError('another seed unavailable')
        return deepcopy(info)
    monkeypatch.setattr(P,'extract',extract)
    state={};rows=M.run_source(M.P5wVideoSource,dict(seeds=[dict(url=f['page_url']),
        dict(url=f['page_url'].replace('6424547','6424548'))]),state,None)
    assert len(rows)==1 and state['p5w_video']['last_fetch']>0


def test_known_denial_is_a_catalogue_result_without_retries_or_media_requests(monkeypatch):
    f=actual();f['page_excerpt']+='<p>未经授权禁止转载</p>';calls=[];read=reader(f,calls)
    monkeypatch.setattr(M,'http_get',lambda u,referer=None,**kw:read(u,referer))
    state={};rows=M.run_source(M.P5wVideoSource,dict(seeds=[dict(url=f['page_url'])]),state,None)
    assert len(rows)==1 and len(calls)==1 and state['p5w_video']['last_fetch']>0
    extra=json.loads(rows[0]['extra'])
    assert extra['no_reprint']==1 and not extra['direct_dispatch']


def test_exact_official_candidate_reaches_existing_dispatch_and_gets_only_provenance_priority(monkeypatch):
    f=actual();read=reader(f,[])
    monkeypatch.setattr(M,'http_get',lambda u,referer=None,**kw:read(u,referer))
    row=M.P5wVideoSource(dict(seeds=[dict(url=f['page_url'])]),{}).fetch(None)[0]
    spec=importlib.util.spec_from_file_location('p5w_fc_admission',Path(M.__file__).parent/'fc/index.py')
    fc=importlib.util.module_from_spec(spec);spec.loader.exec_module(fc)
    state=dict(dispatched=[],rejected=[],published={},pending_retry=[]);audit={}
    picked=fc.pick([row],state,1,audit=audit)
    assert len(picked)==1 and picked[0]['page_url']==f['page_url']
    assert fc.candidate_duration(picked[0])==3316.48
    from source_priority import publisher_priority
    assert publisher_priority(picked[0])==30
    assert publisher_priority({**picked[0],'page_url':f['page_url']+'?other=1'})==10


@pytest.mark.parametrize('duration',[3316.48,3325])
def test_normal_downloader_uses_the_video_attachment_and_checks_the_full_duration(tmp_path,monkeypatch,duration):
    f=actual();metadata=P.extract(f['page_url'],reader(f,[]))
    origin=dict(page_url=f['page_url'],media_url=metadata['public_master_url'],
        declared_duration=3316.48,provider='p5w',metadata=metadata)
    monkeypatch.setattr(P,'resolve',lambda _:deepcopy(origin));calls=[]
    media=tmp_path/'video.mp4';media.write_bytes(b'mocked media, independent media validator patched for this transport test')
    def download(command,**kw):
        calls.append(command);(tmp_path/'downloaded-path.txt').write_text(str(media)+'\n')
    monkeypatch.setattr(G,'run_download',download)
    monkeypatch.setattr(G,'validate_media',lambda _:dict(duration=duration))
    if duration!=3316.48:
        with pytest.raises(ValueError,match='全景网实际媒体时长'):G.fetch(f['page_url'],tmp_path,tmp_path/'failure.json')
        assert not (tmp_path/'source_origin.json').exists()
    else:
        assert G.fetch(f['page_url'],tmp_path,tmp_path/'failure.json')==media
        saved=json.loads((tmp_path/'source_origin.json').read_text())
        assert saved['provider']=='p5w' and saved['page_url']==f['page_url']
        assert not saved['metadata']['media_quality_approved'] and len(saved['sha256'])==64
    assert calls[0][-1]==metadata['public_master_url']
    assert calls[0][calls[0].index('--referer')+1]==f['page_url']
