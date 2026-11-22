"""Unknown mother durations must reach the same bounded admission window."""
import json
from pathlib import Path
from test_linyuan_experiment_duration_probe import F, candidate, dispatch_env, row
from test_linyuan_inventory_backlog_targets import NOW, stock

def real_pool():
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_backlog_candidates_20261120.json').read_text())
    return [dict(key=r['key'],extra=dict(duration=r['duration_sec'])) for r in data['candidates']]

def test_real_unknowns_after_position_119_get_one_probe_not_an_approval(monkeypatch):
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    pool=real_pool()
    assert len(pool)==149 and sum(not F.candidate_duration(c) for c in pool)==18
    assert all(F.candidate_duration(c)>0 for c in pool[:119])
    monkeypatch.setattr(F,'pick',lambda items,state,limit,audit:pool[:limit])
    audit={}
    chosen=F.pick_for_shortfall([{}]*149,{},24,audit,True,True)
    assert len(chosen)==24 and F.candidate_duration(chosen[0])==0
    assert sum(not F.candidate_duration(c) for c in chosen)==1
    assert sum(F.complete_short_candidate(c) for c in chosen)>=2
    assert audit['dispatch_window']['selected_unknown_duration_probes']==1
    assert audit['dispatch_window']['quality_gates_relaxed'] is False

def test_exhausted_daily_probe_budget_does_not_gain_a_priority_slot(monkeypatch):
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    pool=real_pool()
    monkeypatch.setattr(F,'pick',lambda items,state,limit,audit:pool[:limit])
    state=dict(dispatched=[row('one',NOW-7200,failed=True),row('two',NOW-3700,failed=True)])
    audit={}
    chosen=F.pick_for_shortfall([{}]*149,state,24,audit,True,True)
    assert len(chosen)==24 and all(F.candidate_duration(c)>0 for c in chosen)
    assert audit['dispatch_window']['selected_unknown_duration_probes']==0

def test_known_long_supply_does_not_get_displaced_by_unknown_probe(monkeypatch):
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    pool=[candidate(i,80) for i in range(40)]+[candidate(100,1800),candidate(200)]
    monkeypatch.setattr(F,'pick',lambda items,state,limit,audit:pool[:limit])
    chosen=F.pick_for_shortfall([{}]*42,{},24,{},True,True)
    assert chosen[0]['key']=='100' and all(F.candidate_duration(c)>0 for c in chosen)

def test_unknown_probe_beyond_old_window_dispatches_without_breaching_budget(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    monkeypatch.setattr(F,'source_inventory',lambda *a:stock(900))
    candidates.extend([candidate(i,80) for i in range(40)]+[candidate(100),candidate(101)])
    result=F._dispatch_admitted(dict(execution_backend='github'))
    assert result['dispatched']==len(posts)==1
    assert posts[0]['editorial_profile']==F.experiment.TREATMENT
    assert sum(bool(e.get('experiment_duration_probe')) for e in state['dispatched'])==1
    assert state['published']=={}
    assert result['backlog_progress']['treatment']['verified']==0
