"""Unknown duration may request bounded exploration, never claim long stock."""
import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
import index as F

NOW=1792711200  # 2026-10-23, Beijing morning; original experiment remains live.


def allowed(candidate=None,state=None,active=None,**kwargs):
    args=dict(known_long_available=False,now=NOW)
    args.update(kwargs)
    return F.experiment_duration_probe_allowed(
        candidate or {},state or {},active or [],dict(portrait=2,landscape=1),**args)


def row(slug,ts,**kwargs):
    return dict(slug=slug,ts=ts,experiment_duration_probe=dict(requested_at=ts),**kwargs)


@pytest.mark.parametrize('extra',[None,{},dict(duration=None),dict(duration=''),
                                 dict(duration=0),dict(duration=0.0),'{}','{"duration":null}'])
def test_only_missing_duration_gets_exploration(extra):
    candidate=dict(extra=extra);before=copy.deepcopy(candidate)
    assert allowed(candidate)
    assert candidate==before
    assert not F.experiment.long_candidate(candidate)


@pytest.mark.parametrize('duration',[True,False,-1,'0','bad',float('nan'),float('inf'),
                                    10,1199,1200,5400,5401])
def test_known_short_long_or_malformed_duration_is_not_unknown(duration):
    assert not allowed(dict(extra=dict(duration=duration)))


@pytest.mark.parametrize('extra',['bad','[]','null',[],1,False])
def test_invalid_metadata_fails_closed(extra):
    assert not allowed(dict(extra=extra))


@pytest.mark.parametrize('duration',[True,None,'0',-1,1,1199,1200,float('nan')])
def test_measured_duration_never_uses_missing_metadata_probe(duration):
    assert not allowed(measured_duration=duration)


def test_known_long_supply_or_no_portrait_deficit_disables_probe():
    assert not allowed(known_long_available=True)
    for needs in (dict(portrait=0,landscape=2),{},dict(portrait=-1)):
        assert not F.experiment_duration_probe_allowed({}, {}, [], needs,
            known_long_available=False,now=NOW)


def test_eventual_actions_visibility_cannot_create_two_probes_in_one_round():
    state=dict(dispatched=[row('first',NOW-1)])
    before=copy.deepcopy(state)
    assert not allowed(state=state)
    assert state==before
    state['dispatched'][0]['failed']=True
    assert allowed(state=state)  # Completed failure still consumes daily budget.


def test_one_probe_active_across_midnight_blocks_new_day():
    state=dict(dispatched=[row('old',NOW-86400)])
    for status in ('queued','in_progress'):
        assert not allowed(state=state,active=[dict(status=status,display_title='中文源 · old')])
    assert allowed(state=state,active=[dict(status='completed',display_title='中文源 · old')])


def test_two_unique_daily_probes_not_retry_rows_and_no_counter_reset():
    first=row('one',NOW-7200,failed=True)
    state=dict(dispatched=[first,{**first,'ts':NOW-100}],daily_publish={'history':7})
    before=copy.deepcopy(state)
    assert allowed(state=state)
    state['dispatched'].append(row('two',NOW-3700,failed=True))
    assert not allowed(state=state)
    state['dispatched'].pop()
    assert state==before
    # Earliest marker survives an erroneous duplicate row with a newer date.
    old=row('old',NOW-86400,failed=True)
    assert allowed(state=dict(dispatched=[old,row('old',NOW-1,failed=True),first]))


@pytest.mark.parametrize('marker',[True,{},dict(requested_at=True),dict(requested_at=None),
                                  dict(requested_at=float('nan')),dict(requested_at=NOW+1),
                                  dict(requested_at=0)])
def test_unreadable_or_future_budget_is_not_unused(marker):
    assert not allowed(state=dict(dispatched=[dict(slug='bad',experiment_duration_probe=marker)]))


def test_daily_budget_uses_beijing_midnight_not_utc():
    midnight=(NOW+28800)//86400*86400-28800
    assert allowed(state=dict(dispatched=[row('a',midnight-2,failed=True),
                                         row('b',midnight-1,failed=True)]))
    assert not allowed(state=dict(dispatched=[row('a',midnight,failed=True),
                                             row('b',midnight+1,failed=True)]))


