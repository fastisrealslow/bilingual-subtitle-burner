"""Run 39906333491 spent the story budget on a native 852x480 source."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import publishing_experiment as E
import produce_cn as P
from live_tracking import scene_cover_crop
from source_profile_feasibility import profile_for_source,native_scene_unavailable


def actual_source():
    return dict(passed=True,source_sha256='7aa7b579af1469b70f65245badda37ec30e40371b9e112bba833eba9afeea9da',
        duration_sec=3647.85,resolution=dict(width=852,height=480,short_edge=480))


def test_real_long_480p_source_keeps_request_and_runs_only_as_control(tmp_path,monkeypatch):
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE',E.TREATMENT)
    original=actual_source();before=deepcopy(original)
    requested=profile_for_source(original,tmp_path)
    review=json.loads((tmp_path/'source_profile_feasibility.json').read_text())
    assert original==before and requested==E.TREATMENT and E.profile()==E.BASELINE
    assert review['classification_skipped'] is True and review['final_quality_approved'] is False
    assert not any(k in review for k in ('selected','rejected','calls'))
    assert review['source_sha256']==original['source_sha256']
    result=E.production_result(requested,[dict(editorial_profile=E.BASELINE)],review)
    assert result['experiment_accepted']==0 and result['baseline_accepted']==1 and result['fallback'] is True
    assert not result['experiment_complete']


@pytest.mark.parametrize('size',[(1920,1080),(1484,720),(960,540),(1280,720)])
def test_usable_native_geometry_still_requires_actual_story_and_cover(tmp_path,monkeypatch,size):
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE',E.TREATMENT)
    source=actual_source();source['resolution']=dict(zip(['width','height'],size))
    requested=profile_for_source(source,tmp_path)
    assert requested==E.TREATMENT and E.profile()==E.TREATMENT
    assert not (tmp_path/'source_profile_feasibility.json').exists()
    assert native_scene_unavailable(*size) is None
    # Good source size never waives a small face or source overlay.
    with pytest.raises(ValueError):scene_cover_crop((200,100,40,50),*size,[])


@pytest.mark.parametrize('size',[(852,480),(959,540),(960,539),(720,1280),(1104,1080)])
def test_rerouting_uses_exact_unchanged_scene_crop_prerequisite(tmp_path,monkeypatch,size):
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE',E.TREATMENT)
    source=actual_source();source['resolution']=dict(zip(['width','height'],size))
    profile_for_source(source,tmp_path)
    review=json.loads((tmp_path/'source_profile_feasibility.json').read_text())
    assert review['detail']==native_scene_unavailable(*size)
    with pytest.raises(ValueError,match=review['detail']):
        scene_cover_crop((200,100,160,200),*size,[])


@pytest.mark.parametrize('change',[
    dict(passed=False),dict(source_sha256=None),dict(source_sha256='different'),
    dict(resolution={}),dict(resolution=dict(width=852,height=None)),
    dict(resolution=dict(width=True,height=480)),dict(resolution=dict(width=0,height=480)),
])
def test_unverified_or_unknown_source_never_obtains_guessed_control_approval(tmp_path,monkeypatch,change):
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE',E.TREATMENT)
    source=actual_source();source.update(change)
    requested=profile_for_source(source,tmp_path)
    assert requested==E.TREATMENT and E.profile()==E.TREATMENT
    assert not (tmp_path/'source_profile_feasibility.json').exists()


def test_baseline_is_unchanged_and_frozen_experiment_is_not_reset(tmp_path,monkeypatch):
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE',E.BASELINE)
    assert profile_for_source(actual_source(),tmp_path)==E.BASELINE
    assert not (tmp_path/'source_profile_feasibility.json').exists()
    assert E.EXPERIMENT_ID=='field-story-30d-20261018'
    assert E.VERSION==2026101801 and E.SELECTION_REVISION==2026102201


@pytest.mark.parametrize('width,height,should_classify',[(852,480,False),(1920,1080,True)])
def test_actual_cli_routes_before_classifier_without_approving_any_clip(tmp_path,monkeypatch,width,height,should_classify):
    import source_selection
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE',E.TREATMENT)
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true')
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST','true')
    monkeypatch.delenv('PUBLICATION_STATE_PATH',raising=False)
    monkeypatch.setattr(P,'BASE',tmp_path)
    monkeypatch.setattr(P,'TEXT_BACKEND','local')
    monkeypatch.setattr(P,'load_key',lambda:None)
    source=actual_source();source.update(resolution=dict(width=width,height=height),visual_identity={})
    monkeypatch.setattr(P,'run_source_quality_gate',lambda *a:source)
    monkeypatch.setattr(P,'transcribe',lambda *a:[dict(start=0,end=40,text='我们当年买了茅台，因为产品能维持长期需求。')])
    pick=dict(start=0,end=0,score=7,reason='original continuous answer')
    monkeypatch.setattr(source_selection,'select',lambda *a,**kw:[pick])
    monkeypatch.setattr(P,'story_selection_inputs',lambda *a:([pick],[]))
    monkeypatch.setattr(P,'llm',lambda *a,**kw:pytest.fail('No text request expected in this routing test'))
    class ClassifierReached(RuntimeError):pass
    def classify(*a,**kw):raise ClassifierReached
    monkeypatch.setattr(E,'choose_stories',classify)
    video=tmp_path/'source.mp4';video.write_bytes(b'test-only source gate fixture')
    monkeypatch.setattr(P.sys,'argv',['produce_cn','--source',str(video),'--slug','routing',
        '--split-highlights','--require-live-video','--dry-run'])
    if should_classify:
        with pytest.raises(ClassifierReached):P.main()
        assert E.profile()==E.TREATMENT
    else:
        assert P.main()==0 and E.profile()==E.BASELINE
        review=json.loads((tmp_path/'deliver/routing/_tmp/source_profile_feasibility.json').read_text())
        assert review['requested_profile']==E.TREATMENT and review['classification_skipped']
    assert not list((tmp_path/'deliver').rglob('final*.mp4'))


@pytest.mark.parametrize('reason',[
    '标题文案待重试：未确认嘉宾原话归属',
    '原文中未找到满足20秒且起止边界明确的连续候选',
    '原画无法通过真人画面清理门禁',
    '完整词句无法放入两行',
])
def test_profile_routing_never_reopens_unrelated_old_rejections(monkeypatch,reason):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
    import index as F
    patch='\n'.join([
        '+    from source_profile_feasibility import profile_for_source',
        '-    requested_editorial_profile=profile()',
        '+    requested_editorial_profile=profile_for_source(source_report,work)'])
    files=[dict(filename='linyuan/produce_cn.py',patch=patch),
        dict(filename='linyuan/source_profile_feasibility.py',patch='+def profile_for_source(source_report,work):'),
        dict(filename='.github/workflows/linyuan-produce-cn.yml',patch='+            _tmp/source_profile_feasibility.json')]
    monkeypatch.setattr(F,'gh',lambda *a,**k:dict(files=files))
    monkeypatch.setattr(F,'save_state',lambda *a:pytest.fail('Cannot reopen an unrelated failure'))
    e=dict(slug='old',failed=True,source_quality_rejected=True,last_error=reason,
        source_url='https://www.bilibili.com/video/BV1hS421X7Xd',source_check_attempts=2,ts=100)
    assert not F._recover_changed_production_rule(dict(published={}),e,
        dict(id=12345,head_sha='old',conclusion='failure'))
    assert e['failed'] is True and e['source_quality_rejected'] is True and e['source_check_attempts']==2
