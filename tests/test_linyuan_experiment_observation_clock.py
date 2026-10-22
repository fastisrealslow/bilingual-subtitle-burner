import copy
import io
import json
from datetime import datetime
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import collect_experiment_stats as C
import publishing_experiment as E

PUB=datetime(2026,10,23,14,tzinfo=E.CST).timestamp()
NOW=PUB+24.5*3600
BVID='BV1rLnC6CEyx'


def state(actual=E.TREATMENT,content_type='short_view'):
    receipt=dict(E.assignment(PUB),actual=actual,compliant=actual==E.TREATMENT,content_type=content_type)
    return dict(dispatched=[],published=dict(mother=dict(parts=[dict(bvid=BVID,ts=PUB,
        publishing_experiment=receipt,title='actual receipt')])) )


def item(play=100,observed=NOW):
    return dict(bvid=BVID,pubdate=PUB,play=play,observed_at=observed,observation_source='bilibili_creator_archive',publication_clock_basis='creator_pubdate')


def treatment(result,window=24):
    return next(g for g in result['groups'] if g['planned']==E.TREATMENT and g['window']==window)


@pytest.mark.parametrize('window',[24,72,168])
def test_actual_target_clock_first_observation_is_preserved(window):
    now=PUB+(window+.5)*3600;s=state();before=copy.deepcopy(s)
    first=C.reconcile([item(observed=now)],{},s,now)
    assert treatment(first,window)['mean']==100 and len(first['clock_samples'])==1
    second=C.reconcile([item(999,now+3600)],dict(publishing_experiment=first),s,now+3600)
    assert treatment(second,window)['mean']==100
    assert second['clock_samples'][0]['observed_at']==now and s==before
    assert second['experiment_id']==E.EXPERIMENT_ID and second['start']==E.START


def test_legacy_samples_and_old_groups_are_not_erased_or_relabelled():
    row=dict(bvid=BVID,window=24,observed_at=NOW-1,age_hours=35,play=80,
        planned=E.TREATMENT,actual=E.TREATMENT,compliant=True,slot_number=2)
    previous=dict(publishing_experiment=dict(samples=[row]));before=copy.deepcopy(previous)
    result=C.reconcile([item()],previous,state(),NOW)
    assert result['samples']==[row] and previous==before
    assert next(g for g in result['legacy_groups'] if g['planned']==E.TREATMENT and g['window']==24)['mean']==80
    assert treatment(result)['mean']==100 and result['clock_samples'][0]['age_hours']==24.5
    assert C.due_receipts(previous,state(),NOW)==[BVID]
    assert C.due_receipts(dict(publishing_experiment=result),state(),NOW)==[]


def test_other_experiment_clock_rows_stay_archived_not_compared():
    first=C.reconcile([item()],{},state(),NOW)
    first['clock_samples'][0]['experiment_id']='other-experiment'
    result=C.reconcile([],dict(publishing_experiment=first),state(),NOW)
    assert len(result['clock_samples'])==1 and treatment(result)['count']==0
    measured=C.reconcile([item()],dict(publishing_experiment=first),state(),NOW)
    assert len(measured['clock_samples'])==2 and treatment(measured)['mean']==100


@pytest.mark.parametrize('changes',[
    dict(play=None),dict(play=True),dict(play=-1),dict(play='100'),dict(pubdate=None),
    dict(observed_at=NOW+1),dict(observation_source='search_index'),dict(bvid='BVunknown'),
    dict(publication_clock_basis='upload_receipt'),
])
def test_unknown_unbound_future_or_index_counts_are_not_measurements(changes):
    value=item();value.update(changes)
    result=C.reconcile([value],{},state(),NOW)
    assert result['clock_samples']==[] and treatment(result)['mean'] is None


def test_zero_is_a_real_counter_not_a_missing_value_default():
    assert treatment(C.reconcile([item(0)],{},state(),NOW))['mean']==0


def test_late_views_cannot_backfill_target_and_each_fetch_uses_its_actual_clock():
    late=PUB+26.1*3600
    assert C.reconcile([item(observed=late)],{},state(),late)['clock_samples']==[]
    inside=PUB+25.99*3600;finished=inside+120
    result=C.reconcile([item(observed=inside)],{},state(),finished)
    assert len(result['clock_samples'])==1 and result['clock_samples'][0]['observed_at']==inside
    assert C.reconcile([item()],{},state(),NOW+601)['clock_samples']==[]


def test_fallbacks_full_interviews_and_legacy_zero_do_not_enter_primary_groups():
    fallback=C.reconcile([item()],{},state(E.BASELINE),NOW)
    assert len(fallback['clock_samples'])==1 and treatment(fallback)['mean'] is None
    full=C.reconcile([item()],{},state(content_type='full_interview'),NOW)
    assert treatment(full)['mean'] is None
    retained=C.reconcile([],dict(publishing_experiment=fallback),state(E.BASELINE),NOW)
    assert retained['clock_samples']==fallback['clock_samples']


def payload(play=100):
    return dict(bvid=BVID,state=0,mid=C.OWNER_MID,pubdate=PUB,statistics=dict(view=play),
        public=True,observed_at=NOW,statistics_origin='authenticated-creator-read')


