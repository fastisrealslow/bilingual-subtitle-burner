"""No FC transfer for ordinary backlog work without an eligible producer slot."""
from test_linyuan_experiment_duration_probe import F, candidate, dispatch_env, row
from test_linyuan_inventory_backlog_targets import NOW, stock

def setup_stock(monkeypatch):
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    monkeypatch.setattr(F,'source_inventory',lambda *a:stock())
    def forbidden(*args,**kwargs):raise AssertionError('Paid media read before admission capacity')
    monkeypatch.setattr(F,'download',forbidden)
    monkeypatch.setattr(F,'tencent_resolve_url',forbidden)

def domestic(n,duration=80):
    return dict(candidate(n,duration),page_url=f'https://news.qq.com/rain/a/{n}',video_url='')

def test_actual_domestic_candidate_is_skipped_before_paid_download_when_c_places_full(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env
    setup_stock(monkeypatch)
    for i in range(4):
        slug=f'ordinary-{i}'
        state['dispatched'].append(dict(slug=slug,editorial_profile=F.experiment.BASELINE))
        runs.append(dict(status='in_progress',display_title='中文源出片 · '+slug))
    candidates.append(domestic(100))
    result=F._dispatch_admitted(dict(execution_backend='fc'))
    assert not posts and result['dispatched']==0
    assert not result.get('requires_mainland_transfer')

def test_deferred_ordinary_does_not_request_fc_after_four_ci_jobs_fill_capacity(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env
    setup_stock(monkeypatch)
    candidates.extend([domestic(100)]+[candidate(i,80) for i in range(6)])
    result=F._dispatch_admitted(dict(execution_backend='github'))
    assert result['dispatched']==len(posts)==4
    assert not result.get('requires_mainland_transfer')

def test_real_domestic_long_treatment_can_still_use_reserved_worker(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env
    setup_stock(monkeypatch)
    candidates.extend([domestic(100,1800)]+[candidate(i,80) for i in range(6)])
    result=F._dispatch_admitted(dict(execution_backend='github'))
    assert result['dispatched']==len(posts)==4
    assert result.get('requires_mainland_transfer') is True

def test_exhausted_probe_budget_cannot_spend_fc_for_unknown_with_full_ordinary_places(monkeypatch):
    setup_stock(monkeypatch)
    state=dict(dispatched=[row('one',NOW-7200,failed=True),row('two',NOW-3700,failed=True)])
    assert not F.backlog_candidate_has_room(domestic(100,None),stock(),state,[],
        dict(portrait=752,landscape=0),dict(portrait=150,landscape=150),
        known_long_available=False,ordinary_inflight=4)
    assert F.backlog_candidate_has_room(domestic(100,1800),stock(),state,[],
        dict(portrait=752,landscape=0),dict(portrait=150,landscape=150),
        known_long_available=True,ordinary_inflight=4)

def test_ordinary_download_still_allowed_without_treatment_gap(monkeypatch):
    setup_stock(monkeypatch)
    assert F.backlog_candidate_has_room(domestic(100),stock(),{},[],
        dict(portrait=752,landscape=0),dict(portrait=0,landscape=0),
        known_long_available=False,ordinary_inflight=4)

def test_full_backlog_retains_existing_once_daily_recent_context_probe(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env
    setup_stock(monkeypatch)
    monkeypatch.setattr(F,'source_inventory',lambda *a:stock(900,300,150))
    monkeypatch.setattr('source_priority.recording_recency_rank',lambda *a:0)
    candidates.append(candidate(100,80))
    result=F._dispatch_admitted(dict(execution_backend='github'))
    assert result['dispatched']==len(posts)==1
    assert state['recent_context_probe_date']=='2026-11-20'
