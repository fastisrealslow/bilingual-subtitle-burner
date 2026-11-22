"""Nov 20: genuine 900 C / 300 T / 10000 parent targets, bounded production."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import pytest

from test_linyuan_experiment_duration_probe import F,dispatch_env,candidate
from test_linyuan_reserve_integrity import reserve
import inventory_targets as goals
import catchup_from_inventory as catchup

NOW=datetime(2026,11,20,22,tzinfo=F.experiment.CST).timestamp()


def stock(c=147,t=0,wide=0):
    return dict(inventory_fresh=True,ordinary_ready=dict(portrait=c,landscape=0),
        experiment_ready=dict(portrait=t-wide,landscape=wide),daily_mix_usable=c+t,
        verified_portrait=c+t-wide,verified_landscape=wide)


def test_new_goals_are_disjoint_from_operating_floors_and_historical_experiment():
    assert (goals.ORDINARY_TARGET,goals.TREATMENT_TARGET,goals.SEED_TARGET)==(900,300,10000)
    assert F.TARGET_READY_RESERVE==12 and F.MAX_PUBLISH_PER_DAY==4 and F.MAX_ACTIVE_SOURCES==6
    assert F.experiment.VERSION==2026101801 and F.experiment.SELECTION_REVISION==2026102201
    assert not goals.active(datetime(2026,11,19,23,tzinfo=F.experiment.CST).timestamp())
    assert goals.active(NOW)


def test_large_ordinary_shelves_do_not_satisfy_treatment_backlog_or_seed_goal():
    row=goals.progress(stock(900,0),4841)
    assert row['ordinary']['verified']==900 and row['ordinary']['shortfall']==0
    assert row['treatment']['verified']==0 and row['treatment']['shortfall']==300
    assert row['seeds']==dict(target=10000,verified=4841,shortfall=5159)
    assert row['status']=='replenishing' and not row['in_flight_counted'] and not row['quality_gates_relaxed']
    assert goals.progress(stock(900,300,150),9999)['status']=='replenishing'
    assert goals.progress(stock(900,300,150),10000)['status']=='achieved'
    assert goals.progress(stock(900,300,149),10000)['status']=='replenishing'


@pytest.mark.parametrize('change',[dict(inventory_fresh=False),dict(ordinary_ready=None),
    dict(ordinary_ready=dict(portrait=True,landscape=0)),dict(daily_mix_usable=9999)])
def test_stale_or_unclassified_stock_never_manufactures_goal_progress(change):
    current=stock();current.update(change)
    row=goals.progress(current,4841)
    assert row['status']=='unknown'
    assert row['ordinary']['verified'] is row['treatment']['verified'] is None
    assert row['ordinary']['shortfall'] is row['treatment']['shortfall'] is None


def test_real_counter_partitions_only_existing_deduplicated_inspected_daily_files(monkeypatch):
    state,payload=reserve('pppllf',monkeypatch)
    monkeypatch.setattr(F,'inventory_publication_error',lambda *a:None)
    monkeypatch.setattr(F.experiment,'actual_profile',lambda p:
        F.experiment.TREATMENT if p.get('test_treatment') else F.experiment.BASELINE)
    for r in payload['artifacts'][3:5]:r['parts'][0]['test_treatment']=True
    actual=F.source_inventory(state,payload)
    assert actual['ordinary_ready']==dict(portrait=3,landscape=0)
    assert actual['experiment_ready']==dict(portrait=0,landscape=2)
    assert actual['daily_mix_usable']==5 and actual['verified_weekly_full']==1
    # Same actual file cannot grow either bucket twice.
    payload['artifacts'][1]['parts'][0]['sha256']=payload['artifacts'][0]['parts'][0]['sha256']
    actual=F.source_inventory(state,payload)
    assert sum(actual['ordinary_ready'].values())==2
    assert sum(actual['ordinary_ready'].values())+sum(actual['experiment_ready'].values())==actual['daily_mix_usable']


def test_new_goals_keep_refilling_after_old_12_file_floor_without_invoking_fc(monkeypatch):
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    monkeypatch.setattr(F,'catchup_deficit',lambda _:0)
    current=stock(147,4,2)
    assert F.reserve_deficits(current)==dict(portrait=0,landscape=0)
    assert F.replenishment_deficits(current)==dict(portrait=753,landscape=0)
    assert F.treatment_replenishment_deficits(current,dict(dispatched=[]),[])==dict(landscape=148,portrait=148)
    assert catchup.inventory_action(dict(dispatched=[]),current)=='dispatch-source-inventory'
    assert F.replenishment_deficits(stock(900,300,150))==dict(portrait=0,landscape=0)
    assert not any(F.treatment_replenishment_deficits(stock(900,300,150),dict(dispatched=[]),[]).values())


def test_active_jobs_only_reserve_opportunities_and_never_count_as_300_passed(monkeypatch):
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    state=dict(dispatched=[dict(slug='t',editorial_profile=F.experiment.TREATMENT,output_layout='landscape')])
    current=stock();before=deepcopy(current)
    needs=F.treatment_replenishment_deficits(current,state,[dict(status='in_progress',display_title='中文源出片 · t')])
    assert needs==dict(landscape=149,portrait=150)
    assert current==before and goals.progress(current)['treatment']['verified']==0


def test_actual_149_candidates_offer_36_short_opportunities_within_same_24_attempts(monkeypatch):
    fixture=json.loads((Path(__file__).parent/'fixtures/linyuan_backlog_candidates_20261120.json').read_text())
    pool=[dict(key=r['key'],extra=dict(duration=r['duration_sec'])) for r in fixture['candidates']]
    assert len(pool)==149 and sum(F.complete_short_candidate(c) for c in pool)==36
    assert sum(F.complete_short_candidate(c) for c in pool[:24])==2
    calls=[]
    def pick(items,state,limit,audit):calls.append(limit);return pool[:limit]
    monkeypatch.setattr(F,'pick',pick)
    audit={};selected=F.pick_for_shortfall([{}]*149,{},24,audit,True,True)
    assert len(selected)==24 and calls==[149]
    assert sum(F.complete_short_candidate(c) for c in selected)>=2
    assert audit['dispatch_window']['quality_gates_relaxed'] is False
    assert not any(c.get('media_verified') for c in selected)


def test_short_opportunities_cannot_displace_required_long_mothers(monkeypatch):
    pool=[candidate(i,80) for i in range(30)]+[candidate(i+100,1800) for i in range(24)]
    monkeypatch.setattr(F,'pick',lambda items,state,limit,audit:pool[:limit])
    selected=F.pick_for_shortfall([{}]*54,{},24,{},True,True)
    assert len(selected)==24 and all(F.experiment.long_candidate(c) for c in selected)


def test_known_80_second_sources_actually_dispatch_for_new_ordinary_goal_without_fake_t(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    current=stock();monkeypatch.setattr(F,'source_inventory',lambda *a:current)
    candidates.extend(candidate(i,80) for i in range(10))
    before=deepcopy(current)
    result=F._dispatch_admitted(dict(execution_backend='github'))
    assert result['dispatched']==len(posts)==4  # Two of the existing six places stay available to T.
    assert all(p['output_layout']=='auto' and 'editorial_profile' not in p for p in posts)
    assert current==before and state['published']=={} and state['rejected']==[]
    assert result['backlog_progress']['ordinary']['verified']==147
    assert result['backlog_progress']['treatment']['verified']==0


def test_long_t_and_short_c_share_existing_six_worker_budget(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    monkeypatch.setattr(F,'source_inventory',lambda *a:stock())
    candidates.extend([candidate(100,1800),candidate(101,1800)]+[candidate(i,80) for i in range(10)])
    result=F._dispatch_admitted(dict(execution_backend='github'))
    assert result['dispatched']==len(posts)==6
    assert sum(p.get('editorial_profile')==F.experiment.TREATMENT for p in posts)==2
    assert sum(p['output_layout']=='auto' for p in posts)==4
    assert state['published']=={}


def test_full_ordinary_backlog_does_not_launch_short_c_for_missing_t(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    monkeypatch.setattr(F,'source_inventory',lambda *a:stock(900))
    candidates.extend(candidate(i,80) for i in range(10))
    result=F._dispatch_admitted(dict(execution_backend='github'))
    assert result['dispatched']==0 and posts==[]


def test_four_active_ordinary_jobs_preserve_remaining_two_for_t(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    monkeypatch.setattr(F,'source_inventory',lambda *a:stock())
    for i in range(4):
        slug=f'active-c-{i}'
        state['dispatched'].append(dict(slug=slug,editorial_profile=F.experiment.BASELINE))
        runs.append(dict(status='in_progress',display_title=f'中文源出片 · {slug}'))
    candidates.extend([candidate(i,80) for i in range(8)]+[candidate(100,1800),candidate(101,1800)])
    result=F._dispatch_admitted(dict(execution_backend='github'))
    assert result['dispatched']==len(posts)==2
    assert all(p.get('editorial_profile')==F.experiment.TREATMENT for p in posts)


@pytest.mark.parametrize('hour',[10,16])
def test_verified_native_wide_controls_can_fill_controls_but_not_steal_t_slots(monkeypatch,hour):
    monkeypatch.setattr(F.experiment,'actual_profile',lambda _:F.experiment.BASELINE)
    clip=dict(render_mode='crop_delogo',resolution=dict(width=1280,height=720),duration_sec=80)
    at=lambda day,h:datetime(2026,11,day,h,tzinfo=F.experiment.CST).timestamp()
    assert not F.content_fits_slot(clip,{},at(19,hour))
    assert F.content_fits_slot(clip,{},at(20,hour))
    assert not F.content_fits_slot(clip,{},at(20,14))
    assert not F.content_fits_slot(clip,{},at(20,21))
    assert not F.content_fits_slot({**clip,'render_mode':'audio_card'},{},at(20,hour))
