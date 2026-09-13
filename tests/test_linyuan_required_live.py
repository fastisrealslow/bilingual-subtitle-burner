"""Automatic production cannot report an unpublishable static fallback as stock."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import produce_cn as p


@pytest.mark.parametrize('crop', [None, 'crop=600:1000:0:0'])
def test_required_live_rejects_before_static_render_when_cleanup_is_unsafe(monkeypatch,tmp_path,crop):
    monkeypatch.setattr(p,'argument_record_for_render',lambda *a:{})
    monkeypatch.setattr(p,'copywrite',lambda *a,**k:{'cover_title':'完整标题'})
    monkeypatch.setattr(p,'selected_native_clean_plan',lambda *a:None)
    monkeypatch.setattr(p,'reviewed_source_live_crop',lambda *a:crop)
    monkeypatch.setattr(p,'audio_card_live_crop',lambda *a:None)
    monkeypatch.setattr(p,'_render_clean_preview',lambda *a,**k:tmp_path/'preview.mp4')
    monkeypatch.setattr(p,'detect_corner_logos',lambda *a,**k:['external watermark'])
    monkeypatch.setattr(p,'make_audio_card',lambda *a,**k:pytest.fail('Cannot generate a static fallback'))
    with pytest.raises(p.VisualQualityError,match='禁用音频卡'):
        p._produce_one(tmp_path/'source.mp4',tmp_path,tmp_path,
            [dict(start=0,end=180,text='完整观点')],'林园','','',False,720,1280,'',
            source_report={'clean_strategy':'audio_card'},
            preselected_picks=[dict(start=0,end=0)],require_live_video=True)
