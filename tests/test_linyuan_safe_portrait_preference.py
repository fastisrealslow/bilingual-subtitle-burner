"""Portrait preference must not discard a valid native clip or weaken gates."""
from pathlib import Path
import inspect
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import produce_cn as P


@pytest.fixture
def portrait_case(monkeypatch):
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE','field-story-v1')
    monkeypatch.setenv('OUTPUT_LAYOUT','portrait')
    return dict(W=1920,H=1080,preselected_picks=[dict(start=0,end=2,story_selection={'kind':'company_case'})])


def test_real_portrait_is_returned_only_from_actual_dimensions(portrait_case,monkeypatch):
    calls=[]
    def render(**kw):
        calls.append(kw)
        assert kw['_portrait_attempt'] is True
        return dict(resolution=dict(width=720,height=1280))
    monkeypatch.setattr(P,'_produce_one',render)
    assert P._produce_with_layout_preference(**portrait_case)['resolution']['height']==1280
    assert len(calls)==1


def test_same_moutai_headroom_rejection_keeps_pristine_native_arguments(portrait_case,monkeypatch):
    calls=[];native=dict(resolution=dict(width=1740,height=972),marker='existing native clip')
    def render(**kw):
        calls.append(kw)
        if kw.get('_portrait_attempt'):
            kw['_picture_phase']['picture_started']=True
            raise ValueError('整段取景预检：源 204.73s，原始画面不足以保留完整人脸及头顶余量')
        assert kw==portrait_case
        return native
    monkeypatch.setattr(P,'_produce_one',render)
    assert P._produce_with_layout_preference(**portrait_case) is native
    assert len(calls)==2 and '_portrait_attempt' not in calls[1]


@pytest.mark.parametrize('kind',['before_picture','asr','title','cover','runtime','unknown','timeout'])
def test_never_falls_back_from_editorial_cover_or_runtime_failures(portrait_case,monkeypatch,kind):
    calls=[]
    def render(**kw):
        calls.append(kw);phase=kw['_picture_phase']
        if kind!='before_picture':phase['picture_started']=True
        if kind in {'asr','title','cover'}:phase['copy_started']=True
        if kind=='runtime':raise P.EditorialReviewUnavailable('人物检测服务不可用')
        if kind=='unknown':raise ValueError('无法核对的数据结构')
        if kind=='timeout':raise P.PartDeadlineExceeded()
        raise P.VisualQualityError(dict(before_picture='原始ASR不合格',asr='原始ASR存在影响理解的疑点',
            title='标题未保留原话限定',cover='封面人物原画不合格')[kind])
    monkeypatch.setattr(P,'_produce_one',render)
    with pytest.raises((ValueError,P.VisualQualityError,P.PartDeadlineExceeded)):
        P._produce_with_layout_preference(**portrait_case)
    assert len(calls)==1


@pytest.mark.parametrize('layout',['auto','landscape'])
def test_other_requested_layouts_never_enter_portrait_route(portrait_case,monkeypatch,layout):
    monkeypatch.setenv('OUTPUT_LAYOUT',layout)
    monkeypatch.setattr(P,'_produce_one',lambda **kw:kw)
    assert P._produce_with_layout_preference(**portrait_case)==portrait_case


def test_baseline_and_native_portrait_are_unchanged(portrait_case,monkeypatch):
    monkeypatch.setattr(P,'_produce_one',lambda **kw:kw)
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE','baseline')
    assert P._produce_with_layout_preference(**portrait_case)==portrait_case
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE','field-story-v1')
    portrait_case.update(W=720,H=1280)
    assert P._produce_with_layout_preference(**portrait_case)==portrait_case


def test_wide_pixels_cannot_be_claimed_as_portrait(portrait_case,monkeypatch):
    monkeypatch.setattr(P,'_produce_one',lambda **kw:dict(resolution=dict(width=1740,height=972)))
    with pytest.raises(P.VisualQualityError,match='真实竖版尺寸'):
        P._produce_with_layout_preference(**portrait_case)


def test_same_complete_picture_and_final_gates_are_still_required():
    body=inspect.getsource(P._produce_one)
    assert "if not portrait_story and (proposed_native" in body
    assert "suffix=f'{suffix}_portrait'" in body
    assert body.index("_picture_phase['copy_started']=True")<body.index('audit=audit_transcript(')
    assert 'if strategy == "audio_card" and not use_live_video and require_live_video:' in body
    for check in ('render_tracked(','verify_live_region_after_render(',
                  'verify_prepared_live_motion(','verify_final_live_identity(','attach_proof('):
        assert check in body
    from live_tracking import geometry_obstruction
    actual_moutai_face=[768.98,127.58,484.79,615.99]
    assert geometry_obstruction(actual_moutai_face,1920,1080)['required_region'][1]<0
