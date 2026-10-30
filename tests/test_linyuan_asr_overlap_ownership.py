"""Replay the actual failed core seam; never interpolate words or timestamps."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import qwen_asr_evidence as Q


def actual():
    return json.loads((ROOT/'tests/fixtures/linyuan_asr_seam_20261030.json').read_text())


def owned(chunks,ranges):
    return [w for c,(a,b) in zip(chunks,ranges) for w in c['words'] if a<=w['start']<b]


def test_actual_adjacent_hypotheses_keep_one_continuous_phrase_and_raw_word_clocks():
    chunks=actual()['chunks'];before=deepcopy(chunks)
    old=owned(chunks,[(1050,1080),(1080,1110)])
    assert any(a['end']>b['start']+.001 for a,b in zip(old,old[1:]))
    assert '争孙子' in ''.join(w['text'] for w in old)
    ranges,changes=Q.ownership_ranges(chunks)
    assert ranges==[(1050,1081.56),(1081.56,1110)]
    assert changes==[dict(core_boundary=1080,selected_boundary=1081.56,shared_context=['瞎','说','我','说'])]
    words=owned(chunks,ranges)
    assert all(a['end']<=b['start']+.001 for a,b in zip(words,words[1:]))
    assert '争的都是瞎说' in ''.join(w['text'] for w in words)
    assert '争孙子' not in ''.join(w['text'] for w in words)
    assert chunks==before
    assert all(w in c['words'] for c,(a,b) in zip(chunks,ranges) for w in words if a<=w['start']<b)
    punctuated=Q.punctuated_words([dict(chunks=chunks)],words)
    assert '争的都是瞎说。' in ''.join(w['text'] for w in punctuated)
    assert Q.content(''.join(w['text'] for w in punctuated))==Q.content(''.join(w['text'] for w in words))


@pytest.mark.parametrize('change',['context','pause','clock','window'])
def test_disagreement_or_missing_pause_never_earns_a_new_seam(change):
    chunks=actual()['chunks'];right=chunks[1]
    i=next(i for i,w in enumerate(right['words']) if w['start']==1081.56)
    if change=='context':right['words'][i]['text']='不'
    elif change=='pause':right['words'][i-1]['end']=1081.4
    elif change=='clock':right['words'][i]['start']+=.02
    else:right['offset']=1081.5
    before=deepcopy(chunks)
    assert Q.ownership_ranges(chunks)==([(1050,1080),(1080,1110)],[])
    assert chunks==before


def test_clean_seam_remains_byte_identical_even_with_shared_pause_available():
    chunks=actual()['chunks']
    next(w for w in chunks[0]['words'] if w['text']=='争' and w['start']==1079.96)['end']=1080.04
    assert Q.ownership_ranges(chunks)==([(1050,1080),(1080,1110)],[])


def test_source_model_or_audio_coverage_failures_are_still_rejected():
    r=actual();chunks=r['chunks'];prefix=dict(core_start=0,core_end=1050,text='',words=[])
    report=dict(r,chunks=[prefix,*chunks],device='cpu',networking_during_inference=False)
    # This fixture ends at 1110, so only an explicitly scoped replay can pass;
    # it cannot masquerade as the complete actual source.
    with pytest.raises(ValueError,match='complete source audio'):
        Q.validated_words([report],r['source_pcm_sha256'],r['source_video_sha256'],r['duration'])
    with pytest.raises(ValueError,match='different source'):
        Q.validated_words([report],'wrong',r['source_video_sha256'],1110)