@pytest.fixture
def dispatch_env(monkeypatch):
    state=dict(dispatched=[],published={},pending_retry=[],rejected=[])
    stock=dict(inventory_fresh=True,verified_landscape=0,verified_portrait=0,daily_mix_usable=0,
               experiment_ready=dict(landscape=1,portrait=0))
    candidates=[];posts=[];runs=[]
    monkeypatch.setattr(F.time,'time',lambda:NOW)
    monkeypatch.setattr(F,'load_state',lambda:state)
    monkeypatch.setattr(F,'_collect_source_rejections',lambda _:0)
    monkeypatch.setattr(F,'source_inventory',lambda *a:stock)
    monkeypatch.setattr(F,'staging_release_id',lambda:1)
    monkeypatch.setattr(F,'save_state',lambda *a:None)
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    monkeypatch.setattr(F,'_process_retries',lambda *a:None)
    monkeypatch.setattr(F,'weekly_full_request',lambda *a:False)
    monkeypatch.setattr(F,'experiment_reuse_candidates',lambda *a:[])
    def gh(method,path,*args,**kw):
        if method=='POST':posts.append(args[0]['inputs']);return {}
        if '/runs?' in path:
            status=path.split('status=')[1].split('&')[0]
            return dict(workflow_runs=[r for r in runs if r['status']==status])
        if F.DATA_JSON in path:return b'[]'
        if F.SOURCE_INVENTORY_KEY in path:return b'{}'
        raise AssertionError((method,path))
    monkeypatch.setattr(F,'gh',gh)
    def pick(items,st,limit,audit):
        audit.update(records=len(candidates),candidate_count=len(candidates))
        return candidates
    monkeypatch.setattr(F,'pick',pick)
    return state,candidates,posts,runs


def candidate(n,duration=None):
    return dict(key=str(n),video_id=str(n),video_url='',page_url=f'https://weibo.com/{n}',
                title='林园讲述投资经历',source='weibo',extra=dict(duration=duration))


def test_normal_dispatch_sends_only_one_unknown_as_portrait_intent(dispatch_env):
    state,candidates,posts,runs=dispatch_env
    candidates.extend([candidate(1),candidate(2)])
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==2
    assert posts[0]['editorial_profile']==F.experiment.TREATMENT
    assert posts[0]['output_layout']=='portrait'
    assert 'editorial_profile' not in posts[1]
    assert sum(bool(e.get('experiment_duration_probe')) for e in state['dispatched'])==1
    assert all(not F.experiment.long_candidate(c) for c in candidates)
    assert not state['published']  # Request does not fabricate any accepted post.


def test_normal_six_slot_cap_is_shared_with_probe(dispatch_env):
    state,candidates,posts,runs=dispatch_env
    candidates.extend([candidate(1),candidate(2)])
    runs.extend(dict(id=i,status='in_progress',display_title=f'source · existing{i}') for i in range(5))
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==1
    assert len(posts)==1 and posts[0]['editorial_profile']==F.experiment.TREATMENT
    runs.append(dict(id=6,status='queued',display_title='source · existing6'))
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==0
    assert len(posts)==1


def test_known_long_candidate_keeps_priority_and_unknown_stays_baseline(dispatch_env):
    state,candidates,posts,runs=dispatch_env
    candidates.extend([candidate(1),candidate(2,1800)])
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==2
    assert posts[0]['source'].endswith('/2')
    assert posts[0]['editorial_profile']==F.experiment.TREATMENT
    assert 'editorial_profile' not in posts[1]
    assert not any(e.get('experiment_duration_probe') for e in state['dispatched'])


