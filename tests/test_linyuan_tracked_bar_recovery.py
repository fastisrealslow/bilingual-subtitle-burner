import copy
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan/fc'))
import index as fc


def case():
    entry=dict(slug='ly-test',failed=True,source_url='https://example.com/mother',
        ts=100,last_error='原始ASR存在影响理解的疑点，禁止猜改后发布',
        source_check_attempts=2,retry_attempts=5,
        automatic_rule_recoveries={'transcript-review-model-v3':{'run_id':1}},
        production_failure_summary=dict(slug='ly-test',run_id=7,artifact_id=9,accepted=0,
            failures=[dict(stage='part-quality',reason='原始ASR存在影响理解的疑点'),
                      dict(stage='part-quality',reason='成片存在持续黑色填充边')]))
    return dict(dispatched=[entry],published={},rejected=[{'slug':'ly-test'}]),entry,dict(id=7,head_sha='old',conclusion='failure')


def test_black_bar_repair_uses_bound_later_failure_once_without_resetting_history(monkeypatch):
    state,entry,run=case();before=copy.deepcopy(state['rejected'])
    monkeypatch.setattr(fc,'gh',lambda *a,**k:{'files':[{'filename':'linyuan/live_tracking.py'}]})
    monkeypatch.setattr(fc,'save_state',lambda *a:None)
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    assert fc._recover_changed_production_rule(state,entry,run)
    assert entry['source_check_attempts']==2 and entry['retry_attempts']==5
    assert state['rejected']==before and entry['last_error'].startswith('原始ASR')
    assert entry['automatic_rule_recoveries']['tracked-source-strips-v1']['failure_artifact_id']==9
    entry['failed']=True
    assert not fc._recover_changed_production_rule(state,entry,run)


@pytest.mark.parametrize('change',[
    lambda e,r:e['production_failure_summary'].update(run_id=8),
    lambda e,r:e['production_failure_summary'].update(slug='other'),
    lambda e,r:e['production_failure_summary'].update(accepted=1),
    lambda e,r:e['production_failure_summary'].update(accepted=False),
    lambda e,r:e['production_failure_summary'].update(artifact_id=0),
    lambda e,r:e.pop('production_failure_summary'),
    lambda e,r:r.update(conclusion='success'),
    lambda e,r:e.update(repair_bvid='existing-publication'),
])
def test_no_recovery_from_unbound_or_already_accepted_source(monkeypatch,change):
    state,entry,run=case();change(entry,run)
    monkeypatch.setattr(fc,'gh',lambda *a,**k:{'files':[{'filename':'linyuan/live_tracking.py'}]})
    assert not fc._recover_changed_production_rule(state,entry,run)
    assert entry['failed']


def test_no_recovery_for_unrelated_code_or_existing_publication(monkeypatch):
    state,entry,run=case()
    monkeypatch.setattr(fc,'gh',lambda *a,**k:{'files':[{'filename':'linyuan/presentation.py'}]})
    assert not fc._recover_changed_production_rule(state,entry,run)
    state['published'][entry['slug']]={'bvid':'existing'}
    monkeypatch.setattr(fc,'gh',lambda *a,**k:{'files':[{'filename':'linyuan/live_tracking.py'}]})
    assert not fc._recover_changed_production_rule(state,entry,run)


@pytest.mark.parametrize('accepted,report_slug,expected',[(0,'ly-test',True),(1,'ly-test',False),(False,'ly-test',False),(0,'other',False)])
def test_editorial_only_batch_evidence_is_bound_and_never_imports_approval(monkeypatch,accepted,report_slug,expected):
    import json,zipfile
    state,entry,run=case();entry.pop('production_failure_summary')
    monkeypatch.setattr(fc,'gh',lambda *a,**k:{'artifacts':[{
        'id':9,'name':'editorial-ly-test','size_in_bytes':1000,'expired':False}]})
    def download(artifact_id,target,**kwargs):
        assert kwargs['attempts']==1 and kwargs['max_bytes']==2_000_000
        with zipfile.ZipFile(target,'w') as z:
            z.writestr('batch_report.json',json.dumps(dict(slug=report_slug,accepted=accepted,
                rejected=[{'stage':'part-quality','reason':'成片存在持续黑色填充边'}])))
            z.writestr('meta.json',json.dumps({'editorial_approved':True}))
    monkeypatch.setattr(fc,'download_reviewed_zip',download)
    assert fc._observe_zero_batch_failures(entry,run)
    assert ('production_failure_summary' in entry)==expected
    assert entry['failed'] and entry['source_check_attempts']==2
    assert not fc._observe_zero_batch_failures(entry,run)


def test_transient_batch_download_is_retryable_evidence_gap(monkeypatch):
    state,entry,run=case();entry.pop('production_failure_summary')
    monkeypatch.setattr(fc,'gh',lambda *a,**k:{'artifacts':[{
        'id':9,'name':'editorial-ly-test','size_in_bytes':1000,'expired':False}]})
    def unavailable(*a,**k):raise TimeoutError('temporary observation failure')
    monkeypatch.setattr(fc,'download_reviewed_zip',unavailable)
    assert not fc._observe_zero_batch_failures(entry,run)
    assert 'batch_failure_lookup' not in entry and 'production_failure_summary' not in entry


def test_archived_batch_lookup_is_bounded_and_cached_across_backlog_sweeps(monkeypatch):
    state={'dispatched':[],'published':{}};entries={};runs=[];seen=[]
    for i in range(5):
        slug='ly-archived-'+str(i)
        entry={'slug':slug,'failed':True,'source_url':'https://example.com/'+str(i),
               'last_error':'原始ASR存在影响理解的疑点'}
        entries[slug]=entry;state['dispatched'].append(entry)
        runs.append({'id':i+1,'conclusion':'failure','display_title':'林园 · '+slug})
    monkeypatch.setattr(fc,'gh',lambda *a,**k:{'workflow_runs':runs})
    monkeypatch.setattr(fc,'_recover_changed_production_rule',lambda *a:False)
    monkeypatch.setattr(fc,'save_state',lambda *a:None)
    def observe(entry,run):
        seen.append(run['id']);entry['batch_failure_lookup']={'run_id':run['id']};return True
    monkeypatch.setattr(fc,'_observe_zero_batch_failures',observe)
    assert fc._recover_rule_backlog(state,entries)==0
    assert seen==[1,2,3]
    assert fc._recover_rule_backlog(state,entries)==0
    assert seen==[1,2,3,4,5]
