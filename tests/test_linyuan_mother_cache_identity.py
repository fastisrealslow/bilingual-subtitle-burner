from copy import deepcopy
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from prepare_asr_runtime import mother_cache_key


def choice():
    return dict(source_sha256='a'*64,backend='qwen3',
        model_revisions=dict(asr='asr-revision',aligner='aligner-revision'),
        source_overrides={'other-source':{'audio_preprocessing':'ffmpeg-mono-v1'}},
        rollout_note='note',evidence_run_id=123)


def test_unrelated_source_and_evidence_location_do_not_discard_recognition():
    before=choice();after=deepcopy(before)
    after['source_overrides']['other-source']['audio_preprocessing']='left-channel-v1'
    after.update(evidence_run_id=456,rollout_note='reviewed elsewhere')
    assert mother_cache_key(before)==mother_cache_key(after)
    after['audio_preprocessing']='ffmpeg-mono-v1'
    assert mother_cache_key(before)==mother_cache_key(after)


@pytest.mark.parametrize('key,value',[
    ('source_sha256','b'*64),('backend','sensevoice'),
    ('audio_preprocessing','left-channel-v1'),
    ('model_revisions',dict(asr='new-model',aligner='aligner-revision')),
    ('model_revisions',dict(asr='asr-revision',aligner='new-aligner')),
])
def test_actual_inference_changes_never_reuse_hypotheses(key,value):
    before=choice();after=deepcopy(before);after[key]=value
    assert mother_cache_key(before)!=mother_cache_key(after)
