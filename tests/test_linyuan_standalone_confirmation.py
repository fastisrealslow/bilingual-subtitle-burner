"""Replay an actual failed caption without editing source words or clocks."""
from copy import deepcopy
import json
from pathlib import Path
import re
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan'),str(ROOT/'linyuan/fc')]
import produce_cn as P
import index as F
from caption_readability import clean_entries,display_payload_text
from presentation import layout_for,write_ass


def actual_entries():
    data=json.loads((ROOT/'tests/fixtures/linyuan_1025_standalone_confirmation.json').read_text())
    assert data['provenance']['source_sha256']=='c3716946a2594c878298772c08bd0f103455bee81532edd789fdb1b853b41fcc'
    return data['raw_entries']


def test_actual_failed_clip_plans_locally_with_identical_words_and_character_clocks(tmp_path,monkeypatch):
    entries=actual_entries();before=deepcopy(entries)
    cleaned,proof=clean_entries(entries)
    monkeypatch.setattr(P,'llm',lambda *a,**k:pytest.fail('This source needs no model retry'))
    layout=layout_for(720,1280,True)
    out=P.semantic_caption_entries(entries,'',layout,tmp_path/'semantic.json')
    actual=lambda rows:display_payload_text(''.join(e['zh'] for e in rows))
    assert entries==before and actual(out)==actual(cleaned)
    assert ''.join(e['zh'] for e in out).endswith('是的也是')
    spoken=lambda char:re.sub(r'[\s，。！？；：、]','',char)
    expected=[atom for entry in cleaned for atom in entry['caption_chars'] if spoken(atom[0])]
    got=[atom for entry in out for atom in entry['caption_chars'] if spoken(atom[0])]
    assert got==expected
    screens=write_ass(out,tmp_path/'subtitles.ass',layout,'Noto Sans CJK SC')
    assert out and screens
    assert all(.25<=e['end_sec']-e['start_sec']<=8 for e in out)
    assert all(len(e['lines'])==1 for e in screens)
    assert actual(screens)==actual(cleaned)


@pytest.mark.parametrize('text',['问题也是。','这也是。','但是。','也是因为市场增长。','哎，也是','也是？'])
def test_incomplete_predicates_and_unconfirmed_question_tails_remain_blocked(text):
    assert not P.caption_affirmation_ends([dict(zh=text)])


def test_only_actual_source_punctuation_can_complete_the_reply():
    assert P.caption_affirmation_ends([dict(zh='是的，也是。')])=={4}
    assert P.unfinished_caption_tail('这个行业也是')
    with pytest.raises(ValueError,match='未完成'):
        P.apply_semantic_groups([dict(start_sec=0,end_sec=3,zh='这个行业也是。')],['这个行业也是'],12,48)


def test_recovery_needs_the_actual_caption_fix_and_keeps_counters(monkeypatch):
    entry=dict(slug='caption-failure',failed=True,ts=1,source_url='https://example.test/source',
        last_error='字幕分屏未完成：超长意群局部复分失败（37字，硬上限30字）',
        source_check_attempts=3,quality_retries=1)
    state=dict(dispatched=[entry],published={})
    run=dict(id=123,head_sha='old',conclusion='failure',updated_at='2026-10-25T12:00:00Z')
    files=[dict(filename='linyuan/produce_cn.py',patch='@@ def unrelated():\n+caption_bookkeeping=True')]
    monkeypatch.setattr(F,'gh',lambda *a,**k:dict(files=files))
    writes=[];monkeypatch.setattr(F,'save_state',lambda *a:writes.append(True))
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    assert not F._recover_changed_production_rule(state,entry,run)
    files[0]['patch']='@@ def caption_affirmation_ends(entries):\n+pattern="也是(?=[。！；])"'
    assert F._recover_changed_production_rule(state,entry,run)
    assert entry['source_check_attempts']==3 and entry['quality_retries']==1
    assert entry['source_check_run_id']==123 and len(writes)==1 and not state['published']
    entry['failed']=True
    assert not F._recover_changed_production_rule(state,entry,run)


