"""Short deliveries must not starve intact >=15min ordinary mothers."""
import json
from pathlib import Path
from test_linyuan_experiment_duration_probe import F,candidate,dispatch_env
from test_linyuan_inventory_backlog_targets import NOW,stock

def test_actual_149_pool_reserves_two_longer_ordinary_mothers_alongside_two_short_opportunities(monkeypatch):
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    fixture=json.loads((Path(__file__).parent/'fixtures/linyuan_backlog_candidates_20261120.json').read_text())
    pool=[dict(key=r['key'],extra=dict(duration=r['duration_sec'])) for r in fixture['candidates']]
    assert sum(900<=F.candidate_duration(c)<1200 for c in pool)==9
    monkeypatch.setattr(F,'pick',lambda items,state,limit,audit:pool[:limit])
    audit={}
    chosen=F.pick_for_shortfall([{}]*149,{},24,audit,True,True)
    assert len(chosen)==24
    assert audit['dispatch_window']['selected_ordinary_mothers']==2
    assert audit['dispatch_window']['priority_short_opportunities']==2
    assert sum(900<=F.candidate_duration(c)<1200 for c in chosen)>=2
    assert sum(F.complete_short_candidate(c) for c in chosen)>=2

def test_two_longer_ordinary_mothers_actually_dispatch_instead_of_all_four_short(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    monkeypatch.setattr(F,'source_inventory',lambda *a:stock())
    candidates.extend([candidate(i,80) for i in range(40)]+[candidate(100,1132),candidate(101,1142)])
    result=F._dispatch_admitted(dict(execution_backend='github'))
    assert result['dispatched']==len(posts)==4
    assert [p['source'].rsplit('/',1)[-1] for p in posts[:2]]==['100','101']
    assert all('editorial_profile' not in p for p in posts)
    assert state['published']=={} and result['backlog_progress']['ordinary']['verified']==147

def test_known_required_story_mothers_keep_precedence_over_ordinary_mix(monkeypatch):
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    pool=[candidate(i,80) for i in range(40)]+[candidate(100,1132)]+[candidate(i+200,1800) for i in range(24)]
    monkeypatch.setattr(F,'pick',lambda items,state,limit,audit:pool[:limit])
    chosen=F.pick_for_shortfall([{}]*len(pool),{},24,{},True,True)
    assert len(chosen)==24 and all(F.experiment.long_candidate(c) for c in chosen)
