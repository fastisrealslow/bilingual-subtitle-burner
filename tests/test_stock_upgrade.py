import copy
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import stock_upgrade_plan as planmod
import run_stock_upgrade as worker


def plan():
    return dict(old_slug='old',new_slug='new',source_url='https://source',source_sha256='sha',
                parts=[dict(index=1,segments=[dict(start=10,end=140,reason='既有选段')])])


def state():
    return dict(dispatched=[dict(slug='old',source_url='https://source',published_parts=1,
                                ts=1,key='key',parts_total=2)],published={'old':{
        'parts':[dict(status='published',bvid='BVexisting',part_index=0)]}})


def test_existing_published_part_and_receipt_survive_upgrade_claim():
    s=state();old=copy.deepcopy(s['published'])
    assert worker.claim(s,plan())
    assert s['published']==old and s['dispatched'][0]['published_parts']==1
    assert s['dispatched'][0]['failed'] and s['dispatched'][1]['failed']
    assert s['dispatched'][1]['stock_upgrade_status']=='rendering'
    with pytest.raises(ValueError):worker.claim(s,plan())


def test_pending_ranges_are_reused_exactly_without_reselection():
    cues=[dict(start=0,end=9,text='旧片。'),dict(start=10,end=140,text='连续完整解释。')]
    assert planmod.source_ranges(cues,'sha','new',plan())==[(1,1,[dict(start=0,end=0,score=7,reason='既有选段')])]
    with pytest.raises(ValueError):planmod.source_ranges(cues,'changed','new',plan())
    cues[1]['start']=11
    with pytest.raises(ValueError):planmod.source_ranges(cues,'sha','new',plan())


def test_unknown_slug_does_not_change_normal_source_selection(monkeypatch,tmp_path):
    p=tmp_path/'plan.json';p.write_text(json.dumps(dict(batches=[plan()])))
    monkeypatch.setattr(planmod,'PLAN',p)
    assert planmod.source_ranges([],'sha','other') is None


def test_publication_during_render_is_checked_again(monkeypatch):
    s=state();worker.claim(s,plan());receipt=copy.deepcopy(s['published'])
    monkeypatch.setattr(worker.fc,'find_content_duplicate',lambda *args:dict(reason='already published'))
    monkeypatch.setattr(worker.fc.editorial,'source_reuse_error',lambda *args:None)
    result=worker.promote(s,plan(),[dict(title='old content',fingerprints={})],123)
    assert result['accepted']==[] and result['skipped'][0]['index']==0
    assert s['dispatched'][-1]['failed'] and s['published']==receipt


def test_verified_replacement_has_its_own_progress_and_keeps_old_receipts(monkeypatch):
    s=state();worker.claim(s,plan());receipt=copy.deepcopy(s['published'])
    monkeypatch.setattr(worker.fc,'find_content_duplicate',lambda *args:None)
    monkeypatch.setattr(worker.fc.editorial,'source_reuse_error',lambda *args:None)
    result=worker.promote(s,plan(),[dict(title='new title',fingerprints={'sha256':'newsha'})],123)
    assert len(result['accepted'])==1
    assert not s['dispatched'][-1]['failed'] and s['dispatched'][-1]['parts_total']==1
    assert s['dispatched'][0]['failed'] and s['published']==receipt
    assert worker.claim(s,plan()) is False


def test_duplicate_stock_exclusion_does_not_touch_other_parts_or_receipts():
    s=state();receipt=copy.deepcopy(s['published'])
    worker.exclude_duplicates(s,[dict(slug='old',index=1,published_duplicate={'bvid':'BVexisting'})])
    assert worker.fc.processed_part_indices(s['dispatched'][0])=={0,1}
    assert s['published']==receipt


def test_known_722_false_start_is_not_a_title_and_clean_old_title_survives():
    import headline_policy as h
    assert not h.complete('现在我认为不不还不还不是消费')
    item=plan();item['parts'][0].update(title='林园：这个时候你只需要考虑财务',reviewed_title=True)
    result=planmod.source_ranges([dict(start=10,end=140,text='这个时候你只需要考虑财务。')],'sha','new',item)
    assert result[0][2][0]['editorial_title']==item['parts'][0]['title']


def test_existing_audio_card_is_not_forced_into_new_live_tracking():
    item=plan();item['parts'][0]['render_mode']='audio_card'
    result=planmod.source_ranges([dict(start=10,end=140,text='原有完整观点。')],'sha','new',item)
    assert result[0][2][0]['stock_original_mode']=='audio_card'


def test_unreviewed_legacy_title_does_not_override_current_copy():
    item=plan();item['parts'][0]['title']='林园：到今天为止，我没有觉得我的方法有问题'
    result=planmod.source_ranges([dict(start=10,end=140,text='到今天为止，我没有觉得我的方法有问题。')],'sha','new',item)
    assert 'editorial_title' not in result[0][2][0]
