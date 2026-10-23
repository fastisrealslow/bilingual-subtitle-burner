"""Run 39910123732: reject inset portraits and doomed covers before encoding."""
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import produce_cn as P


@pytest.mark.parametrize('live_sharp',[False,True])
def test_native_speaker_cannot_be_replaced_by_sharp_book_portrait(tmp_path,monkeypatch,live_sharp):
    import cv2
    reference=tmp_path/'ref.jpg';reference.touch()
    scene=tmp_path/'source.jpg'
    monkeypatch.setattr(P,'_local_face_models',lambda:('detector','recognizer'))
    image=np.zeros((720,1484,3),dtype=np.uint8)
    image[229:387,1134:1256]=1
    image[73:147,116:163]=2
    monkeypatch.setattr(cv2,'imread',lambda path:np.zeros_like(image) if path.endswith('ref.jpg') else image)
    class Detector:
        def setInputSize(self,*_):pass
        def detect(self,im):
            return True,np.array([[0,0,120,160,1.]]) if not im.any() else np.array([
                [1134,229,122,158,.7581],[116,73,47,74,.4632]])
    class Recognizer:
        def alignCrop(self,im,face):return face[-1]
        def feature(self,aligned):return aligned
        def match(self,ref,feature,*_):return feature
    monkeypatch.setattr(cv2.FaceDetectorYN,'create',lambda *a,**k:Detector())
    monkeypatch.setattr(cv2.FaceRecognizerSF,'create',lambda *a,**k:Recognizer())
    monkeypatch.setattr(cv2,'Laplacian',lambda im,*a:np.array([0,40 if im[0,0]==2 or live_sharp else 2]))
    _,box,proof=P.select_verified_cover_face([scene],reference)
    assert box==(1134,229,122,158)
    assert proof['matched_faces']==2 and proof['native_matching_faces']==1
    assert (proof['sharpness']>=60)==live_sharp


def story_case(tmp_path):
    return dict(src=tmp_path/'source.mp4',work=tmp_path,out=tmp_path,
        cues=[dict(start=926.6,end=1037.88,text='我们退出银行，因为看不清风险。')],
        speaker='林园',occasion='访谈',api_key='',existing_subtitles=False,
        W=1484,H=720,suffix='',preselected_picks=[dict(start=0,end=0,
            story_selection=dict(kind='personal_story',subject_quote='银行',
                event_quote='我们退出银行',detail_quote='因为看不清风险'))])


def test_bad_scene_never_spends_argument_title_or_body_budget_or_retries_layout(tmp_path,monkeypatch):
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE','field-story-v1')
    monkeypatch.setenv('OUTPUT_LAYOUT','portrait')
    monkeypatch.setattr(P,'_download_speaker_reference',lambda *a:tmp_path/'ref.jpg')
    calls=[]
    def cover(*a,**kw):
        calls.append((a,kw))
        assert a[1:3]==(926.6,1037.88) and a[3]==''
        assert kw['clean_scene_crop'] and kw['style']=='scene' and not kw['allow_editorial_fallback']
        raise P.VisualQualityError('现场原画封面人物身份或清晰度不足')
    monkeypatch.setattr(P,'make_cover',cover)
    monkeypatch.setattr(P,'argument_record_for_render',lambda *a:pytest.fail('Doomed cover spent text budget'))
    monkeypatch.setattr(P,'copywrite',lambda *a,**k:pytest.fail('Doomed cover spent title budget'))
    with pytest.raises(P.VisualQualityError,match='封面生成/人物/角标复检失败'):
        P._produce_with_layout_preference(**story_case(tmp_path))
    assert len(calls)==1


@pytest.mark.parametrize('profile',['field-story-v1','baseline'])
def test_good_cover_is_prepared_without_copy_and_baseline_is_unchanged(tmp_path,monkeypatch,profile):
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE',profile)
    monkeypatch.setattr(P,'_download_speaker_reference',lambda *a:tmp_path/'ref.jpg')
    calls=[]
    def cover(*a,**kw):
        calls.append(a[5]);a[5].write_bytes(b'verified scene cover')
    monkeypatch.setattr(P,'make_cover',cover)
    class AtArgument(Exception):pass
    def argument(*a):
        assert len(calls)==(1 if profile=='field-story-v1' else 0)
        raise AtArgument
    monkeypatch.setattr(P,'argument_record_for_render',argument)
    with pytest.raises(AtArgument):P._produce_one(**story_case(tmp_path))
    if calls:assert calls==[tmp_path/'cover_16x9.jpg']
