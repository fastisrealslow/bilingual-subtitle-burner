"""Legacy metadata is insufficient unless the archived sound actually matches."""
from pathlib import Path
import sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import audit_linyuan_legacy_ranges as A


def test_transcoded_gain_and_small_delay_match_but_other_sound_does_not():
    rng=np.random.default_rng(987)
    source=rng.normal(0,.1,30*A.RATE)
    # Preserve the original statement twice, as some real legacy edits did.
    segments=[dict(start=2,end=10),dict(start=2,end=10),dict(start=15,end=23)]
    audio=np.concatenate([source[int((s['start']+.02)*A.RATE):int((s['end']+.02)*A.RATE)] for s in segments])
    assert A.audit_segments(audio*.6,source,segments)['waveform_matches']
    other=rng.normal(0,.1,len(audio))
    assert not A.audit_segments(other,source,segments)['waveform_matches']
    changed=audio.copy();changed[9*A.RATE:13*A.RATE]=other[9*A.RATE:13*A.RATE]
    assert not A.audit_segments(changed,source,segments)['waveform_matches']


def test_a_matching_excerpt_does_not_approve_extra_content_or_silence():
    rng=np.random.default_rng(27);source=rng.normal(0,.1,12*A.RATE)
    segments=[dict(start=1,end=9)];audio=source[A.RATE:9*A.RATE]
    assert A.audit_segments(audio,source,segments)['waveform_matches']
    assert not A.audit_segments(np.r_[audio,np.zeros(A.RATE)],source,segments)['waveform_matches']
    assert not A.audit_segments(np.zeros(len(audio)),source,segments)['waveform_matches']
