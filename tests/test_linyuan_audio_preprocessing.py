"""A real anti-phase WAV must survive extraction; incompatible evidence cannot skip ASR."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import wave

import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import audio_preprocessing as audio
import prepare_asr_runtime as runtime
from qwen_cpu_transcript import resume_recognition

SOURCE79='8749fee3a7345c3e1c2fe2e14fc0b33d33d66829f5ff48a25ca6f6daee5ae41f'
SOURCE311='753efbca9d8f40551bf32b7b02a74c9185be33ab411499e91571b4547f530da0'


def test_exact_source_binding_and_real_ffmpeg_channel_preservation(tmp_path):
    assert audio.policy(SOURCE79)==audio.LEFT
    assert audio.policy(SOURCE311)==audio.LEFT
    assert audio.policy('another-source')==audio.DEFAULT
    signal=(np.sin(np.arange(32000)*2*np.pi*440/16000)*12000).astype('<i2')
    source=tmp_path/'source.wav'
    with wave.open(str(source),'wb') as f:
        f.setnchannels(2);f.setsampwidth(2);f.setframerate(16000)
        f.writeframes(np.column_stack((signal,-signal)).tobytes())
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    def extract(args):
        pcm=subprocess.check_output(['ffmpeg','-v','error','-i',str(source),*args,'-f','s16le','-'])
        return np.frombuffer(pcm,dtype='<i2').astype(float)
    old=extract(audio.asr_args(audio.DEFAULT))
    fixed=extract(audio.asr_args(audio.LEFT))
    delivered=extract(['-af',audio.render_prefix(audio.LEFT)+'anull','-ac','1'])
    assert np.sqrt(np.mean(old**2))<1
    assert np.array_equal(fixed,signal)
    assert np.array_equal(delivered,signal)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before


def test_old_policy_cannot_skip_weight_preparation_or_reuse_checkpoint(tmp_path,monkeypatch):
    report={'source_video_sha256':SOURCE79,'chunks':[{'text':'啊'}]}
    monkeypatch.setattr('qwen_asr_evidence.load_reports',lambda _: [report])
    assert runtime.cached_evidence(tmp_path/'source.json',{'source_sha256':SOURCE79}) is None
    assert not audio.matches([report],audio.LEFT)
    assert audio.matches([report],audio.DEFAULT)
    corrected={**report,'audio_preprocessing':audio.LEFT}
    assert audio.matches([corrected],audio.LEFT)
    path=tmp_path/'recognition.json';path.write_text(json.dumps(report))
    assert resume_recognition(corrected,path)==0
