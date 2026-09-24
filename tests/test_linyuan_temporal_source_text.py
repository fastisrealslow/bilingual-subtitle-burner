import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from temporal_source_text import changing_text_tracks


def actual_rows():
    return json.loads((Path(__file__).parent/'fixtures/linyuan_source88_central_captions.json').read_text())['evidence']


def test_actual_passed_video88_central_captions_are_detected():
    tracks=changing_text_tracks(actual_rows())
    assert tracks and max(t['distinct_frames'] for t in tracks)>=4
    assert all(len({r['frame'] for r in t['evidence']})>=3 for t in tracks)


def test_fixed_scene_sign_and_ocr_spelling_noise_are_not_caption_changes():
    rows=[dict(frame=i,text=t,confidence=.99,rect=[.1,.5,.55,.62]) for i,t in enumerate(
        ['世纪经济报道','世纪经济报道','世纪经齐报道','世纪经济报到','世纪经济报道'])]
    assert not changing_text_tracks(rows)


def test_one_frame_many_words_or_low_confidence_are_insufficient():
    assert not changing_text_tracks([{**r,'frame':0} for r in actual_rows()])
    assert not changing_text_tracks([{**r,'confidence':.5} for r in actual_rows()])
    assert not changing_text_tracks(actual_rows()[:2])


def test_unaligned_scene_labels_do_not_form_a_caption_track():
    rows=[dict(frame=i,text=t,confidence=.99,rect=[.1,.1+i*.3,.55,.2+i*.3])
          for i,t in enumerate(['资本市场','公司经营','新闻发布'])]
    assert not changing_text_tracks(rows)


def test_known_bad_video_cannot_reuse_its_old_artifact_approval():
    from fc.index import artifact_quality_error
    meta=dict(fingerprints=dict(sha256='ff83ec0b2505bf302d610af0ae2ba4c3b2427aff800a27644a3921a769cbaec3'),
              corner_review=dict(version=2026091302,passed=True),live_region_verified=True)
    assert '动态原字幕' in artifact_quality_error(meta)
