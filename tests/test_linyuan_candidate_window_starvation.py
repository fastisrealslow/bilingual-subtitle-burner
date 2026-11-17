"""Nov 17: 16 known long sources at positions 69+ never entered 24 attempts."""
from copy import deepcopy
import json
from pathlib import Path

from test_linyuan_experiment_duration_probe import F,dispatch_env,candidate
from test_linyuan_treatment_only_admission import full_stock


def test_long_candidate_beyond_attempt_cap_enters_actual_dispatch(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env;full_stock(monkeypatch,state)
    candidates.extend(candidate(i,80) for i in range(F.MAX_ATTEMPTS+1))
    candidates.append(candidate(999,1800))
    old=F.gh
    def gh(method,path,*args,**kwargs):
        if method=='GET' and F.DATA_JSON in path:return json.dumps([{}]*len(candidates)).encode()
        return old(method,path,*args,**kwargs)
    monkeypatch.setattr(F,'gh',gh)
    def pick(items,st,limit,audit):
        audit.update(records=len(items),candidate_count=len(candidates))
        return candidates[:limit]
    monkeypatch.setattr(F,'pick',pick)
    result=F._dispatch_admitted({'execution_backend':'github'})
    assert result['dispatched']==1 and len(posts)==1
    assert posts[0]['source'].endswith('/999')
    assert posts[0]['editorial_profile']==F.experiment.TREATMENT
    assert result['candidate_window']['selected_known_long']==1
    assert not state['published'] and not state['rejected']


def test_actual_inventory_order_recovers_all_sixteen_without_expanding_attempts(monkeypatch):
    fixture=json.loads((Path(__file__).parent/'fixtures/linyuan_candidate_window_20261117.json').read_text())
    pool=[dict(key=c['key'],extra=dict(duration=c['duration_sec'])) for c in fixture['candidates']]
    before=deepcopy(pool);calls=[]
    def pick(items,state,limit,audit):
        calls.append(limit)
        return pool[:limit]
    monkeypatch.setattr(F,'pick',pick)
    assert len(pool)==123 and not any(F.experiment.long_candidate(c) for c in pool[:24])
    audit={};selected=F.pick_for_shortfall([{}]*123,{},24,audit,treatment_needed=True)
    assert calls==[123] and len(selected)==24
    assert sum(F.experiment.long_candidate(c) for c in selected)==16
    assert selected[:16]==[c for c in pool if F.experiment.long_candidate(c)]
    assert audit['dispatch_window']['attempt_limit']==24
    assert audit['dispatch_window']['quality_gates_relaxed'] is False
    assert pool==before and not any(c.get('media_verified') for c in selected)


def test_controls_keep_exact_old_ranking_and_no_extra_scan(monkeypatch):
    pool=[candidate(1,80),candidate(2,1800)];calls=[]
    def pick(items,state,limit,audit):calls.append(limit);return pool[:limit]
    monkeypatch.setattr(F,'pick',pick)
    audit={}
    assert F.pick_for_shortfall([{}]*100,{},1,audit)==pool[:1]
    assert calls==[1] and 'dispatch_window' not in audit


def test_pool_smaller_than_limit_and_unknown_sources_do_not_claim_long_stock(monkeypatch):
    pool=[candidate(1),candidate(2,80)]
    monkeypatch.setattr(F,'pick',lambda *a,**k:pool)
    audit={};assert F.pick_for_shortfall([{},{}],{},24,audit,True)==pool
    assert audit['dispatch_window']['known_long_candidates']==0
    assert not any(F.experiment.long_candidate(c) for c in pool)
