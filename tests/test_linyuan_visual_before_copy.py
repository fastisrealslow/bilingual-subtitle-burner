"""Bad source geometry must not consume the bounded CPU copy budget."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import produce_cn as p
import live_tracking


@pytest.mark.parametrize('failure', ['no_crop', 'source_overlay', 'face_or_crop', 'final_window', None])
def test_actual_window_is_checked_before_any_title_request(monkeypatch, tmp_path, failure):
    calls = []
    monkeypatch.setattr(p, 'argument_record_for_render', lambda *a: {})
    monkeypatch.setattr(p, 'selected_native_clean_plan', lambda *a: None)
    monkeypatch.setattr(p, 'reviewed_source_live_crop',
                        lambda *a: None if failure == 'no_crop' else 'crop=632:470:0:0')
    monkeypatch.setattr(p, 'audio_card_live_crop', lambda *a: None)
    monkeypatch.setattr(p, '_render_clean_preview', lambda *a, **k: tmp_path / 'preview.mp4')
    monkeypatch.setattr(p, 'detect_corner_logos',
                        lambda *a, **k: ['source logo'] if failure == 'source_overlay' else [])
    monkeypatch.setattr(p, '_download_speaker_reference', lambda *a: tmp_path / 'reference.jpg')
    monkeypatch.setattr(p, '_local_face_models', lambda: ('detector', 'recognizer'))
    monkeypatch.setattr(p, 'selected_segment_exclusions', lambda *a: [])

    def track(source, start, duration, output, *args, **kwargs):
        assert (start, duration) == (120, 180)
        calls.append('actual moving window')
        if failure == 'face_or_crop':
            raise p.VisualQualityError('来源角标无法避开且保留完整人脸')
        return {'framing': {'validated': True}}

    class CopyReached(Exception):
        pass

    def copy(*args, **kwargs):
        calls.append('CPU title request')
        raise CopyReached()

    monkeypatch.setattr(live_tracking, 'render_tracked', track)
    def verify_window(path,**kwargs):
        assert path.name=='tracked1.mp4'
        assert kwargs['live_region']==dict(x=0,y=0,width=632,height=470)
        calls.append('prepared window checked')
        if failure=='final_window':raise p.VisualQualityError('原字幕仍然残留')
        return {}
    monkeypatch.setattr(p,'verify_live_region_after_render',verify_window)
    monkeypatch.setattr(p, 'copywrite', copy)
    expected = p.VisualQualityError if failure else CopyReached
    with pytest.raises(expected):
        p._produce_one(tmp_path / 'source.mp4', tmp_path, tmp_path,
            [dict(start=120, end=300, text='完整来源原话')], '林园', '', '', False, 720, 1280, '',
            source_report={'clean_strategy': 'audio_card'},
            preselected_picks=[dict(start=0, end=0)], require_live_video=True)
    if failure:
        assert 'CPU title request' not in calls
    else:
        assert calls == ['actual moving window', 'prepared window checked', 'CPU title request']
