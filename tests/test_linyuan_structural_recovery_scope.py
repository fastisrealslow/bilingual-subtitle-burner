"""Title edits must not restart an unchanged NoStructuralCandidate source."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
import index as F


@pytest.fixture
def case(monkeypatch):
    candidate=dict(slug='source',failed=True,source_quality_rejected=True,
        source_url='https://www.bilibili.com/video/BVexample',ts=1,
        source_check_attempts=2,quality_retries=1,
        last_error='原文中未找到满足20秒且起止边界明确的连续候选；需核对边界或换源，重复模型请求无助于恢复')
    state=dict(dispatched=[candidate],published={},daily_publish=dict(count=4))
    run=dict(id=123,conclusion='failure',head_sha='old',updated_at='2026-10-22T21:57:47Z')
    changes=[];writes=[]
    def gh(method,path):
        assert method=='GET' and path=='/compare/old...main'
        return dict(files=changes)
    monkeypatch.setattr(F,'gh',gh)
    monkeypatch.setattr(F,'save_state',lambda s:writes.append(deepcopy(s)))
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    return state,candidate,run,changes,writes


@pytest.mark.parametrize('filename,patch',[
    ('linyuan/title_rewrite.py','+ def story_literal_candidates(selection):'),
    ('linyuan/title_rewrite.py','+ def lifetime_holding_hedge_error(title):'),
    ('linyuan/publishing_experiment.py','+ def personal_detail_error(detail):'),
    ('linyuan/produce_cn.py','+ cw=copywrite(cues)'),
    ('linyuan/produce_cn.py','+ title_policy=title_rewrite.VERSION'),
    ('linyuan/produce_cn.py','+ story_selection=choose_stories(cues)'),
    ('linyuan/produce_cn.py','+ # fix source_selection later'),
    ('linyuan/produce_cn.py',None),
])
def test_nonstructural_code_cannot_reopen_empty_candidate_failure(case,filename,patch):
    state,candidate,run,changes,writes=case
    changes.append(dict(filename=filename,patch=patch))
    assert not F._recover_changed_production_rule(state,candidate,run)
    assert candidate['failed'] and candidate['source_quality_rejected']
    assert candidate['source_check_attempts']==2 and candidate['quality_retries']==1
    assert not candidate['automatic_rule_recoveries'] and not writes


@pytest.mark.parametrize('filename,patch',[
    ('linyuan/source_selection.py','+ def speech_opening(text):'),
    ('linyuan/produce_cn.py','+ source_picks=source_selection.select(cues)'),
])
def test_real_structural_repair_keeps_once_only_recovery_and_lifetime_history(case,filename,patch):
    state,candidate,run,changes,writes=case
    changes.append(dict(filename=filename,patch=patch))
    before=deepcopy(state['daily_publish'])
    assert F._recover_changed_production_rule(state,candidate,run)
    assert 'automatic-source-context-v10' in candidate['automatic_rule_recoveries']
    assert not candidate['failed'] and candidate['source_check_attempts']==2
    assert candidate['quality_retries']==1 and state['daily_publish']==before
    candidate['failed']=True
    # The same source_selection patch may also match its existing v41 case;
    # leave that historical case consumed to verify this repair creates no new budget.
    candidate['automatic_rule_recoveries'].setdefault('medium-source-opening-v41',dict(run_id=1))
    assert not F._recover_changed_production_rule(state,candidate,run)
    assert len(writes)==1


def test_real_title_failure_remains_recoverable_after_title_repair(case):
    state,candidate,run,changes,writes=case
    candidate['last_error']='标题文案待重试：保留原话限定条件'
    changes.append(dict(filename='linyuan/title_rewrite.py',patch='+ def lifetime_holding_hedge_error(title):'))
    assert F._recover_changed_production_rule(state,candidate,run)
    assert candidate['source_check_attempts']==2 and len(writes)==1


def test_keynote_proposal_cannot_reopen_an_existing_title_failure(case):
    state,candidate,run,changes,writes=case
    candidate['last_error']='标题文案待重试：未确认嘉宾原话归属'
    changes.extend([
        dict(filename='linyuan/produce_cn.py',patch='+ source_picks=keynote_review["picks"]'),
        dict(filename='linyuan/keynote_proposals.py',patch='+def propose(cues,source_report,call,path):')])
    assert not F._recover_changed_production_rule(state,candidate,run)
    assert candidate['failed'] and candidate['source_check_attempts']==2 and not writes


def keynote_delta(changes):
    changes.append(dict(filename='linyuan/produce_cn.py',patch='\n'.join([
        "+        if (not source_picks and automatic_only() and editorial.CONTENT_POLICY=='reference_v1'",
        "+                source_picks=keynote_review['picks']",
        "+                diagnostics['keynote_proposal_count']=len(source_picks)"])))


def source_evidence(candidate,run,duration):
    candidate['production_failure_summary']=dict(slug=candidate['slug'],run_id=run['id'],
        artifact_id=456,accepted=0,source_context=dict(passed=True,
            source_url=candidate['source_url'],source_sha256='a'*64,
            duration_sec=duration,resolution=dict(width=1920,height=1080)))


@pytest.mark.parametrize('duration',[46.66,96.27,899.99,5400.01,None,True,float('nan')])
def test_actual_long_only_delta_cannot_reopen_short_or_unknown_source(case,duration):
    state,candidate,run,changes,writes=case
    keynote_delta(changes);source_evidence(candidate,run,duration)
    assert not F._recover_changed_production_rule(state,candidate,run)
    assert candidate['failed'] and candidate['source_check_attempts']==2 and not writes
    assert not candidate['automatic_rule_recoveries']


@pytest.mark.parametrize('duration',[900,1253.63,1635.6,5400])
def test_real_long_only_delta_needs_matching_measured_source_and_keeps_history(case,duration):
    state,candidate,run,changes,writes=case
    keynote_delta(changes);source_evidence(candidate,run,duration)
    assert F._recover_changed_production_rule(state,candidate,run)
    assert candidate['source_check_attempts']==2 and candidate['quality_retries']==1
    candidate['failed']=True
    assert not F._recover_changed_production_rule(state,candidate,run)
    assert len(writes)==1


@pytest.mark.parametrize('change',[
    lambda s:s.update(run_id=999),lambda s:s.update(slug='other'),
    lambda s:s.update(accepted=False),lambda s:s.update(artifact_id=0),
    lambda s:s.update(source_context=None),
    lambda s:s['source_context'].update(passed=False),
    lambda s:s['source_context'].update(source_sha256='invalid'),
    lambda s:s['source_context'].update(source_url='https://www.bilibili.com/video/BVdifferent'),
])
def test_keynote_recovery_rejects_unbound_source_context(case,change):
    state,candidate,run,changes,writes=case
    keynote_delta(changes);source_evidence(candidate,run,1253.63)
    change(candidate['production_failure_summary'])
    assert not F._recover_changed_production_rule(state,candidate,run)
    assert candidate['failed'] and not writes


def test_old_zero_candidate_report_without_measured_context_is_not_a_long_source(case):
    state,candidate,run,changes,writes=case
    keynote_delta(changes)
    assert not F._recover_changed_production_rule(state,candidate,run)
    assert candidate['failed'] and not writes


def test_impossible_t_scene_is_not_recovered_by_proposer_it_will_skip(case):
    state,candidate,run,changes,writes=case
    keynote_delta(changes);source_evidence(candidate,run,1635.6)
    candidate['editorial_profile']='field-story-v1'
    candidate['production_failure_summary']['source_context']['resolution']=dict(width=854,height=480)
    assert not F._recover_changed_production_rule(state,candidate,run)
    assert candidate['failed'] and not writes
