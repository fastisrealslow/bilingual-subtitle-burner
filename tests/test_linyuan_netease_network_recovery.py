"""A proven network repair is one recovery, never a reset or media approval."""
import copy
from pathlib import Path
import sys

import pytest

sys.path[:0]=[str(Path(__file__).parents[1]/'linyuan/fc'),str(Path(__file__).parents[1]/'linyuan')]
import index as fc


@pytest.fixture
def case(monkeypatch):
    entry=dict(slug='netease-timeout',failed=True,source_url='https://www.163.com/v/video/VA7UULO86.html',
        ts=1,last_error='取源未完成：URLError: <urlopen error timed out>',failure_stage='source-fetch',
        source_check_run_id=7,source_check_attempts=2,quality_retries=1,source_check_exhausted=True,
        source_quality_rejected=False,automatic_rule_recoveries={'download-netease-player-v1':{'run_id':6}})
    state=dict(dispatched=[entry],published={},daily_publish={'count':3})
    run=dict(id=7,conclusion='failure',head_sha='old',updated_at='2026-10-22T08:14:00Z')
    calls=[]
    def gh(method,path):
        assert method=='GET' and path=='/compare/old...main';calls.append(path)
        return dict(files=[dict(filename='linyuan/ci_fetch_netease.py')])
    monkeypatch.setattr(fc,'gh',gh);monkeypatch.setattr(fc,'save_state',lambda _:None)
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    return state,entry,run,calls


def test_exact_timeout_after_material_repair_reopens_only_once(case):
    state,entry,run,calls=case;before=copy.deepcopy(state)
    assert fc._recover_changed_production_rule(state,entry,run)
    assert not entry['failed'] and not entry['source_check_exhausted']
    assert entry['source_check_attempts']==2 and entry['quality_retries']==1
    assert entry['automatic_rule_recoveries']['download-netease-player-v1']=={'run_id':6}
    assert entry['automatic_rule_recoveries']['download-netease-network-v2']['run_id']==7
    assert 'source_check_run_id' not in entry
    assert state['published']==before['published'] and state['daily_publish']==before['daily_publish']
    entry['failed']=True;entry['source_check_run_id']=7
    assert not fc._recover_changed_production_rule(state,entry,run)
    assert len(calls)==1


@pytest.mark.parametrize('field,value',[
    ('failure_stage','editorial-or-render'),('source_quality_rejected',True),
    ('source_check_run_id',6),('source_url','https://www.163.com/v/video/VA7UULO86.html?token=x'),
    ('source_url','https://evil.test/v/video/VA7UULO86.html'),
    ('last_error','HTTP Error 403: Forbidden'),('last_error','原始ASR存在影响理解的疑点，禁止猜改后发布'),
    ('repair_bvid','BVposted'),
])
def test_wrong_source_identity_stage_or_content_does_not_reopen(case,field,value):
    state,entry,run,calls=case;entry[field]=value
    assert not fc._recover_changed_production_rule(state,entry,run)
    assert entry['failed']


def test_no_adapter_change_and_restricted_published_or_paused_remain_blocked(case,monkeypatch):
    state,entry,run,calls=case
    monkeypatch.setattr(fc,'gh',lambda *a:dict(files=[dict(filename='linyuan/fc/index.py')]))
    assert not fc._recover_changed_production_rule(state,entry,run)
    monkeypatch.setattr('source_admission_guard.restriction',lambda *a:'禁止转载')
    assert not fc._recover_changed_production_rule(state,entry,run)
    monkeypatch.setattr('source_admission_guard.restriction',lambda *a:None)
    state['published'][entry['slug']]={'parts':[{'bvid':'BVposted'}]}
    assert not fc._recover_changed_production_rule(state,entry,run)
