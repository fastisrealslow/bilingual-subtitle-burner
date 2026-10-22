"""A newly repaired public adapter can recover its old extractor failure once."""
import copy
from pathlib import Path
import sys

import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'linyuan/fc'),
             str(Path(__file__).resolve().parents[1]/'linyuan')]
import index as fc

REASON='下载器不支持此素材地址，需更换来源或接通专用下载器'


@pytest.fixture
def case(monkeypatch):
    entry=dict(slug='failed',failed=True,source_url='https://www.163.com/v/video/VA7UULO86.html',
        ts=1,last_error=REASON,source_quality_rejected=True,source_check_run_id=7,
        source_check_attempts=2,quality_retries=1)
    state=dict(dispatched=[entry],published={},daily_publish=dict(count=2))
    run=dict(id=7,conclusion='failure',head_sha='old',updated_at='2026-10-22T05:04:28Z')
    calls=[]
    def gh(method,path,*args,**kwargs):
        calls.append((method,path))
        assert (method,path)==('GET','/compare/old...main')
        return dict(files=[dict(filename='linyuan/ci_fetch_netease.py')])
    monkeypatch.setattr(fc,'gh',gh)
    monkeypatch.setattr(fc,'save_state',lambda _: None)
    monkeypatch.setattr(fc,'log_event',lambda *a: None)
    return state,entry,run,calls


def test_exact_unsupported_page_recovers_once_without_old_asr_or_reset_counters(case):
    state,entry,run,calls=case
    before=copy.deepcopy(state)
    assert fc._recover_changed_production_rule(state,entry,run)
    assert not entry['failed'] and not entry['source_quality_rejected']
    assert 'source_check_run_id' not in entry, 'New HLS mux may have different bytes; do not import old ASR'
    assert entry['source_check_attempts']==2 and entry['quality_retries']==1
    assert entry['automatic_rule_recoveries']['download-netease-player-v1']['run_id']==7
    assert state['published']==before['published'] and state['daily_publish']==before['daily_publish']
    entry['failed']=True
    assert not fc._recover_changed_production_rule(state,entry,run)
    assert len(calls)==1


@pytest.mark.parametrize('url',[
    'https://www.douyin.com/video/123','http://www.163.com/v/video/VA7UULO86.html',
    'https://www.163.com/v/video/VA7UULO86.html?token=secret',
    'https://www.163.com.evil/v/video/VA7UULO86.html',
    'https://www.163.com/v/video/VA7UULO86.html#alternate',
    'https://www.163.com/other/VA7UULO86.html',
])
def test_other_extractors_or_page_shapes_are_not_reset(case,url):
    state,entry,run,calls=case
    entry['source_url']=url
    assert not fc._recover_changed_production_rule(state,entry,run)
    assert entry['failed'] and calls==[]


@pytest.mark.parametrize('field,value',[
    ('last_error','原始ASR存在影响理解的疑点，禁止猜改后发布'),
    ('last_error','实时官方源元数据明确禁止转载'),('last_error','画质不达标'),
    ('last_error','来源角标无法避开'),('repair_bvid','BVposted'),
])
def test_content_and_media_failures_are_not_download_failures(case,field,value,monkeypatch):
    state,entry,run,calls=case
    entry[field]=value
    # Unrelated adapter edits must not satisfy old picture/ASR recovery cases.
    assert not fc._recover_changed_production_rule(state,entry,run)
    assert entry['failed'] and 'download-netease-player-v1' not in entry.get('automatic_rule_recoveries',{})


def test_adapter_must_actually_have_changed_since_failed_run(case,monkeypatch):
    state,entry,run,calls=case
    monkeypatch.setattr(fc,'gh',lambda *a,**kw:dict(files=[dict(filename='linyuan/ci_fetch_generic.py')]))
    assert not fc._recover_changed_production_rule(state,entry,run)
    assert entry['failed']


def test_restricted_paused_and_already_published_sources_remain_blocked(case,monkeypatch):
    state,entry,run,calls=case
    monkeypatch.setattr('source_admission_guard.restriction',lambda *a:'禁止转载')
    assert not fc._recover_changed_production_rule(state,entry,run)
    assert calls==[]
    monkeypatch.setattr('source_admission_guard.restriction',lambda *a:None)
    monkeypatch.setattr(fc,'REVIEW_PAUSED_SLUGS',{'failed'})
    assert not fc._recover_changed_production_rule(state,entry,run)
    monkeypatch.setattr(fc,'REVIEW_PAUSED_SLUGS',set())
    state['published']['failed']=dict(parts=[dict(bvid='BVposted')])
    assert not fc._recover_changed_production_rule(state,entry,run)
    assert calls==[]
