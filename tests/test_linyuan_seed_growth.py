import copy
import json
from pathlib import Path
import sqlite3
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import monitor_v2 as monitor
from seed_growth import admit_verified, existing_roots, catalog_leads, run


def evidence(bvid='BV1NpBFYkEKx', **overrides):
    return dict(bvid=bvid, status='verified_metadata', title='林园：不加杠杆，2020.04.12访谈',
        observed_author='实际上传者', owner_mid=123, no_reprint=0,
        pages=[dict(page=1,cid=100,duration=1800),dict(page=2,cid=200,duration=1200)],
        discovery='normal_public_search', media_verified=True, editorial_approved=True, **overrides)


def test_parent_dedup_caps_count_and_never_carries_media_approvals():
    rows = [evidence(), evidence(), evidence('BV1LQVc6eEFu')]
    data = dict(seeds=[])
    added = admit_verified(data, rows, set(), 1)
    assert len(added) == 1 and len(added[0]['pages']) == 2
    assert not added[0]['media_verified'] and not added[0]['editorial_approved']
    assert added[0]['content_date_hint'] == '2020-04-12'
    assert added[0]['content_date_hint_provenance'] == 'uploader_title_unverified'
    assert admit_verified(data, rows, {r['bvid'] for r in data['seeds']}, 0) == []


@pytest.mark.parametrize('changes', [dict(status='not_verified'), dict(owner_mid=1700344493),
    dict(observed_author='园园滚雪球'),dict(title='红枫林园最新视频'),dict(title='林园群采访')])
def test_bad_or_reference_leads_never_enter_seeds(changes):
    row = evidence(); row.update(changes)
    assert admit_verified(dict(seeds=[]), [row], set(), 10) == []


def test_known_denial_and_weak_identity_are_not_dispatchable():
    denied = evidence(); denied['no_reprint'] = 1
    weak = evidence('BV1LQVc6eEFu'); weak['title'] = '林园经典-2018.05.05-奥马哈之行'
    added = admit_verified(dict(seeds=[]), [denied, weak], set(), 10)
    assert len(added) == 2 and all(r['direct_dispatch'] is False for r in added)


def test_restriction_survives_another_route_or_page_without_changing_claims(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, 'DB_PATH', tmp_path/'catalog.db'); monitor.init_db()
    bvid = 'BV1NpBFYkEKx'
    def row(suffix, extra):
        return dict(id='bilibili_search:'+bvid+suffix, source='bilibili_search', title='林园完整访谈',
            url='https://www.bilibili.com/video/'+bvid, publish_time='',author='实际作者',
            extra=json.dumps(dict(bvid=bvid, **extra)))
    original = row('', dict(no_reprint=1, content_date_hint='2020-04-12',
        content_date_hint_provenance='uploader_title_unverified'))
    monitor.upsert_items([original])
    updated = row(':p2', dict(source_role='mother_candidate', direct_dispatch=True, no_reprint=0))
    monitor.upsert_items([updated])
    with sqlite3.connect(monitor.DB_PATH) as conn:
        encoded, title = conn.execute('SELECT extra, title FROM items WHERE id=?',(updated['id'],)).fetchone()
    extra = json.loads(encoded)
    assert extra['no_reprint'] == 1 and extra['direct_dispatch'] is False
    assert extra['content_date_hint'] == '2020-04-12' and title == updated['title']
    assert updated['extra'] == row(':p2', dict(source_role='mother_candidate', direct_dispatch=True, no_reprint=0))['extra']


def test_missing_author_can_be_resolved_but_known_research_policy_stays_closed():
    old = json.dumps(dict(source_role='catalog_only', direct_dispatch=False, metadata_status='needs_author_verification'))
    fresh = json.dumps(dict(source_role='mother_candidate', direct_dispatch=True, metadata_status='known_duration'))
    assert json.loads(monitor.retain_source_restrictions(fresh, [old]))['direct_dispatch']
    old = json.dumps(dict(source_role='catalog_only', direct_dispatch=False, metadata_status='known_duration'))
    assert not json.loads(monitor.retain_source_restrictions(fresh, [old]))['direct_dispatch']


def test_seed_denial_updates_all_cached_parts_without_waiting_for_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, 'DB_PATH', tmp_path/'catalog.db'); monitor.init_db()
    root = 'BV1NpBFYkEKx'
    for suffix in ('', ':p2'):
        monitor.upsert_items([dict(id='bilibili_search:'+root+suffix,source='bilibili_series',
            title='林园完整访谈',url='https://www.bilibili.com/video/'+root, publish_time='',author='实际作者',
            extra=json.dumps(dict(bvid=root,direct_dispatch=True)))])
    (tmp_path/'bilibili_seeds.json').write_text(json.dumps(dict(seeds=[dict(bvid=root,no_reprint=1)])))
    assert monitor.enforce_seed_restrictions(tmp_path) == 2
    assert monitor.enforce_seed_restrictions(tmp_path) == 0
    with sqlite3.connect(monitor.DB_PATH) as conn:
        assert all(json.loads(r[0])['direct_dispatch'] is False for r in conn.execute('SELECT extra FROM items'))


