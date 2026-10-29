"""A 2000-entry directory is a floor, never proof of ongoing T supply."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import seed_growth as G
from fc import index as F


@pytest.fixture
def base(tmp_path,monkeypatch):
    for name,key in [('douyin','urls'),('weibo','urls'),('haokan','vids'),('netease','vcodes'),('yicai','ids')]:
        (tmp_path/(name+'_seeds.json')).write_text(json.dumps({key:[]}))
    (tmp_path/'up_videos.json').write_text('{}')
    (tmp_path/'monitor_v2_config.json').write_text(json.dumps([dict(
        type='bilibili_collection',seeds_file='bilibili_seeds.json')]))
    (tmp_path/'bilibili_seeds.json').write_text(json.dumps(dict(seeds=[dict(bvid='BV0000000000')])))
    (tmp_path/'.automation').mkdir()
    (tmp_path/'.automation/fc_state.json').write_text('{"dispatched":[],"published":{}}')
    (tmp_path/'.automation/source_inventory.json').write_text('{"source_admission":{"candidates":[]}}')
    monkeypatch.setattr(G.time,'time',lambda:1793196000)
    monkeypatch.setattr(G.time,'sleep',lambda _:None)
    monkeypatch.setattr(F,'source_inventory',lambda *a:dict(inventory_fresh=True,
        experiment_ready=dict(landscape=0,portrait=0)))
    monkeypatch.setattr(G,'catalog_leads',lambda _:[dict(bvid=f'BV{i:010d}',catalog_duration=1800) for i in range(1,13)])
    return tmp_path


def verified(lead,**changes):
    return dict(lead,status='verified_metadata',title='林园完整访谈：投资经历',
        observed_author='真实上传者',owner_mid=123,no_reprint=0,
        pages=[dict(page=1,cid=int(lead['bvid'][2:])+1,duration=1800)],**changes)


def test_reached_catalogue_floor_still_adds_four_long_candidates_at_most_eight_per_day(base):
    calls=[]
    def verify(lead):calls.append(lead['bvid']);return verified(lead)
    first=G.run(base,target=1,limit=48,search=False,verify=verify,replenish_long=True)
    assert first['long_replenishment'] and first['target_is_minimum']
    assert first['added_count']==4 and first['after']['total']==5
    second=G.run(base,target=1,limit=48,search=False,verify=verify,replenish_long=True)
    assert second['added_count']==4 and second['after']['total']==9
    before=len(calls)
    third=G.run(base,target=1,limit=48,search=False,verify=verify,replenish_long=True)
    assert third['added_count']==0 and len(calls)==before
    assert first['accepted_mp4_count']==second['accepted_mp4_count']==0
    assert first['production_dispatch'] is second['production_dispatch'] is False
    seeds=json.loads((base/'bilibili_seeds.json').read_text())['seeds'][1:]
    assert all(e['direct_dispatch'] and not e['media_verified'] and not e['editorial_approved'] for e in seeds)


def test_denied_short_unknown_rights_and_restricted_programmes_do_not_expand_catalogue_above_floor(base,monkeypatch):
    monkeypatch.setattr(G,'catalog_leads',lambda _:[dict(bvid=f'BV{i:010d}',catalog_duration=1800) for i in range(1,6)])
    def verify(lead):
        row=verified(lead);number=int(lead['bvid'][2:])
        if number==1:row['no_reprint']=1
        if number==2:row['no_reprint']=None
        if number==3:row['pages'][0]['duration']=120
        if number==4:row.update(title='2026.10.15林园专访',published_at='2026-10-15T00:00:00+00:00')
        return row
    report=G.run(base,target=1,search=False,verify=verify,replenish_long=True)
    assert report['added_count']==1 and report['after']['total']==2
    rows=json.loads((base/'bilibili_seeds.json').read_text())['seeds']
    assert rows[-1]['bvid']=='BV0000000005'


@pytest.mark.parametrize('stock',[
    dict(inventory_fresh=False,experiment_ready=dict(landscape=0,portrait=0)),
    dict(inventory_fresh=True,experiment_ready=dict(landscape=4,portrait=0)),
])
def test_unknown_inventory_or_four_actual_trials_do_not_create_more_search_load(base,monkeypatch,stock):
    monkeypatch.setattr(F,'source_inventory',lambda *a:stock)
    def forbidden(*a):raise AssertionError('No discovery without a proven long-source need')
    monkeypatch.setattr(G,'catalog_leads',forbidden)
    report=G.run(base,target=1,verify=forbidden,replenish_long=True)
    assert report['added_count']==0


def test_six_waiting_long_sources_or_absent_admission_evidence_do_not_expand_again(base):
    p=base/'.automation/source_inventory.json'
    for admission in (dict(candidates=[dict(duration_sec=1800)]*6),{}):
        p.write_text(json.dumps(dict(source_admission=admission)))
        assert G.long_replenishment_budget(base)==0
