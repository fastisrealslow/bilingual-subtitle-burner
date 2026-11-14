"""A story-only shortage must not spend its workers on unneeded controls."""
from copy import deepcopy
import pytest
from test_linyuan_experiment_duration_probe import F,dispatch_env,candidate,row,NOW


def full_stock(monkeypatch,state=None):
    stock=dict(inventory_fresh=True,verified_landscape=100,verified_portrait=37,daily_mix_usable=137,
               experiment_ready=dict(landscape=0,portrait=0))
    monkeypatch.setattr(F,'source_inventory',lambda *a:stock)
    if state is not None:state['source_refresh_requested_at']=NOW


def test_known_short_candidates_stay_eligible_without_transfer_rejection_or_baseline_jobs(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env;full_stock(monkeypatch,state)
    short=candidate(1,80);short['video_url']='https://example.test/native.mp4'
    candidates.append(short);before=deepcopy(state)
    monkeypatch.setattr(F,'download',lambda *a:pytest.fail('unneeded paid transfer'))
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==0
    assert not posts and state==before


def test_only_one_existing_unknown_probe_is_requested_when_controls_are_full(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env;full_stock(monkeypatch,state)
    candidates.extend([candidate(1),candidate(2),candidate(3,80)])
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==1
    assert posts[0]['editorial_profile']==F.experiment.TREATMENT
    assert len(state['dispatched'])==1 and not state['published']


def test_exhausted_original_probe_budget_cannot_spill_into_extra_baseline_jobs(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env;full_stock(monkeypatch,state)
    state['dispatched']=[row('old1',NOW-7200,failed=True),row('old2',NOW-3700,failed=True)]
    candidates.extend([candidate(1),candidate(2)])
    before=deepcopy(state)
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==0
    assert not posts and state==before


def test_known_long_story_is_prioritized_without_starting_short_or_unknown_controls(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env;full_stock(monkeypatch,state)
    candidates.extend([candidate(1,80),candidate(2),candidate(3,1800)])
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==1
    assert posts[0]['source'].endswith('/3') and posts[0]['editorial_profile']==F.experiment.TREATMENT


def test_unknown_domestic_mother_cannot_start_paid_exploration_when_controls_are_full(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env;full_stock(monkeypatch,state)
    c=candidate(1);c.update(page_url='https://example.test/interview',video_url='https://cdn.test/video')
    candidates.append(c)
    monkeypatch.setattr(F,'download',lambda *a:pytest.fail('unneeded domestic transfer'))
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==0
    assert not posts and not state['dispatched']


def test_original_worker_ceiling_still_applies_when_only_treatment_is_missing(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env;full_stock(monkeypatch,state)
    candidates.append(candidate(1,1800))
    runs.extend(dict(id=i,status='in_progress',display_title=f'source · other{i}') for i in range(6))
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==0
    assert not posts


def test_short_only_catalogue_requests_real_source_refresh_once_with_original_backoff(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env;full_stock(monkeypatch)
    candidates.append(candidate(1,80));old=F.gh;refreshes=[]
    def gh(method,path,*args,**kwargs):
        if method=='POST' and path=='/actions/workflows/linyuan-monitor.yml/dispatches':
            assert args==({'ref':'main'},)
            refreshes.append(path);return {}
        return old(method,path,*args,**kwargs)
    monkeypatch.setattr(F,'gh',gh)
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==0
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==0
    assert len(refreshes)==1 and state['source_refresh_requested_at']==NOW
    assert not posts and not state['dispatched'] and not state['rejected']
