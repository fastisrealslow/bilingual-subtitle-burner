import json
from pathlib import Path
import sys
import pytest
import copy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import platform_collections as c


def state():
    return {'published':{'batch':{'source_url':'https://example.com/own-source',
        'parts':[{'bvid':'BVtwo','part_index':1,'title':'林园：第二条'},
                 {'bvid':'BVone','part_index':0,'title':'林园：第一条'}]}}}


class Fake:
    csrf='test_csrf_not_a_secret'
    def __init__(self):
        self.rows=[];self.eps=[];self.calls=[];self.enabled=True;self.other=None
        self.registry=dict(version=1,owner_mid=c.OWNER_MID,collections={})
        self.checkpoints=[]
    def identity(self):return {'owner_mid':c.OWNER_MID,'logged_in':True,'season_enabled':self.enabled}
    def seasons(self):return self.rows
    def video(self,bvid):
        return dict(aid=1 if bvid=='BVone' else 2,title=bvid,pages=[{'cid':50}],
            pic='https://i0.hdslb.com/bfs/archive/test.jpg',_existing_season=self.other)
    def episodes(self,section):return list(self.eps)
    def call(self,path,*,payload=None,params=None):
        self.calls.append((path,payload,params))
        if path.endswith('/season/add'):
            assert any(v['status']=='create_requested' for v in self.registry['collections'].values())
            assert self.checkpoints, 'Creation must follow a durable reservation'
            self.rows.append({'season':{'id':9,'desc':payload['desc']},
                              'sections':{'sections':[{'id':10}]}})
            return 9
        if path.endswith('/episodes/add'):
            self.eps.extend({'aid':e['aid'],'id':e['aid']+100} for e in reversed(payload['episodes']))
        if path.endswith('/section/edit'):
            by_id={e['id']:e for e in self.eps}
            self.eps=[by_id[e['id']] for e in payload['sorts']]


def sync(fake, data=None, **options):
    return c.sync(fake, data or state(), registry=fake.registry,
                  persist_registry=lambda value: fake.checkpoints.append(copy.deepcopy(value)), **options)


def test_same_source_published_parts_only_and_stable_id():
    p=c.plans(state())[0]
    assert p['bvids']==['BVone','BVtwo']
    d=state();d['published']['batch']['parts'].append({'status':'skipped','part_index':2})
    assert c.plans(d)[0]['key']==p['key']
    d['published']['batch']['parts'][0]['part_index']=0
    assert not c.plans(d)


def test_create_add_sort_readback_and_idempotence():
    fake=Fake();r=sync(fake,apply=True)
    assert not r['errors'] and r['created']==1 and r['added']==2
    assert r['verified_collections'][0]['bvids']==['BVone','BVtwo']
    posts=[p for _,p,_ in fake.calls if p is not None]
    assert not any('copyright' in p or 'is_pay' in p for p in posts)
    r=sync(fake,apply=True)
    assert not r['errors'] and r['created']==0 and r['added']==0


def test_inspect_and_permission_gate_never_mutate():
    fake=Fake();r=c.sync(fake,state())
    assert r['plans'] and not fake.calls
    fake.enabled=False;r=sync(fake,apply=True)
    assert r['errors'][0]['error']=='account_season_permission_disabled'
    assert not fake.calls


def test_existing_foreign_collection_never_moved():
    fake=Fake();fake.other=42;r=sync(fake,apply=True)
    assert r['plans'][0]['status']=='video_already_in_other_collection'
    assert not fake.calls


def test_captcha_stops_without_retry_or_second_creation():
    fake=Fake();original=fake.call
    def blocked(path,**kw):
        if path.endswith('/season/add'):raise c.CollectionError('api_601')
        return original(path,**kw)
    fake.call=blocked;r=sync(fake,apply=True)
    assert r['errors'][0]['error']=='api_601' and not r['created']
    assert all(payload is None for _,payload,_ in fake.calls)


