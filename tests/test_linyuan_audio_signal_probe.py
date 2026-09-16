import sys
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from audio_signal_probe import measure,probe


def test_opposite_phase_speech_band_and_mains_hum_are_distinguished():
    t=np.arange(16000)/16000
    speech=np.sin(2*np.pi*500*t)*.2
    row=measure(np.column_stack((speech,-speech)))
    assert row['stereo_correlation']==pytest.approx(-1)
    assert row['mono_to_loudest_channel_ratio']<1e-6
    assert min(row['speech_band_energy_fraction'])>.99
    hum=measure(np.sin(2*np.pi*50*t)[:,None])
    assert hum['hum_40_70hz_energy_fraction'][0]>.99
    assert hum['speech_band_energy_fraction'][0]<.01


def test_actual_ffmpeg_downmix_cancellation_is_measured_without_rewriting_source(tmp_path):
    import wave,hashlib
    t=np.arange(32000)/16000
    channel=(np.sin(2*np.pi*500*t)*10000).astype('<i2')
    source=tmp_path/'anti-phase.wav'
    with wave.open(str(source),'wb') as f:
        f.setnchannels(2);f.setsampwidth(2);f.setframerate(16000)
        f.writeframes(np.column_stack((channel,-channel)).astype('<i2').tobytes())
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    result=probe(source)
    assert result['channels']==2 and result['final_quality_approved'] is False
    assert all(r['ffmpeg_asr_mono_rms']<1e-6 for r in result['samples'])
    assert all(min(r['channel_rms'])>.1 for r in result['samples'])
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
