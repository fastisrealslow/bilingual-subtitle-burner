"""Prefer real clip expressions without weakening identity/logo/frame gates."""
import json
from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import produce_cn as P
import presentation as V


def setup_frames(monkeypatch,dirty=False):
    def extract(cmd,**kwargs):
        Image.new('RGB',(1280,720),'#789798').save(cmd[-1])
    monkeypatch.setattr(P.subprocess,'run',extract)
    checked=[]
    def logos(frames):
        checked.extend(frames)
        return ['external logo'] if dirty and any(p.name.endswith('.0.png') for p in frames) else []
    monkeypatch.setattr(P,'detect_corner_logos_in_images',logos)
    def identify(frames,reference):
        assert frames and all(p in checked for p in frames)
        if dirty:assert not any(p.name.endswith('.0.png') for p in frames)
        return frames[0],(400,120,180,240),dict(sharpness=100,cosine_score=.7,selected_frame=frames[0].name)
    monkeypatch.setattr(P,'select_verified_cover_face',identify)


def test_one_dirty_sample_does_not_force_stock_portrait(monkeypatch,tmp_path):
    setup_frames(monkeypatch,dirty=True)
    selected=[]
    def save(image,path,face,identity):
        selected.append(identity);image.save(path)
    monkeypatch.setattr(V,'save_scene_cover',save)
    out=tmp_path/'cover.jpg'
    P.make_cover('source.mp4',0,120,'医药股经营不好，我不买','林园',out,
                 style='scene',reference_path='reference.jpg')
    assert len(selected)==1 and selected[0]['selected_frame'].endswith('.1.png')
    assert out.exists() and not list(tmp_path.glob('*.png'))


def test_bad_first_composition_tries_another_verified_clip_frame(monkeypatch,tmp_path):
    setup_frames(monkeypatch)
    selected=[]
    def save(image,path,face,identity):
        selected.append(identity)
        if len(selected)==1:raise ValueError('人脸在信息流裁切之外')
        image.save(path)
    monkeypatch.setattr(V,'save_scene_cover',save)
    out=tmp_path/'cover.jpg'
    P.make_cover('source.mp4',0,120,'医药股经营不好，我不买','林园',out,
                 style='scene',reference_path='reference.jpg')
    assert len(selected)==2 and selected[0]['selected_frame']!=selected[1]['selected_frame']
    proof=json.loads(Path(str(out)+'.identity.json').read_text())
    assert proof['scene_candidates_tried']==2 and proof['sharpness']==100
    assert not list(tmp_path.glob('*.png'))
