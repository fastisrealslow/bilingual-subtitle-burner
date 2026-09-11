import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import landscape as L
import presentation as V
from editorial_policy import subtitle_files_text


def test_rotation_only_selects_continuous_live_cards():
    base = dict(render_mode='live_video_card', subtitle_files=['one.ass'], source_sha256='a', segments=[dict(start=1,end=130)])
    assert L.selected(base, 'landscape')
    assert not L.selected(base, 'portrait')
    assert L.selected(base) == L.selected(dict(base))
    assert not L.selected({**base, 'subtitle_files':['one.ass','two.ass']})
    for mode in ('audio_card', 'direct', 'crop'):
        assert not L.selected({**base, 'render_mode':mode}, 'landscape')
    with pytest.raises(ValueError): L.selected(base, 'invalid')


def test_reflow_preserves_every_displayed_character_and_cue_time(tmp_path):
    old = V.layout_for(720,1280,True)
    cues = [dict(start_sec=.23,end_sec=3.71,zh='我们看财务，企业要有利润',semantic_group=True),
            dict(start_sec=4.25,end_sec=7.68,zh='贵州茅台的现金流长期保持稳定',semantic_group=True)]
    V.write_ass(cues,tmp_path/'old.ass',old,'Noto Sans CJK SC')
    parsed=L.read_captions(tmp_path,['old.ass'])
    V.write_ass(parsed,tmp_path/'new.ass',L.layout(),'Noto Sans CJK SC')
    assert subtitle_files_text(tmp_path,['new.ass'])==subtitle_files_text(tmp_path,['old.ass'])
    assert L.read_captions(tmp_path,['new.ass'])==parsed
    with pytest.raises(ValueError):L.read_captions(tmp_path,['old.ass','new.ass'])


def test_landscape_regions_do_not_stretch_source_or_cover_subtitles():
    spec=L.layout();live=spec['live_region'];sub=spec['subtitle_region']
    assert spec['canvas']==dict(width=1280,height=720)
    assert abs(live['width']/live['height']-632/470)<.003
    assert live['y']+live['height']<=sub['y']
    assert sub['y']+sub['height']<=720
    assert 2*spec['subtitle_font_px']*1.448+8<=sub['height']


def test_actual_qr_in_landscape_window_is_rejected(tmp_path):
    import cv2
    import numpy as np
    frame=np.full((720,1280,3),90,dtype=np.uint8)
    qr=cv2.QRCodeEncoder_create().encode('landscape-real-source-qr')
    frame[150:350,500:700]=cv2.cvtColor(cv2.resize(qr,(200,200),interpolation=cv2.INTER_NEAREST),cv2.COLOR_GRAY2BGR)
    video=tmp_path/'qr.mp4'
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),12,(1280,720))
    for _ in range(12):writer.write(frame)
    writer.release()
    with pytest.raises(ValueError,match='二维码'):V.verify_render(video,L.layout())
