"""Bad source geometry must not consume the bounded CPU copy budget."""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import produce_cn as p
import live_tracking


@pytest.mark.parametrize('failure', ['no_crop', 'source_overlay', 'face_or_crop', 'final_window', 'static_window',
                                   'repaired_corner', 'unclean_repair', 'moving_corner', None])
def test_actual_window_is_checked_before_any_title_request(monkeypatch, tmp_path, failure):
    monkeypatch.setenv('LINYUAN_STATIC_CORNER_REPAIR','1')
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
        if failure in ('repaired_corner', 'unclean_repair', 'moving_corner'):
            if 'corner-repair' in output.name:
                assert kwargs['exclusions']  # The second crop must actually avoid the finding.
            return dict(engine='yunet_sface_per_frame_cpu',passed=True,frames=3,
                decoded_frames=3,encoded_frames=3,first_crop=[200,200,400,300],last_crop=[200,200,400,300],
                framing=dict(held_frames=2,pan_frames=int(failure=='moving_corner'),cut_frames=[],
                    geometry_reframes=[],crop_samples=[dict(crop=[200,200,400,300])]))
        return {'framing': {'validated': True}}

    class CopyReached(Exception):
        pass

    def copy(*args, **kwargs):
        calls.append('CPU title request')
        raise CopyReached()

    monkeypatch.setattr(live_tracking, 'render_tracked', track)
    def verify_window(path,**kwargs):
        assert path.name in ('tracked1.mp4','tracked1-corner-repair.mp4')
        assert kwargs['live_region']==dict(x=0,y=0,width=632,height=470)
        calls.append('prepared window checked')
        if failure=='final_window':raise p.VisualQualityError('原字幕仍然残留')
        if failure in ('repaired_corner','unclean_repair','moving_corner'):
            if path.name=='tracked1.mp4':
                import json
                evidence=path.parent/'_tmp'/'live-region-tracked1';evidence.mkdir(parents=True)
                (evidence/'corner_ocr.json').write_text(json.dumps({'logos':[[.02,.02,.12,.12]]}))
                raise p.VisualQualityError('真人动态区仍有稳定来源角标：fixture')
            if failure=='unclean_repair':raise p.VisualQualityError('原字幕仍然残留')
        return {}
    monkeypatch.setattr(p,'verify_live_region_after_render',verify_window)
    def motion(path,proof_path):
        assert path.name==('tracked1-corner-repair.mp4' if failure=='repaired_corner' else 'tracked1.mp4')
        calls.append('prepared motion checked')
        if failure=='static_window':raise p.VisualQualityError('疑似照片/背景板')
        return {}
    monkeypatch.setattr(p,'verify_prepared_live_motion',motion)
    monkeypatch.setattr(p, 'copywrite', copy)
    expected = CopyReached if failure in (None,'repaired_corner') else p.VisualQualityError
    with pytest.raises(expected):
        p._produce_one(tmp_path / 'source.mp4', tmp_path, tmp_path,
            [dict(start=120, end=300, text='完整来源原话')], '林园', '', '', False, 720, 1280, '',
            source_report={'clean_strategy': 'audio_card'},
            preselected_picks=[dict(start=0, end=0)], require_live_video=True)
    if failure and failure!='repaired_corner':
        assert 'CPU title request' not in calls
    else:
        prefix=['actual moving window', 'prepared window checked']
        assert calls == prefix*(2 if failure=='repaired_corner' else 1)+['prepared motion checked', 'CPU title request']


def test_prepared_motion_failure_keeps_byte_bound_rejection_evidence(tmp_path,monkeypatch):
    import json
    import hashlib
    import live_motion
    tracked=tmp_path/'tracked.mp4';tracked.write_bytes(b'fixture')
    proof=tmp_path/'motion.json'
    def verify(path,rect):
        assert path==tracked and rect==dict(x=0,y=0,width=632,height=470)
        return dict(passed=False,moving_by_third=[0,0,0],version=live_motion.VERSION)
    monkeypatch.setattr(live_motion,'verify_window',verify)
    with pytest.raises(p.VisualQualityError,match='疑似照片'):
        p.verify_prepared_live_motion(tracked,proof)
    saved=json.loads(proof.read_text())
    assert saved['source_sha256']==hashlib.sha256(b'fixture').hexdigest()
    assert saved['passed'] is False and saved['final_checks_required'] is True
