"""A blurred identity timestamp should not force every cover to a stock portrait."""
import json
from pathlib import Path
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import produce_cn as P
import editorial_cover as C


@pytest.mark.parametrize('clear_extra', [True, False])
def test_wider_search_keeps_face_text_and_sharpness_gates(tmp_path, monkeypatch, clear_extra):
    times = []; checked = []; candidate_lists = []
    def extract(cmd, **kwargs):
        times.append(float(cmd[cmd.index('-ss')+1]))
        Image.new('RGB', (1280,720), '#789798').save(cmd[-1])
    def corners(frames):
        checked.extend(frames)
        return ['external logo'] if any('.wide-0.' in p.name for p in frames) else []
    def identify(frames, ref):
        candidate_lists.append(list(frames))
        assert not any('.wide-0.' in p.name for p in frames)
        extras=[p for p in frames if '.wide-' in p.name]
        path=extras[-1] if extras and clear_extra else frames[0]
        return path,(200,100,180,240),dict(sharpness=90 if extras and clear_extra else 20,
            cosine_score=.7, threshold=.363, selected_frame=path.name)
    monkeypatch.setattr(P.subprocess, 'run', extract)
    monkeypatch.setattr(P, 'detect_corner_logos_in_images', corners)
    monkeypatch.setattr(P, 'select_verified_cover_face', identify)
    monkeypatch.setattr(C, 'font_path', lambda: 'test-font')
    monkeypatch.setattr(C, 'render', lambda image,out,*args: (image.save(out) or {'style':'editorial'}))
    out=tmp_path/'cover.jpg'
    if clear_extra:
        P.make_cover('source.mp4',100,130,'杠杆不要加，不提倡','林园',out,
            style='editorial',preferred_time=115,reference_path='identity.jpg')
        proof=json.loads(Path(str(out)+'.proof.json').read_text())
        assert proof['source_identity']['sharpness']==90
        assert proof['source_identity']['sharpness_floor_unchanged']==60
    else:
        with pytest.raises(P.VisualQualityError,match='清晰度不足'):
            P.make_cover('source.mp4',100,130,'杠杆不要加，不提倡','林园',out,
                style='editorial',preferred_time=115,reference_path='identity.jpg')
    assert len(times)>3 and len(times)<=10 and all(100<=t<=130 for t in times)
    assert len(candidate_lists)==2
    assert all(frame in checked for frame in candidate_lists[-1])
    assert not list(tmp_path.glob('*.png'))


def test_whole_mother_identity_time_does_not_collapse_clip_samples(tmp_path, monkeypatch):
    times=[]
    def extract(cmd, **kwargs):
        times.append(float(cmd[cmd.index('-ss')+1]))
        Image.new('RGB',(1280,720),'#789798').save(cmd[-1])
    monkeypatch.setattr(P.subprocess,'run',extract)
    monkeypatch.setattr(P,'detect_corner_logos_in_images',lambda frames:[])
    monkeypatch.setattr(P,'select_verified_cover_face',lambda frames,ref:
        (frames[0],(200,100,180,240),dict(sharpness=100)))
    monkeypatch.setattr(C,'font_path',lambda:'test-font')
    monkeypatch.setattr(C,'render',lambda image,out,*args:(image.save(out) or {'style':'editorial'}))
    out=tmp_path/'cover.jpg'
    P.make_cover('source.mp4',119.56,149.56,'我有定价权，我说了算','林园',out,
        style='editorial',preferred_time=454.2,reference_path='identity.jpg')
    assert len(times)==7 and max(times)-min(times)>=17.9
    proof=json.loads(Path(str(out)+'.proof.json').read_text())
    assert proof['source_identity']['ignored_out_of_segment_time']==454.2