def test_caption_failure_remains_recoverable_beyond_the_latest_thirty_runs(monkeypatch):
    entry=dict(slug='older-caption',failed=True,ts=1,source_url='https://example.test/source',
        last_error='字幕分屏未完成：超长意群局部复分失败（37字，硬上限30字）',
        source_check_attempts=3)
    state=dict(dispatched=[entry],published={})
    run=dict(id=123,display_title='中文源出片 · older-caption',head_sha='old',
        conclusion='failure',updated_at='2026-10-25T12:00:00Z')
    def api(method,path):
        if '/runs?' in path:return dict(workflow_runs=[run])
        if path.startswith('/compare/'):
            return dict(files=[dict(filename='linyuan/produce_cn.py',
                patch='@@ def caption_affirmation_ends(entries):\n+pattern="也是(?=[。！；])"')])
        return {}
    monkeypatch.setattr(F,'gh',api)
    monkeypatch.setattr(F,'save_state',lambda *a:None)
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    assert F._recover_rule_backlog(state,{entry['slug']:entry})==1
    assert entry['source_check_attempts']==3 and entry['source_check_run_id']==123


@pytest.mark.parametrize('active,fresh,valid,expected',[(0,True,True,1),(6,True,True,0),
    (0,False,True,0),(0,True,False,0)])
def test_proven_repair_gets_one_normal_worker_even_when_reserve_is_full(monkeypatch,active,fresh,valid,expected):
    monkeypatch.setattr(F.time,'time',lambda:10000)
    rule='caption-standalone-reply-v1'
    entry=dict(slug='repair-caption',source_url='https://www.bilibili.com/video/BVsource',
        source='bilibili_search',title='林园访谈',output_layout='landscape',
        source_check_retry_after=9999,source_check_attempts=3,source_check_run_id=123,
        automatic_rule_recoveries={rule:dict(run_id=123,ts=9990)},
        rule_recovery_pending=dict(rule=rule,run_id=123 if valid else 456,queued_at=9990))
    state=dict(dispatched=[entry],published={},rejected=[],pending_retry=[],
               recent_context_probe_date='1970-01-01')
    stock=dict(inventory_fresh=fresh,verified_portrait=20,verified_landscape=20,
        daily_mix_usable=40,experiment_ready=dict(portrait=3,landscape=3))
    monkeypatch.setattr(F,'load_state',lambda:state)
    monkeypatch.setattr(F,'source_inventory',lambda *a,**k:stock)
    monkeypatch.setattr(F,'_collect_source_rejections',lambda *a:0)
    monkeypatch.setattr(F,'save_state',lambda *a:None)
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    monkeypatch.setattr(F,'staging_release_id',lambda:1)
    monkeypatch.setattr(F,'obsolete_review_candidates',lambda *a:[])
    monkeypatch.setattr(F.experiment,'production_deficits',lambda *a:dict(portrait=0,landscape=0))
    def pick(items,state,limit,audit):
        audit.update(records=0,candidate_count=0);return []
    monkeypatch.setattr(F,'pick',pick)
    posts=[]
    def api(method,path,body=None,**kwargs):
        if method=='POST':posts.append((path,body));return {}
        if 'runs?' in path:return dict(workflow_runs=[dict(status='in_progress')]*active)
        if F.DATA_JSON in path:return b'[]'
        if F.SOURCE_INVENTORY_KEY in path:return b'{}'
        return {}
    monkeypatch.setattr(F,'gh',api)
    result=F._dispatch_admitted({'execution_backend':'github'})
    actual=[body for path,body in posts if path.endswith(F.WF_PRODUCE+'/dispatches')]
    assert result['dispatched']==len(actual)==expected
    assert entry['source_check_attempts']==3+expected
    if expected:
        assert actual[0]['inputs']['recovery_run_id']=='123'
        assert 'rule_recovery_pending' not in entry
        assert not state['published']

