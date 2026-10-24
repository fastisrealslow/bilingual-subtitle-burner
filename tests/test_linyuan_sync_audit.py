"""Audit recognition windows must use media PTS, including drifting AAC."""
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_linyuan_subtitle_sync import extract_published_audio


@pytest.mark.parametrize('clock_rate', [1, 1.018, .982])
def test_real_audio_audit_extraction_tracks_pts_in_both_directions(tmp_path, clock_rate):
    source = tmp_path / 'source.m4a'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
        'sine=frequency=440:sample_rate=44100:duration=12', '-af',
        f'asetpts=PTS/{clock_rate}', '-c:a', 'aac', str(source)], check=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    proof = extract_published_audio(source, tmp_path / 'published.wav')
    assert proof['passed'] and proof['drift_sec'] < .08
    old = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(source),
        '-ac', '1', '-ar', '16000', '-f', 's16le', '-'])
    if clock_rate != 1:
        assert abs(len(old)/32000-proof['source_audio_end_sec']) > .15
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
