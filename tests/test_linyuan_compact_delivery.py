import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
from compact_delivery import compact


def package(tmp_path):
    names = ['final_1.mp4', 'cover_1.jpg', 'subtitle_1.ass', 'subtitle_edit_proof_1.json',
             'cover_1_feed_square.jpg', 'cover_1_list_160.jpg']
    for name in names:
        (tmp_path / name).write_bytes(('original:' + name).encode())
    meta = dict(slug='ly-test', final=names[0], cover=names[1], subtitle_files=[names[2]],
        subtitle_edit_proof_version=1, subtitle_edit_proofs=[names[3]],
        cover_proof=dict(feed_safe_crop=[280, 0, 1000, 720], feed_square=names[4], thumbnail=names[5]),
        fingerprints=dict(sha256=hashlib.sha256((tmp_path / names[0]).read_bytes()).hexdigest()))
    (tmp_path / 'meta.json').write_text(json.dumps(meta))
    (tmp_path / 'unaccepted.mp4').write_bytes(b'unaccepted')
    return meta, names


def test_bundle_keeps_all_required_companions_exactly_once_and_preserves_metadata(tmp_path):
    meta, names = package(tmp_path)
    calls = []
    def validate(part, folder):
        calls.append(folder)
        assert part == meta and all((folder / name).is_file() for name in names)
    report = compact(tmp_path, 'ly-test', validate)
    assert len(calls) == 2 and report['full_validation_passed']
    assert set(p.name for p in (tmp_path / '_publish_ready').iterdir()) == {*names, 'meta.json'}
    assert report['metadata_unchanged'] and report['media_unchanged']


def test_failed_quality_gate_never_creates_a_publish_bundle(tmp_path):
    package(tmp_path)
    with pytest.raises(ValueError, match='full validation'):
        compact(tmp_path, 'ly-test', lambda *a: '字幕不合格')
    assert not (tmp_path / '_publish_ready').exists()


def test_missing_companion_is_not_silently_omitted(tmp_path):
    _, names = package(tmp_path)
    (tmp_path / names[4]).unlink()
    with pytest.raises(ValueError, match='missing'):
        compact(tmp_path, 'ly-test', lambda *a: None)


def test_changed_video_cannot_be_repackaged_as_an_accepted_delivery(tmp_path):
    meta, _ = package(tmp_path)
    (tmp_path / meta['final']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='fingerprint'):
        compact(tmp_path, 'ly-test', lambda *a: None)


def test_previous_bundle_and_originals_are_not_overwritten(tmp_path):
    _, names = package(tmp_path)
    (tmp_path / '_publish_ready').mkdir()
    (tmp_path / '_publish_ready' / 'previous.txt').write_text('previous')
    with pytest.raises(FileExistsError):
        compact(tmp_path, 'ly-test', lambda *a: None)
    assert (tmp_path / '_publish_ready' / 'previous.txt').read_text() == 'previous'
    assert (tmp_path / names[0]).read_bytes().startswith(b'original')
