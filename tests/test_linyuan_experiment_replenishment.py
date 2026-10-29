"""A consumed real T replenishes once; fallbacks and clocks earn no budget."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys

import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'linyuan/fc'),
             str(Path(__file__).resolve().parents[1]/'linyuan')]
import index as F
import dispatch_on_runner as runner
from daily_publication_audit import experiment_recovery_needed

FIXTURE=Path(__file__).parent/'fixtures/linyuan_actual_consumed_treatments_20261028.json'


@pytest.fixture
def actual(monkeypatch):
    data=json.loads(FIXTURE.read_text())
    monkeypatch.setattr(F.time,'time',lambda:datetime.fromisoformat(data['as_of']).timestamp())
    monkeypatch.setattr(F,'source_inventory',lambda *a:dict(inventory_fresh=True))
    monkeypatch.setattr(F,'inventory_record_current',lambda *a:True)
    return data


def candidates(data,active=()):
    return list(F.experiment_replenishment_candidates(data['state'],data['inventory'],data['items'],active))


def spend(data):
    plans=candidates(data)
    for parent,record in plans:
        data['state']['dispatched'].append(dict(slug=F.experiment_reuse_slug(parent),
            source_url=parent['source_url'],experiment_parent_slug=parent['slug'],
            experiment_mother_sha256=parent['source_sha256'],failed=True,
            experiment_selection_revision=F.experiment.SELECTION_REVISION,
            experiment_replenishment_basis=parent['experiment_replenishment_basis']))
    return plans


def test_actual_four_publications_reopen_three_mothers_without_relabeling_history(actual,monkeypatch):
    before=deepcopy(actual)
    # Before this repair the fixed-revision records permanently consume all
    # three mothers, despite later successful T publications.
    with monkeypatch.context() as m:
        m.setattr(F,'experiment_replenishment_candidates',lambda *a:iter(()))
        assert list(F.experiment_reuse_candidates(actual['state'],actual['inventory'],actual['items']))==[]
    plans=candidates(actual)
    assert len(plans)==3 and len({p['source_sha256'] for p,r in plans})==3
    assert all(r['historical_mother'] for p,r in plans)
    assert actual==before
    assert len({F.experiment_reuse_slug(p) for p,r in plans})==3


def test_failed_replenishment_or_changed_clock_cannot_repeat_the_same_consumption(actual,monkeypatch):
    plans=spend(actual)
    assert plans and candidates(actual)==[]
    monkeypatch.setattr(F.time,'time',lambda:datetime.fromisoformat(actual['as_of']).timestamp()+86400)
    assert candidates(actual)==[]
    for e in actual['state']['dispatched']:
        if e.get('experiment_replenishment_basis'):e['source_check_attempts']=999
    assert candidates(actual)==[]


def test_only_a_new_real_t_receipt_earns_one_more_automatic_selection(actual):
    plans=spend(actual);parent=plans[0][0]
    slug=next(s for s,i in actual['state']['published'].items() if i['source_url']==parent['source_url']
              and any((p.get('publishing_experiment') or {}).get('actual')==F.experiment.TREATMENT for p in i['parts']))
    part=deepcopy(next(p for p in actual['state']['published'][slug]['parts']
                       if p['publishing_experiment']['actual']==F.experiment.TREATMENT))
    part.update(bvid='BVnew-real-T',fingerprints=dict(sha256='d'*64),
                source_segments=[dict(start=500,end=550)])
    part['publishing_experiment'].update(actual='baseline',compliant=False)
    actual['state']['published'][slug]['parts'].append(part)
    assert candidates(actual)==[]
    part['publishing_experiment'].update(actual=F.experiment.TREATMENT,compliant=True)
    again=candidates(actual)
    assert len(again)==1 and again[0][0]['source_sha256']==parent['source_sha256']
    assert F.experiment_reuse_slug(again[0][0])!=F.experiment_reuse_slug(parent)


def test_duplicate_bvid_or_metadata_edits_do_not_earn_a_second_media_budget(actual):
    spend(actual)
    for info in actual['state']['published'].values():
        for part in list(info['parts']):
            if (part.get('publishing_experiment') or {}).get('actual')==F.experiment.TREATMENT:
                part['source_segments']=[dict(start=600,end=650)]
                info['parts'].append(dict(deepcopy(part),bvid=part['bvid']+'duplicate'))
    assert candidates(actual)==[]


@pytest.mark.parametrize('change',[
    lambda p:p.update(status='submitted'),
    lambda p:p['publishing_experiment'].update(compliant=False),
    lambda p:p['publishing_experiment'].update(actual='baseline'),
    lambda p:p['publishing_experiment'].update(experiment_id='other'),
    lambda p:p['publishing_experiment'].update(selection_kind='theory'),
    lambda p:p['publishing_experiment'].update(source_duration_sec=120),
    lambda p:p.update(source_sha256='broken'),
    lambda p:p.update(fingerprints={}),
    lambda p:p.update(source_segments=[dict(start=2,end=1)]),
])
def test_ordinary_unbound_or_invalid_receipts_cannot_reopen_mothers(actual,change):
    for info in actual['state']['published'].values():
        for part in info['parts']:
            if (part.get('publishing_experiment') or {}).get('actual')==F.experiment.TREATMENT:change(part)
    assert candidates(actual)==[]


def test_real_active_job_pending_retry_unknown_result_and_source_restrictions_stay_closed(actual,monkeypatch):
    for parent,record in candidates(actual):
        state=deepcopy(actual)
        active=[dict(status='in_progress',display_title='中文源出片 · '+parent['slug'])]
        assert parent['source_sha256'] not in {p['source_sha256'] for p,r in candidates(state,active)}
        root=next(e for e in state['state']['dispatched'] if e['slug']==parent['slug'])
        root['source_check_retry_after']=9999999999
        assert parent['source_sha256'] not in {p['source_sha256'] for p,r in candidates(state)}
        root.pop('source_check_retry_after')
        state['state']['dispatched'].append(dict(slug='unknown-child',source_url=parent['source_url'],
            experiment_parent_slug=parent['slug'],experiment_mother_sha256=parent['source_sha256']))
        assert parent['source_sha256'] not in {p['source_sha256'] for p,r in candidates(state)}
    with monkeypatch.context() as m:
        import source_admission_guard
        m.setattr(source_admission_guard,'restriction',lambda *a:'known denial')
        assert candidates(actual)==[]
    for item in actual['items']:item['extra']['no_reprint']=1
    assert candidates(actual)==[]


def test_unknown_inventory_does_not_open_the_normal_replenishment_path(actual,monkeypatch):
    monkeypatch.setattr(F,'source_inventory',lambda *a:dict(inventory_fresh=False))
    assert list(F.experiment_reuse_candidates(actual['state'],actual['inventory'],actual['items']))==[]


@pytest.mark.parametrize('active,expected',[(0,3),(6,0)])
def test_real_empty_trial_shelves_dispatch_new_bound_jobs_within_six_worker_cap(actual,monkeypatch,active,expected):
    state=actual['state'];state.update(rejected=[],pending_retry=[])
    inventory=actual['inventory']
    stock=dict(inventory_fresh=True,verified_landscape=21,verified_portrait=8,daily_mix_usable=29,
               experiment_ready=dict(landscape=0,portrait=0))
    monkeypatch.setattr(F,'load_state',lambda:state)
    monkeypatch.setattr(F,'source_inventory',lambda *a:stock)
    monkeypatch.setattr(F,'_collect_source_rejections',lambda *a:0)
    monkeypatch.setattr(F,'save_state',lambda *a:None)
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    monkeypatch.setattr(F,'staging_release_id',lambda:1)
    monkeypatch.setattr(F,'obsolete_review_candidates',lambda *a:[])
    monkeypatch.setattr(F,'_process_retries',lambda *a:None)
    def pick(items,state,limit,audit):audit.update(records=len(items),candidate_count=0);return []
    monkeypatch.setattr(F,'pick',pick)
    calls=[]
    def api(method,path,body=None,**kwargs):
        if method=='POST':calls.append((path,body));return {}
        if F.DATA_JSON in path:return json.dumps(actual['items']).encode()
        if F.SOURCE_INVENTORY_KEY in path:return json.dumps(inventory).encode()
        if 'production_status.json' in path:return b'{"runs":{}}'
        if 'status=in_progress' in path:return dict(workflow_runs=[dict(status='in_progress')]*active)
        return dict(workflow_runs=[])
    monkeypatch.setattr(F,'gh',api)
    result=F._dispatch_admitted(dict(execution_backend='github'))
    jobs=[body for path,body in calls if F.WF_PRODUCE in path]
    assert result['dispatched']==len(jobs)==expected
    if expected:
        assert result['experiment_supply']['status']=='replenishing'
        assert all(j['inputs']['editorial_profile']==F.experiment.TREATMENT for j in jobs)
        children=[e for e in state['dispatched'] if e.get('experiment_replenishment_basis')]
        assert len(children)==3 and len({e['slug'] for e in children})==3
        assert all(e['experiment_selection_revision']==F.experiment.SELECTION_REVISION for e in children)
        # API listings may lag a successful dispatch. Persisted consumption
        # budgets prevent a second invocation from replaying the same jobs.
        assert candidates(actual)==[]


def test_four_native_wide_trials_cover_two_days_without_forcing_portrait_or_claiming_more_stock():
    E=F.experiment;now=datetime.fromisoformat('2026-10-28T12:00:00+08:00').timestamp()
    stock=dict(experiment_ready=dict(landscape=4,portrait=0))
    assert E.production_deficits(stock,{},[],now)==dict(landscape=0,portrait=0)
    stock['experiment_ready']['landscape']=2
    entries=[dict(slug='wide'+str(i),editorial_profile=E.TREATMENT,output_layout='landscape') for i in range(2)]
    runs=[dict(status='in_progress',display_title='中文源出片 · '+e['slug']) for e in entries]
    assert E.production_deficits(stock,dict(dispatched=entries),runs,now)==dict(landscape=0,portrait=0)
    assert stock['experiment_ready']==dict(landscape=2,portrait=0)


@pytest.mark.parametrize('actual_profile',[F.experiment.TREATMENT,F.experiment.BASELINE])
def test_consumed_trial_or_fallback_wakes_normal_verifier_once(monkeypatch,actual_profile):
    state={};posts=[]
    monkeypatch.setattr(F.time,'time',lambda:10000)
    monkeypatch.setattr(F,'gh',lambda *a,**k:posts.append(a))
    monkeypatch.setattr(F,'save_state',lambda s:None)
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    receipt=dict(experiment_id=F.experiment.EXPERIMENT_ID,planned=F.experiment.TREATMENT,actual=actual_profile)
    assert F.refresh_after_experiment_publication(state,receipt)
    assert not F.refresh_after_experiment_publication(state,receipt)
    assert len(posts)==1 and 'linyuan-source-inventory.yml' in posts[0][1]
    assert not F.refresh_after_experiment_publication({},dict(receipt,planned=F.experiment.BASELINE))


def test_after_the_last_slot_failed_actual_experiment_still_requests_recovery():
    report=dict(experiment=dict(active=True,missing_slots=[],noncompliant_slots=[dict(slot='2026-10-28 21')]),
                experiment_drafts=None)
    assert experiment_recovery_needed(report)
    report['experiment']['noncompliant_slots']=[]
    assert not experiment_recovery_needed(report)
    report['experiment_drafts']=dict(passed=False)
    assert experiment_recovery_needed(report)
    report['experiment']['active']=False
    assert not experiment_recovery_needed(report)


def test_runner_retains_explicit_experiment_failure_instead_of_green_empty_check(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(F,'TOKEN','test')
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    monkeypatch.setattr(F,'flush_logs',lambda:None)
    monkeypatch.setattr(F,'dispatch_handler',lambda *a:dict(dispatched=0,attempted=0,
        experiment_supply=dict(status='stalled',verified_drafts=dict(portrait=0,landscape=0))))
    monkeypatch.setattr(runner,'retire_timer',lambda:pytest.fail('Stalled supply is not a successful migration'))
    with pytest.raises(runner.ExperimentSupplyStalled):runner.main()
    receipt=json.loads((tmp_path/'github-dispatch-receipt.json').read_text())
    assert receipt['failure']['category']=='experiment_supply_stalled' and not receipt['completed']
