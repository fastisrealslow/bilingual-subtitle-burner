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