def test_unmanaged_episode_not_deleted_or_sorted():
    fake=Fake();sync(fake,apply=True)
    fake.eps.append({'id':999,'aid':999});fake.calls=[]
    r=sync(fake,apply=True)
    assert r['errors'][0]['error']=='unmanaged_episode_present'
    assert not any(payload is not None for _,payload,_ in fake.calls)


def test_missing_cookie_and_owner_mismatch_safe():
    with pytest.raises(c.CollectionError,match='cookies_missing_or_invalid'):c.Client('')
    client=object.__new__(c.Client)
    client.call=lambda *a,**kw:dict(isLogin=True,mid=42)
    with pytest.raises(c.CollectionError,match='login_or_owner_mismatch'):client.identity()


def test_real_empty_section_response_is_supported_without_accepting_wrong_type():
    client=object.__new__(c.Client)
    for data in ({},{'section':{'id':10},'episodes':None},{'episodes':[]}):
        client.call=lambda *a,**kw:data
        assert client.episodes(10)==[]
    client.call=lambda *a,**kw:{'episodes':{'unexpected':1}}
    with pytest.raises(c.CollectionError,match='unrecognized_episode_response'):client.episodes(10)


def test_new_public_copy_has_no_automation_marker_and_registry_survives_restart():
    fake=Fake();result=sync(fake,apply=True)
    created=next(payload for path,payload,_ in fake.calls if path.endswith('/season/add'))
    assert created['title']=='林园｜第一条'
    assert '自动' not in created['desc'] and 'ly-series:' not in created['desc']
    assert '制作' not in created['desc']
    restart=Fake();restart.rows=fake.rows;restart.eps=fake.eps
    restart.registry=copy.deepcopy(fake.checkpoints[-1])
    result=sync(restart,apply=True)
    assert result['created']==0 and result['added']==0 and not result['errors']


def test_unknown_creation_outcome_stops_future_creation_even_if_no_public_marker():
    fake=Fake();original=fake.call
    def unknown(path,**options):
        if path.endswith('/season/add'):
            original(path,**options)
            raise c.CollectionError('transport_or_decode_error')
        return original(path,**options)
    fake.call=unknown;result=sync(fake,apply=True)
    assert result['errors'][0]['error']=='transport_or_decode_error'
    fake.calls=[];fake.call=original
    result=sync(fake,apply=True)
    assert result['errors'][0]['error']=='prior_creation_outcome_unknown'
    assert not fake.calls


def test_registry_failure_stops_before_any_creation():
    fake=Fake()
    def unavailable(value):raise OSError('fixture persistence unavailable')
    result=c.sync(fake,state(),apply=True,registry=fake.registry,persist_registry=unavailable)
    assert result['errors'][0]['error']=='registry_persistence_failed'
    assert not any(payload is not None for _,payload,_ in fake.calls)


def test_old_marker_is_bound_privately_without_recreating_or_rewriting_manual_copy():
    fake=Fake();plan=c.plans(state())[0]
    fake.rows=[{'season':{'id':9,'desc':c.MARKER+plan['key']},
                'sections':{'sections':[{'id':10}]}}]
    result=sync(fake,apply=True)
    assert result['created']==0 and not result['errors']
    assert fake.registry['collections'][plan['key']]['season_id']==9
    assert not any(path.endswith('/season/add') for path,_,_ in fake.calls)


def test_foreign_binding_and_missing_registry_never_mutate():
    fake=Fake();key=c.plans(state())[0]['key']
    fake.registry['collections'][key]=dict(season_id=9,slug='wrong',source_url='other',bvids=[])
    result=sync(fake,apply=True)
    assert result['errors'][0]['error']=='registry_source_binding_mismatch' and not fake.calls
    with pytest.raises(c.CollectionError,match='durable_registry_required'):
        c.sync(Fake(),state(),apply=True)
