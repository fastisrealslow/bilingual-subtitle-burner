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