def test_corrupt_restriction_metadata_fails_closed():
    with pytest.raises(ValueError):
        monitor.retain_source_restrictions('{}', ['invalid json'])


def test_campaign_stops_at_target_without_network_or_media_work(monkeypatch):
    import seed_growth
    monkeypatch.setattr(seed_growth, 'census', lambda _: dict(total=1000))
    monkeypatch.setattr(seed_growth, 'existing_roots', lambda _: set())
    def prohibited(*a, **kw): raise AssertionError('No discovery above target')
    monkeypatch.setattr(seed_growth, 'catalog_leads', prohibited)
    report = run(Path('/not-used'), verify=prohibited)
    assert report['added_count'] == 0 and report['accepted_mp4_count'] == 0
    assert report['production_dispatch'] is False


def test_actual_campaign_persists_only_verified_parent_with_target_bound(tmp_path, monkeypatch):
    import seed_growth
    for name, key in [('douyin','urls'),('weibo','urls'),('haokan','vids'),('netease','vcodes'),('yicai','ids')]:
        (tmp_path/(name+'_seeds.json')).write_text(json.dumps({key: []}))
    (tmp_path/'up_videos.json').write_text('{}')
    existing = 'BV1NpBFYkEKx'
    (tmp_path/'bilibili_seeds.json').write_text(json.dumps(dict(seeds=[dict(bvid=existing)])))
    (tmp_path/'monitor_v2_config.json').write_text(json.dumps([dict(type='bilibili_collection',seeds_file='bilibili_seeds.json')]))
    monkeypatch.setattr(monitor, 'DB_PATH', tmp_path/'monitor_v2.db'); monitor.init_db()
    monkeypatch.setattr(seed_growth.time, 'sleep', lambda _: None)
    for bvid in ('BV1LQVc6eEFu', 'BV1Nfji64E9e'):
        monitor.upsert_items([dict(id='bilibili_search:'+bvid,source='bilibili_series',title='林园完整访谈',
            url='https://www.bilibili.com/video/'+bvid,publish_time='',author='真实作者',
            extra=json.dumps(dict(bvid=bvid,duration=1800)))])
    def fake_verify(lead):
        result = evidence(lead['bvid']);result.update(title='林园：投资不加杠杆')
        return result
    report = run(tmp_path, target=2, limit=2, search=False, verify=fake_verify)
    assert report['baseline']['total'] == 1 and report['after']['total'] == 2
    assert report['added_count'] == 1 and report['accepted_mp4_count'] == 0
    assert len(json.loads((tmp_path/'bilibili_seeds.json').read_text())['seeds']) == 2
    assert (tmp_path/'.automation/seed_growth_scan.json').exists()


@pytest.mark.parametrize('restriction', [dict(http_status=412),dict(http_status=429),dict(api_code=-412)])
@pytest.mark.parametrize('initial_success',[False,True])
def test_provider_restriction_pauses_requests_and_preserves_the_unchecked_tail(tmp_path, monkeypatch, restriction, initial_success):
    import seed_growth as G
    monkeypatch.setattr(G,'census',lambda _:dict(total=1))
    monkeypatch.setattr(G,'existing_roots',lambda _:set())
    monkeypatch.setattr(G.time,'sleep',lambda _:None)
    clock=[1000]
    monkeypatch.setattr(G.time,'time',lambda:clock[0])
    ids=sorted(['BV1NpBFYkEKx','BV1LQVc6eEFu','BV1Nfji64E9e'])
    leads=[dict(bvid=b,catalog_duration=1800) for b in ids]
    monkeypatch.setattr(G,'catalog_leads',lambda _:leads)
    (tmp_path/'bilibili_seeds.json').write_text('{"seeds":[]}')
    requests=[]
    def blocked(lead):
        requests.append(lead['bvid'])
        if initial_success and len(requests)==1:return evidence(lead['bvid'])
        return dict(lead,status='not_verified',error='HTTPError',**restriction)
    result=G.run(tmp_path,target=1500,limit=48,search=False,verify=blocked)
    consumed=1+int(initial_success)
    assert requests==ids[:consumed] and result['added_count']==int(initial_success)
    assert result['verification_paused']['until']==4600
    path=tmp_path/'.automation/seed_growth_scan.json'
    scan=json.loads(path.read_text());assert scan['parents']['cursor']==consumed
    def forbidden(*a,**kw):raise AssertionError('No provider request during persisted cooldown')
    monkeypatch.setattr(G,'catalog_leads',forbidden)
    assert G.run(tmp_path,target=1500,search=True,verify=forbidden)['verification_paused']['until']==4600
    assert json.loads(path.read_text())==scan
    clock[0]=4601;monkeypatch.setattr(G,'catalog_leads',lambda _:leads)
    def unavailable(lead):
        requests.append(lead['bvid'])
        return dict(lead,status='not_verified',error='ValueError',api_code=62002)
    resumed=G.run(tmp_path,target=1500,limit=len(ids)-consumed,search=False,verify=unavailable)
    assert requests==ids and resumed['verification']=={'not_verified':len(ids)-consumed}
    assert 'verification_paused' not in resumed
    assert len(json.loads((tmp_path/'bilibili_seeds.json').read_text())['seeds'])==int(initial_success)
