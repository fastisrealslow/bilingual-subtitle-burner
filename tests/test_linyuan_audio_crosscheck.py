import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import linyuan_audio_crosscheck as A


def test_wrong_media_bytes_never_reach_asr_or_extraction(tmp_path,monkeypatch):
    (tmp_path/'final.mp4').write_bytes(b'unrelated')
    monkeypatch.setattr(A.subprocess,'run',lambda *a,**k:pytest.fail('must reject before decoding'))
    with pytest.raises(ValueError,match='does not match'):
        A.extract(dict(file='final.mp4',final_sha256='0'*64,window=dict(start=0,end=12)),tmp_path,tmp_path)


def test_manifest_windows_are_short_and_bound_to_actual_final_sha():
    rows=json.loads(A.CORPUS.read_text())
    assert len(rows)==3
    for case in rows:
        assert len(case['final_sha256'])==64 and len(case['source_sha256'])==64
        assert 0<case['window']['end']-case['window']['start']<30
        assert case['primary_cues']
