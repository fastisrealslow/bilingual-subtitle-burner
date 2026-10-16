import hashlib
import json
from pathlib import Path
import sys
import wave

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import asr_failure_ab as ab


def samples(tmp_path):
    rows = []
    for start, duration, ident in ab.SAMPLES:
        wav = tmp_path / (ident + '.wav')
        with wave.open(str(wav), 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b'\0' * duration * 32000)
        rows.append(dict(id=ident, file=wav.name, source_sha256=ab.SOURCE_SHA,
                         source_pcm_sha256=ab.SOURCE_PCM_SHA,
                         offset=start, duration=duration, sha256=ab.sha(wav)))
    ab.save(tmp_path / 'manifest.json', rows)
    return rows


def test_same_audio_hash_and_clock_required(tmp_path):
    rows = samples(tmp_path)
    assert ab.validate_samples(tmp_path) == rows
    rows[0]['offset'] += 1
    ab.save(tmp_path / 'manifest.json', rows)
    with pytest.raises(ValueError, match='identity changed'):
        ab.validate_samples(tmp_path)


@pytest.mark.parametrize('change', ['hash', 'path', 'source', 'pcm', 'count'])
def test_changed_samples_fail_closed(tmp_path, change):
    rows = samples(tmp_path)
    if change == 'hash': rows[0]['sha256'] = 'a' * 64
    elif change == 'path': rows[0]['file'] = '../other.wav'
    elif change == 'source': rows[0]['source_sha256'] = 'b' * 64
    elif change == 'pcm': rows[0]['source_pcm_sha256'] = 'b' * 64
    else: rows.pop()
    ab.save(tmp_path / 'manifest.json', rows)
    with pytest.raises(ValueError): ab.validate_samples(tmp_path)


def test_wrong_mother_rejected_before_extracting(tmp_path):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'wrong source')
    with pytest.raises(ValueError, match='Mother hash'):
        ab.prepare(source, tmp_path / 'audio')
    assert not (tmp_path / 'audio').exists()


def test_readonly_experiment_isolated_from_publishing():
    root = Path(__file__).resolve().parents[1]
    raw = (root / '.github/workflows/linyuan-asr-failure-ab.yml').read_text()
    workflow = yaml.safe_load(raw)
    assert workflow['permissions'] == {'contents': 'read', 'actions': 'read'}
    assert workflow['jobs']['compare']['strategy']['fail-fast'] is False
    assert workflow['jobs']['compare']['strategy']['matrix']['profile'] == list(ab.PROFILES)
    assert 'fc_http_client' not in raw and 'auto_publish' not in raw
    assert all(len(revision) == 40 for _, revision, _ in ab.PROFILES.values())
