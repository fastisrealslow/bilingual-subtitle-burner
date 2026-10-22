"""Prove a tiny shared-end repetition repair without editing raw ASR or starts."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan'),str(ROOT/'linyuan/fc')]
import caption_lines as C
import caption_readability as R
import index as F


def actual_entry():
    return dict(zh='个个',start_sec=1799.96,end_sec=1800.12,
        caption_chars=[['个',1799.96,1800.12],['个',1800.04,1800.12]])


def test_real_shared_end_pair_retains_text_starts_and_original_evidence():
    entry=actual_entry();before=deepcopy(entry)
    atoms=C.character_anchors(entry)
    assert atoms==[('个',1799.96,1800.04),('个',1800.04,1800.12)]
    cleaned,proof=R.clean_entries([entry])
    assert entry==before and proof['raw_entries']==[before]
    assert cleaned[0]['zh']=='个个' and proof['raw_text']==proof['display_text']=='个个'
    assert [a[1] for a in cleaned[0]['caption_chars']]==[1799.96,1800.04]
    clock=proof['source_clock_proof']
    assert clock['version']==C.SHARED_END_CLOCK_VERSION and len(clock['adjustments'])==1
    assert clock['adjustments'][0]['original']==before['caption_chars'][0]
    assert R.replay_edit_proof(proof)==('个个','个个')


@pytest.mark.parametrize('kind',['missing','version','old_end','new_end','cap'])
def test_clock_transformation_must_be_exactly_replayable(kind):
    _,proof=R.clean_entries([actual_entry()])
    if kind=='missing':proof.pop('source_clock_proof')
    elif kind=='version':proof['source_clock_proof']['version']+=1
    elif kind=='old_end':proof['source_clock_proof']['adjustments'][0]['original'][2]+=.01
    elif kind=='new_end':proof['source_clock_proof']['adjustments'][0]['normalized'][2]+=.01
    else:proof['source_clock_proof']['maximum_overlap_seconds']=.2
    with pytest.raises(ValueError,match='source_clock_proof'):R.replay_edit_proof(proof)


@pytest.mark.parametrize('atoms',[
    [('个',0,.2),('个',.08,.2)],  # >100ms
    [('个',0,.16),('期',.08,.16)],
    [('一',0,.16),('一',.08,.16)],
    [('不',0,.16),('不',.08,.16)],
    [('个',0,.16),('个',.08,.18)],  # Not a shared end
    [('个',0,.16),('个',0,.16)],
    [('个',0,.09),('个',.01,.09)],  # Do not invent a near-zero first word
    [('个',.1,.16),('个',.08,.16)],
])
def test_other_overlaps_are_still_rejected(atoms):
    entry=dict(zh=''.join(a[0] for a in atoms),start_sec=0,end_sec=.2,caption_chars=atoms)
    with pytest.raises(ValueError,match='倒退或重叠'):C.character_anchors(entry)


def test_unaffected_proof_is_unchanged_and_cannot_claim_an_unneeded_clock_repair():
    entry=dict(zh='原话不变。',start_sec=0,end_sec=2)
    _,proof=R.clean_entries([entry])
    assert 'source_clock_proof' not in proof
    assert R.replay_edit_proof(proof)==(proof['raw_text'],proof['display_text'])
    proof['source_clock_proof']=dict(version=C.SHARED_END_CLOCK_VERSION)
    with pytest.raises(ValueError,match='source_clock_proof'):R.replay_edit_proof(proof)


def test_recovery_is_once_for_changed_caption_code_not_asr_or_primary_cover(monkeypatch):
    entry=dict(slug='clock-failure',failed=True,source_url='https://www.bilibili.com/video/BVunit',
        last_error='字幕逐字时间倒退或重叠',source_check_attempts=2,ts=1)
    state=dict(dispatched=[entry],published={})
    run=dict(id=123,head_sha='old',conclusion='failure',updated_at='2026-10-23T01:00:00Z')
    monkeypatch.setattr(F,'gh',lambda *a,**kw:dict(files=[dict(filename='linyuan/caption_lines.py')]))
    monkeypatch.setattr(F,'save_state',lambda *a:None)
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    assert F._recover_changed_production_rule(state,entry,run)
    assert entry['source_check_attempts']==2 and entry['source_check_run_id']==123
    assert 'caption-shared-end-clock-v1' in entry['automatic_rule_recoveries']
    entry['failed']=True
    assert not F._recover_changed_production_rule(state,entry,run)
    for reason in ('原始ASR存在影响理解的疑点，禁止猜改后发布','封面生成/人物/角标复检失败：原画不足'):
        other=dict(entry,last_error=reason,ts=1,automatic_rule_recoveries={})
        assert not F._recover_changed_production_rule(dict(published={}),other,run)