@pytest.mark.parametrize('play',[None,True,-1,'100'])
def test_public_counter_parser_does_not_invent_zero(play):
    with pytest.raises(ValueError):C.parse_creator(payload(play),BVID)


def test_public_counter_requires_exact_owner_video_and_public_state():
    assert C.parse_creator(payload(0),BVID)['play']==0
    for field,value in [('bvid','BVother'),('state',1),('mid',1),('public',False)]:
        body=payload();body[field]=value
        with pytest.raises(ValueError):C.parse_creator(body,BVID)


@pytest.mark.parametrize('body',[[],dict(code=0,data=['bad']),dict(code=0,data={})])
def test_malformed_public_response_is_unknown_not_zero(body):
    with pytest.raises(ValueError):C.parse_creator(body,BVID)


def test_verified_creator_ptime_not_creation_or_zero_online_time():
    body=payload();del body['pubdate']
    body['publication_clock_candidates']=dict(ptime=PUB,ctime=PUB-62,online_time=0)
    result=C.parse_creator(body,BVID)
    assert result['pubdate']==PUB and result['publication_clock_basis']=='creator_ptime'
    body['publication_clock_candidates']=dict(ctime=PUB-62)
    with pytest.raises(ValueError):C.parse_creator(body,BVID)
    body['pubdate']=PUB;body['publication_clock_candidates']=dict(ptime=PUB-62)
    with pytest.raises(ValueError,match='conflict'):C.parse_creator(body,BVID)


def test_unavailable_counter_retains_data_and_fails_loudly(tmp_path,monkeypatch):
    (tmp_path/'site').mkdir();(tmp_path/'linyuan/.automation').mkdir(parents=True)
    out=tmp_path/'site/stats.json';out.write_text(json.dumps(dict(peers=['keep existing metrics'])))
    (tmp_path/'linyuan/.automation/fc_state.json').write_text(json.dumps(state()))
    monkeypatch.setattr(C,'ROOT',tmp_path);monkeypatch.setattr(C.time,'time',lambda:NOW)
    monkeypatch.setattr(sys,'argv',['collector'])
    def unavailable(bvids):raise ValueError('not an authenticated own-video counter')
    monkeypatch.setattr(C,'fetch_views',unavailable)
    with pytest.raises(RuntimeError,match='prior observations retained'):C.main()
    report=json.loads(out.read_text())
    assert report['peers']==['keep existing metrics']
    assert report['publishing_experiment']['clock_samples']==[]
    assert report['publishing_experiment']['sampling_health']['fetched']==0
    assert report['publishing_experiment']['sampling_health']['missing_is_zero'] is False


def test_batch_reader_uses_existing_authorized_owner_and_keeps_unknown_unknown(monkeypatch):
    monkeypatch.setenv('BILIBILI_COOKIES','unit-test-placeholder')
    def read(ids,owner,cookies,include_statistics=False):
        assert ids==[BVID] and owner==C.OWNER_MID and cookies=='unit-test-placeholder' and include_statistics
        return dict(videos=[payload(0)])
    monkeypatch.setattr(C,'archive_status',read)
    items,errors=C.fetch_views([BVID])
    assert not errors and items[0]['play']==0 and items[0]['observation_source']=='bilibili_creator_archive'
    monkeypatch.setattr(C,'archive_status',lambda *a,**kw:dict(videos=[],api_code=-101))
    items,errors=C.fetch_views([BVID])
    assert not items and errors[0]['api_code']==-101


def test_optional_creator_statistics_keep_existing_visibility_contract(monkeypatch):
    import bili_archive_status as A
    archive=dict(bvid=BVID,mid=C.OWNER_MID,state=0,is_only_self=0,no_public=0,pubdate=PUB,ctime=PUB-100)
    response=dict(code=0,data=dict(arc_audits=[dict(Archive=archive,stat=dict(view=0,like=1,danmaku=0))]))
    class Reply(io.StringIO):pass
    class Opener:
        def open(self,url,timeout):
            assert url.startswith('https://member.bilibili.com/x/web/archives?')
            return Reply(json.dumps(response))
    monkeypatch.setattr(A.urllib.request,'build_opener',lambda *a:Opener())
    monkeypatch.setattr(A.time,'time',lambda:NOW)
    cookies=json.dumps(dict(cookie_info=dict(cookies=[dict(name='DedeUserID',value=str(C.OWNER_MID)),
        dict(name='SESSDATA',value='unit-test-placeholder')])))
    original=A.archive_status([BVID],C.OWNER_MID,cookies)
    assert original['public_count']==1 and 'statistics' not in original['videos'][0]
    enriched=A.archive_status([BVID],C.OWNER_MID,cookies,include_statistics=True)
    assert enriched['public_count']==1 and C.parse_creator(enriched['videos'][0],BVID)['play']==0
    del archive['pubdate']
    missing=A.archive_status([BVID],C.OWNER_MID,cookies,include_statistics=True)
    with pytest.raises(ValueError):C.parse_creator(missing['videos'][0],BVID)
