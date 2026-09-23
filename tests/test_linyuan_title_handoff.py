from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_handoff as H
import title_rewrite as T
import produce_cn as P

SOURCE='a'*64
MODEL='d'*64
TEXT='我们锁定医药赛道，别的不搞。'


@pytest.fixture
def handoff(tmp_path,monkeypatch):
    monkeypatch.setattr(P,'TEXT_BACKEND','local')
    monkeypatch.setenv('LINYUAN_TITLE_HANDOFF_MODEL_DIGEST',MODEL)
    base=T.generate(TEXT)
    monkeypatch.setattr(T,'generate',lambda *a,**kw:deepcopy(base))
    cues=[dict(text=TEXT,start=0,end=30)]
    result=P.copywrite(cues,[0],'林园','',None,tmp_path)
    row=dict(status='generated',experiment_valid=True,source_sha256=SOURCE,
        transcript_sha256=result['copy_identity']['transcript_sha256'],
        commit='b'*40,run_id='123',proof_error=None,result=result,model_digest=MODEL)
    path=tmp_path/'handoff.json';path.write_text(json.dumps(row,ensure_ascii=False))
    return path,row,cues


def test_exact_result_survives_new_render_workspace_without_another_model_call(handoff,tmp_path,monkeypatch):
    path,row,cues=handoff
    monkeypatch.setenv('LINYUAN_TITLE_HANDOFF',str(path))
    monkeypatch.setattr(T,'generate',lambda *a,**kw:pytest.fail('Do not regenerate the handed-off title'))
    work=tmp_path/'render';work.mkdir()
    result=P.copywrite(cues,[0],'林园','',None,work,source_sha256=SOURCE)
    assert result['title']==row['result']['title']
    assert result['cover_title']==row['result']['cover_title']
    assert result['title_handoff']['generation_run_id']=='123'
    assert result['title_handoff']['editorial_approved'] is False
    assert T.error(result['title'],result['title_rewrite'],TEXT) is None


@pytest.mark.parametrize('field,value',[
    ('source_sha256','c'*64),('transcript_sha256','c'*64),
    ('status','unresolved'),('experiment_valid',False),('proof_error','old proof invalid')])
def test_missing_or_changed_upstream_evidence_is_not_a_reusable_result(handoff,field,value):
    path,row,_=handoff;row[field]=value;path.write_text(json.dumps(row))
    with pytest.raises(ValueError):H.load_result(path,row['result']['copy_identity'],SOURCE,MODEL)


@pytest.mark.parametrize('field,value',[
    ('title_editor_sha256','different-code'),('text_model','different-model'),
    ('occasion','other-context'),('speaker','other-speaker'),('title_draft_profile','other-profile')])
def test_changed_generation_identity_rejects_old_result(handoff,field,value):
    path,row,_=handoff;identity={**row['result']['copy_identity'],field:value}
    with pytest.raises(ValueError):H.load_result(path,identity,SOURCE,MODEL)


def test_same_model_name_with_different_weights_is_not_reused(handoff):
    path,row,_=handoff
    for digest in (None,'e'*64):
        with pytest.raises(ValueError,match='模型权重'):
            H.load_result(path,row['result']['copy_identity'],SOURCE,digest)


def test_tampered_title_is_checked_again_and_never_silently_regenerated(handoff,tmp_path,monkeypatch):
    path,row,cues=handoff;row['result']['title']='林园：医药股保证翻倍'
    path.write_text(json.dumps(row,ensure_ascii=False))
    monkeypatch.setenv('LINYUAN_TITLE_HANDOFF',str(path))
    monkeypatch.setattr(T,'generate',lambda *a,**kw:pytest.fail('No fallback after a bad handoff'))
    work=tmp_path/'render';work.mkdir()
    with pytest.raises(P.EditorialReviewUnavailable,match='独立标题交接未通过'):
        P.copywrite(cues,[0],'林园','',None,work,source_sha256=SOURCE)
    assert not (work/'copywrite.json').exists()


def test_different_selected_words_cannot_use_other_segment_title(handoff,tmp_path,monkeypatch):
    path,row,cues=handoff;monkeypatch.setenv('LINYUAN_TITLE_HANDOFF',str(path))
    work=tmp_path/'render';work.mkdir()
    with pytest.raises(P.EditorialReviewUnavailable,match='完整选段字幕不一致'):
        P.copywrite([dict(text='我没有研究光伏能源。',start=0,end=30)],[0],
            '林园','',None,work,source_sha256=SOURCE)
