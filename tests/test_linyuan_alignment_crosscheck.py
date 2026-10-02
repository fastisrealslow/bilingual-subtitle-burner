import hashlib
from pathlib import Path
import sys
import wave

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from alignment_crosscheck import load_window


def test_probe_uses_only_same_audio_and_diagnostic_window(tmp_path):
    pcm=b'\x01\x00'*64000
    audio=tmp_path/'raw.wav'
    with wave.open(str(audio),'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(pcm)
    error=dict(source_pcm_sha256=hashlib.sha256(pcm).hexdigest(),offset=1,duration=2)
    assert load_window(audio,error)==pcm[32000:96000]
    with pytest.raises(ValueError,match='differ'):
        load_window(audio,{**error,'source_pcm_sha256':'another source'})
    with pytest.raises(ValueError,match='outside'):
        load_window(audio,{**error,'duration':4})
