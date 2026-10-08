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
    assert not audio.matches([report],audio.DEFAULT)
    corrected={**report,'audio_preprocessing':audio.LEFT}
    assert audio.matches([corrected],audio.LEFT)
    path=tmp_path/'recognition.json';path.write_text(json.dumps(report))
    assert resume_recognition(corrected,path)==0


def test_asr_uses_presentation_clock_not_decoded_sample_count(tmp_path):
    # A real AAC stream with denser samples than its timestamps reproduces the
    # published mother's 2672s PCM / 2625s media drift without a huge fixture.
    source=tmp_path/'clock.m4a'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i',
        'sine=frequency=440:sample_rate=44100:duration=12',
        '-af','asetpts=PTS/1.018','-c:a','aac',str(source)],check=True)
    duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries',
        'format=duration','-of','default=nw=1:nk=1',str(source)]))
    old=subprocess.check_output(['ffmpeg','-v','error','-i',str(source),
        '-ac','1','-ar','16000','-f','s16le','-'])
    corrected=subprocess.check_output(['ffmpeg','-v','error','-i',str(source),
        *audio.asr_args(audio.DEFAULT),'-f','s16le','-'])
    assert len(old)/32000-duration>.15
    assert abs(len(corrected)/32000-duration)<.08
    assert audio.clock_proof(source,len(corrected)/32000,audio.DEFAULT)['passed']
    import pytest
    with pytest.raises(ValueError,match='媒体时钟不一致'):
        audio.clock_proof(source,len(corrected)/32000+1,audio.DEFAULT)