@pytest.mark.parametrize('needs,layout',[
    (dict(portrait=2,landscape=1),'portrait'),
    (dict(portrait=1,landscape=2),'landscape'),
    (dict(portrait=2,landscape=2),'landscape'),
    (dict(portrait=1,landscape=1),'landscape'),
    (dict(portrait=1,landscape=0),'portrait'),
    (dict(portrait=0,landscape=1),'landscape'),
])
def test_known_long_new_mother_follows_larger_actual_treatment_gap(dispatch_env,monkeypatch,needs,layout):
    state,candidates,posts,runs=dispatch_env
    candidates.append(candidate(1,1800))
    monkeypatch.setattr(F.experiment,'production_deficits',lambda *a:dict(needs))
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==1
    assert posts[0]['editorial_profile']==F.experiment.TREATMENT
    assert posts[0]['output_layout']==layout and state['dispatched'][0]['output_layout']==layout
    assert not state['published'] and not state['dispatched'][0].get('experiment_duration_probe')


def test_each_intent_consumes_only_its_layout_gap_and_never_becomes_stock(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env
    candidates.extend([candidate(1,1800),candidate(2,1800),candidate(3,1800)])
    monkeypatch.setattr(F.experiment,'production_deficits',lambda *a:dict(portrait=2,landscape=1))
    monkeypatch.setattr(F,'MAX_PER_DAY',3)
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==3
    assert [p['output_layout'] for p in posts]==['portrait','landscape','portrait']
    assert all(p['editorial_profile']==F.experiment.TREATMENT for p in posts)
    assert not state['published'] and len(state['dispatched'])==3


def test_full_interview_and_unknown_probes_keep_their_existing_portrait_rule(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env
    candidates.append(candidate(1,1800))
    monkeypatch.setattr(F.experiment,'production_deficits',lambda *a:dict(portrait=1,landscape=2))
    monkeypatch.setattr(F,'weekly_full_request',lambda *a:True)
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==1
    assert posts[0]['output_layout']=='portrait' and posts[0]['include_full']=='true'


def test_daily_probe_budget_keeps_ordinary_supply_not_extra_dispatches(dispatch_env):
    state,candidates,posts,runs=dispatch_env
    state['dispatched'].extend([row('old1',NOW-7200,failed=True),row('old2',NOW-3700,failed=True)])
    candidates.extend([candidate(1),candidate(2)])
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==2
    assert all('editorial_profile' not in post for post in posts)
    assert len(state['dispatched'])==4


@pytest.mark.parametrize('seconds,profile',[(60,F.experiment.BASELINE),(1800,F.experiment.TREATMENT)])
def test_measured_download_duration_overrides_missing_ledger_for_request(dispatch_env,monkeypatch,seconds,profile):
    state,candidates,posts,runs=dispatch_env
    c=candidate(1);c.update(page_url='https://example.invalid/interview',video_url='https://cdn.invalid/video')
    candidates.append(c)
    monkeypatch.setattr(F,'download',lambda *a:seconds)
    monkeypatch.setattr(F,'upload_asset',lambda *a:'https://example.invalid/uploaded')
    assert F._dispatch_admitted({})['dispatched']==1
    assert posts[0].get('editorial_profile',F.experiment.BASELINE)==profile
    assert not state['dispatched'][0].get('experiment_duration_probe')


def test_weekly_full_request_does_not_become_duration_probe(dispatch_env,monkeypatch):
    state,candidates,posts,runs=dispatch_env
    candidates.append(candidate(1))
    monkeypatch.setattr(F,'weekly_full_request',lambda *a:True)
    assert F._dispatch_admitted({'execution_backend':'github'})['dispatched']==1
    assert 'editorial_profile' not in posts[0]
    assert posts[0]['include_full']=='true'


def test_short_fallback_is_preserved_but_verified_experiment_requires_its_own_proof():
    root=Path(__file__).resolve().parents[1]
    workflow=(root/'.github/workflows/linyuan-produce-cn.yml').read_text()
    # Production archives ordinary accepted outputs before strict E-only proof;
    # do not accidentally adopt the non-publishing preview mirror's ordering.
    assert workflow.index('name: deliver-${{ env.RUN_SLUG }}')<workflow.index('scripts/verify_linyuan_story_delivery.py')
    assert F.experiment.actual_profile(dict(editorial_profile=F.experiment.BASELINE))==F.experiment.BASELINE
    assert F.experiment.proof_error(dict(editorial_profile=F.experiment.TREATMENT))
