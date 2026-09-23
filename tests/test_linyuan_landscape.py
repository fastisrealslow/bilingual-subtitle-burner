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


def test_landscape_keeps_aspect_and_generated_subtitles_outside_source_scan():
    spec=L.layout();live=spec['live_region'];sub=spec['subtitle_region']
    assert spec['canvas']==dict(width=1280,height=720)
    assert abs(live['width']/live['height']-632/470)<.003
    assert live['y']==0 and live['height']==550
    assert sub['x']>=0 and sub['x']+sub['width']<=1280
    assert sub['y']>=live['y']+live['height']
    assert live['x']+live['width']<1280-L.BRAND_WIDTH-16
    assert sub['y']+sub['height']<=720
    assert 2*spec['subtitle_font_px']*1.448+8<=sub['height']


@pytest.mark.parametrize('style',['classic','quiet'])
def test_actual_qr_in_landscape_window_is_rejected(tmp_path,style):
    import cv2
    import numpy as np
    frame=np.full((720,1280,3),90,dtype=np.uint8)
    qr=cv2.QRCodeEncoder_create().encode('landscape-real-source-qr')
    frame[150:350,500:700]=cv2.cvtColor(cv2.resize(qr,(200,200),interpolation=cv2.INTER_NEAREST),cv2.COLOR_GRAY2BGR)
    video=tmp_path/'qr.mp4'
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),12,(1280,720))
    for _ in range(12):writer.write(frame)
    writer.release()
    with pytest.raises(ValueError,match='二维码'):V.verify_render(video,L.layout(style))


def test_optional_quiet_layout_grows_picture_without_hiding_source_checks(monkeypatch):
    old=L.layout('classic')
    monkeypatch.setenv('LINYUAN_LANDSCAPE_STYLE','quiet')
    spec=L.layout();live=spec['live_region'];sub=spec['subtitle_region']
    assert live['width']*live['height']>old['live_region']['width']*old['live_region']['height']
    assert abs(live['width']/live['height']-632/470)<.003
    assert sub['y']>=live['y']+live['height']
    assert sub['y']+sub['height']<=720
    assert 2*spec['subtitle_font_px']*1.448+8<=sub['height']
    assert live['x']+live['width']<1280-L.BRAND_WIDTH-16
    assert L.layout('classic')==old
    with pytest.raises(ValueError):L.layout('unknown')


def test_quiet_reflow_keeps_actual_words_and_original_cue_times(tmp_path):
    cues=[dict(start_sec=0,end_sec=3.84,zh='我们锁定医药赛道，别的不搞',semantic_group=True),
          dict(start_sec=4.12,end_sec=8.52,zh='股价还可能再跌，但便宜的时候我还在买',semantic_group=True)]
    V.write_ass(cues,tmp_path/'classic.ass',L.layout('classic'),'Noto Sans CJK SC')
    old=L.read_captions(tmp_path,['classic.ass'])
    V.write_ass(old,tmp_path/'quiet.ass',L.layout('quiet'),'Noto Sans CJK SC')
    assert L.read_captions(tmp_path,['quiet.ass'])==old


def test_readable_quiet_preserves_font_size_and_recognizes_exact_legacy_crop():
    spec=L.layout('quiet')
    assert spec['subtitle_font_px']==44
    assert L.source_window({'layout_proof':spec})==spec['live_region']
    old=dict(canvas=dict(width=1280,height=720),template='landscape-live-v4-quiet-footer',
             live_region=dict(x=237,y=0,width=806,height=600),
             subtitle_region=dict(x=180,y=600,width=920,height=120))
    assert L.source_window({'layout_proof':old})==old['live_region']
    old['live_region']['y']=1
    with pytest.raises(ValueError):L.source_window({'layout_proof':old})


def test_queue_migration_cannot_overwrite_another_render_or_accepted_stock():
    from run_landscape_stock import reserve
    plan=dict(old_slug='old',new_slug='new')
    entry=dict(slug='new',repair_of='old',stock_upgrade_status='rendering')
    state=dict(dispatched=[entry])
    reserve(state,plan,123)
    assert entry['landscape_run_id']==123
    with pytest.raises(ValueError):reserve(state,plan,456)
    entry['stock_upgrade_status']='verified'
    with pytest.raises(ValueError):reserve(state,plan,123)


def test_known_moving_source_captions_are_held_instead_of_cropping_the_face():
    with pytest.raises(ValueError,match='遮挡人物'):
        L.source_window(dict(fingerprints=dict(sha256='a0a1a9c3674e4620ad36595fde0b17abca69ddb44e17376a1734d25d76d302ec')))
    assert L.source_window(dict(fingerprints=dict(sha256='other'))) == dict(x=44,y=360,width=632,height=470)
