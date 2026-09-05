import importlib.util
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import rebuild_tv_wine_review as review


def test_subtitle_window_overlap_and_length():
    cues = [dict(start=238, end=243, text='甲' * 40),
            dict(start=242, end=250, text='乙' * 16),
            dict(start=476, end=480, text='结尾'),
            dict(start=481, end=485, text='不要其他内容')]
    entries = review.subtitle_entries(cues)
    assert all(0 <= e['start_sec'] < e['end_sec'] <= review.END-review.START
               for e in entries)
    assert all(a['end_sec'] <= b['start_sec'] for a,b in zip(entries,entries[1:]))
    assert all(len(e['zh']) <= 28 for e in entries)
    assert ''.join(e['zh'] for e in entries) == '甲'*40+'乙'*16+'结尾'


def load_fc():
    spec = importlib.util.spec_from_file_location('review_fc', ROOT/'linyuan/fc/index.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reject_unrelated_target_before_network():
    fc = load_fc()
    with patch.object(fc, 'gh') as gh:
        try:
            fc.publish_tv_wine_review_once({'slug': 'other'})
            assert False
        except ValueError:
            pass
        gh.assert_not_called()


def test_existing_upload_lock_never_uploads_again():
    import base64
    fc = load_fc()
    receipt = {'status': 'uploading', 'slug': review.SLUG}
    value = {'content': base64.b64encode(json.dumps(receipt).encode()).decode()}
    with patch.object(fc,'gh',return_value=value), patch.object(fc,'download_release_part') as download:
        assert fc.publish_tv_wine_review_once({'slug':review.SLUG,'review_of_bvid':review.ORIGINAL_BVID}) == receipt
        download.assert_not_called()


def test_qr_geometry_keeps_valid_unreadable_code():
    assert review.credible_qr([[10,10],[100,10],[100,100],[10,100]],632,470)
    assert not review.credible_qr([[436,440],[70,266],[22,-169],[547,94]],632,470)
    assert not review.credible_qr([[456,0],[614,467],[614,467],[13,45]],632,470)
    assert not review.credible_qr([[10,0],[174,114],[213.6,200.6],[20,149]],632,470)
