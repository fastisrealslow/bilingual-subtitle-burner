"""A usable 1080p scene must not inherit a 632x470 portrait-body limitation."""
from pathlib import Path
import sys
import pytest
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import produce_cn as P
import publishing_experiment as E


def test_ordinary_scene_uses_original_pixels_and_exact_absolute_source_interval(tmp_path,monkeypatch):
    original=tmp_path/'mother.jpg';tracked=tmp_path/'portrait-body.jpg'
    Image.new('RGB',(1920,1080),'#789798').save(original)
    Image.new('RGB',(632,470),'#789798').save(tracked)
    calls=[]
    def cover(src,start,end,title,speaker,out,**kw):
        calls.append((src,start,end,kw))
        # The old ordinary route fails this real native-size boundary.
        with Image.open(src) as image:
            if image.width<960 or image.height<540:
                raise P.VisualQualityError('现场封面原始像素不足')
        assert kw['clean_scene_crop'] and kw['video_filter']==''
        assert kw['preferred_time']==950 and not kw['allow_editorial_fallback']
    monkeypatch.setattr(P,'make_cover',cover)
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE','baseline')
    old,lo,hi,_=P.cover_sample_context(original,tracked,926,1038,'crop=632:470')
    with pytest.raises(P.VisualQualityError):cover(old,lo,hi,'原审核标题','林园',tmp_path/'old.jpg',
        clean_scene_crop=False,video_filter='',preferred_time=950,allow_editorial_fallback=False)
    style,reason=P.make_live_clip_cover(original,tracked,926,1038,'原审核标题','林园',tmp_path/'new.jpg',
        clean_filter='crop=632:470',preferred_time=950,allow_editorial_fallback=True)
    assert style=='scene' and reason is None
    assert calls[-1][0:3]==(original,926,1038)
    assert E.profile()=='baseline'  # Better cover does not invent a story experiment.


def test_automatic_failed_native_scene_uses_verified_editorial_relative_body_context(tmp_path,monkeypatch):
    calls=[]
    def cover(src,start,end,title,speaker,out,**kw):
        calls.append((src,start,end,kw))
        if kw['style']=='scene':raise P.VisualQualityError('原画存在无法避开的角标')
        assert kw['style']=='editorial' and not kw['clean_scene_crop']
        assert (src,start,end)==('tracked.mp4',0,112)
        assert kw['preferred_time'] is None and kw['video_filter']==''
    monkeypatch.setattr(P,'make_cover',cover)
    style,reason=P.make_live_clip_cover('mother.mp4','tracked.mp4',926,1038,'原标题','林园',tmp_path/'new.jpg',
        preferred_time=950,allow_editorial_fallback=True)
    assert style=='editorial' and '角标' in reason and len(calls)==2


def test_explicit_scene_failure_never_falls_back_or_changes_its_verdict(tmp_path,monkeypatch):
    calls=[]
    def cover(*a,**kw):
        calls.append(kw);raise P.VisualQualityError('人脸原始像素不足96')
    monkeypatch.setattr(P,'make_cover',cover)
    with pytest.raises(P.VisualQualityError,match='不足96'):
        P.make_live_clip_cover('mother.mp4','tracked.mp4',926,1038,'原标题','林园',tmp_path/'new.jpg')
    assert len(calls)==1 and calls[0]['clean_scene_crop']


def test_explicit_photo_keeps_existing_cleaned_body_context(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(P,'make_cover',lambda *a,**kw:calls.append((a,kw)))
    assert P.make_live_clip_cover('mother.mp4','tracked.mp4',926,1038,'原标题','林园',tmp_path/'new.jpg',
        style='photo',preferred_time=950)==('photo',None)
    assert calls[0][0][:3]==('tracked.mp4',0,112)
    assert not calls[0][1]['clean_scene_crop'] and calls[0][1]['preferred_time'] is None
