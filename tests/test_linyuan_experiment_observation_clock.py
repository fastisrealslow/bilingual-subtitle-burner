import copy
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
    return dict(bvid=BVID,pubdate=PUB,play=play,observed_at=observed,observation_source='bilibili_public_view')


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
    return dict(code=0,data=dict(bvid=BVID,state=0,owner=dict(mid=C.OWNER_MID),pubdate=PUB,stat=dict(view=play)))


@pytest.mark.parametrize('play',[None,True,-1,'100'])
def test_public_counter_parser_does_not_invent_zero(play):
    with pytest.raises(ValueError):C.parse_view(payload(play),BVID)


def test_public_counter_requires_exact_owner_video_and_public_state():
    assert C.parse_view(payload(0),BVID)['play']==0
    for field,value in [('bvid','BVother'),('state',1),('owner',dict(mid=1))]:
        body=payload();body['data'][field]=value
        with pytest.raises(ValueError):C.parse_view(body,BVID)


@pytest.mark.parametrize('body',[[],dict(code=0,data=['bad']),dict(code=0,data={})])
def test_malformed_public_response_is_unknown_not_zero(body):
    with pytest.raises(ValueError):C.parse_view(body,BVID)


def test_unavailable_counter_retains_data_and_fails_loudly(tmp_path,monkeypatch):
    (tmp_path/'site').mkdir();(tmp_path/'linyuan/.automation').mkdir(parents=True)
    out=tmp_path/'site/stats.json';out.write_text(json.dumps(dict(peers=['keep existing metrics'])))
    (tmp_path/'linyuan/.automation/fc_state.json').write_text(json.dumps(state()))
    monkeypatch.setattr(C,'ROOT',tmp_path);monkeypatch.setattr(C.time,'time',lambda:NOW)
    monkeypatch.setattr(sys,'argv',['collector'])
    def unavailable(bvid):raise ValueError('not a public counter')
    monkeypatch.setattr(C,'fetch_view',unavailable)
    with pytest.raises(RuntimeError,match='prior observations retained'):C.main()
    report=json.loads(out.read_text())
    assert report['peers']==['keep existing metrics']
    assert report['publishing_experiment']['clock_samples']==[]
    assert report['publishing_experiment']['sampling_health']['fetched']==0
    assert report['publishing_experiment']['sampling_health']['missing_is_zero'] is False
