import hashlib
import importlib.util
from pathlib import Path
import wave

import pytest

spec = importlib.util.spec_from_file_location('asr_scale', Path(__file__).resolve().parents[1] / 'linyuan/check_same_source_asr_scale.py')
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)


def test_trial_cuts_exact_original_pcm_samples_without_reencoding(tmp_path):
    wav = tmp_path / 'full.wav'; cut = tmp_path / 'cut.wav'
    pcm = bytes(range(256)) * 1000
    with wave.open(str(wav), 'wb') as stream:
        stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed')); stream.writeframes(pcm)
    digest = M.cut_pcm(wav, dict(offset=1.25, duration=2.5), cut)
    expected = pcm[40000:120000]
    with wave.open(str(cut)) as stream:
        assert stream.readframes(stream.getnframes()) == expected
    assert digest == hashlib.sha256(expected).hexdigest()
    with pytest.raises(ValueError, match='Incomplete'):
        M.cut_pcm(wav, dict(offset=7, duration=20), cut)


def test_scale_trial_keeps_frozen_baseline_and_independent_model_identity():
    assert M.MODELS['0.6B'][1] == '5eb144179a02acc5e5ba31e748d22b0cf3e303b0'
    assert M.MODELS['1.7B'][1] != M.MODELS['0.6B'][1]
    assert M.CORES == (0, 1290, 1320, 1350, 1590)
