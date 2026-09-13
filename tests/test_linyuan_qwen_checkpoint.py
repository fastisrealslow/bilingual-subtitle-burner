import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from qwen_cpu_transcript import resume_recognition, resume_alignment
from qwen_asr_evidence import validated_words
from restore_production_evidence import restore


def report():
    return dict(version=1,source_pcm_sha256='pcm',source_video_sha256='video',
        model_id='Qwen/Qwen3-ASR-0.6B',model_revision='asr-revision',device='cpu',threads=2,
        networking_during_inference=False,core_range=[0,60],duration=60,chunks=[
            dict(offset=0,duration=33,core_start=0,core_end=30,text='第一句。'),
            dict(offset=27,duration=33,core_start=30,core_end=60,text='第二句。')])


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False))
    return path


def aligned():
    value=report()
    value['alignment']=dict(model_id='Qwen/Qwen3-ForcedAligner-0.6B',model_revision='align-revision',
        device='cpu',threads=2,networking_during_inference=False)
    value['chunks'][0]['words']=[dict(text='第一句',start=1,end=3)]
    return value


def test_recognition_resumes_exact_prefix_and_ignores_untrusted_words(tmp_path):
    saved=aligned();saved['chunks']=saved['chunks'][:1]
    target=report();target['chunks']=[]
    assert resume_recognition(target,write(tmp_path/'recognition.json',saved))==1
    assert target['chunks']==report()['chunks'][:1]


@pytest.mark.parametrize('change',[
    lambda r:r.update(source_pcm_sha256='different-audio'),
    lambda r:r.update(source_video_sha256='different-video'),
    lambda r:r.update(model_revision='different-model'),
    lambda r:r.update(networking_during_inference=True),
    lambda r:r.update(threads=4),
    lambda r:r['chunks'].reverse(),
    lambda r:r['chunks'][0].update(core_end=29),
    lambda r:r['chunks'][1].update(offset=29),
])
def test_recognition_rejects_foreign_or_broken_prefix(tmp_path,change):
    saved=report();change(saved)
    target=report();target['chunks']=[]
    assert resume_recognition(target,write(tmp_path/'recognition.json',saved))==0
    assert target['chunks']==[]


def test_partial_alignment_still_requires_missing_audio_to_be_processed(tmp_path):
    target=report()
    assert resume_alignment(target,write(tmp_path/'aligned.json',aligned()),'align-revision')==1
    assert 'words' not in target['chunks'][1]
    with pytest.raises(ValueError,match='changed or lost'):
        validated_words([target],'pcm','video',60)
    target['chunks'][1]['words']=[dict(text='第二句',start=31,end=33)]
    assert len(validated_words([target],'pcm','video',60))==2


@pytest.mark.parametrize('change',[
    lambda r:r.update(source_pcm_sha256='other'),
    lambda r:r['alignment'].update(model_revision='old-aligner'),
    lambda r:r['chunks'][0].update(text='篡改文字'),
    lambda r:r['chunks'][0]['words'][0].update(text='漏字'),
    lambda r:r['chunks'][0]['words'][0].update(start=-1),
    lambda r:r['chunks'][0]['words'][0].update(end=float('nan')),
])
def test_alignment_rejects_mismatched_or_invalid_words(tmp_path,change):
    saved=aligned();change(saved);target=report()
    assert resume_alignment(target,write(tmp_path/'aligned.json',saved),'align-revision')==0
    assert all('words' not in c for c in target['chunks'])


def test_incident_restore_keeps_partial_work_out_of_accepted_evidence(tmp_path):
    evidence=tmp_path/'evidence';work=tmp_path/'work'
    evidence.mkdir();work.mkdir();(evidence/'qwen_cpu').mkdir()
    for folder in (evidence,work):write(folder/'source_quality.json',dict(passed=True,source_sha256='video'))
    write(evidence/'qwen_cpu'/'recognition.json',report())
    write(evidence/'qwen_cpu'/'aligned.json',aligned())
    restore(evidence,work)
    assert (work/'_partial_qwen_cpu'/'recognition.json').is_file()
    assert (work/'_partial_qwen_cpu'/'aligned.json').is_file()
    assert not (work/'qwen_cpu').exists()
    assert not (work/'cues_raw.json').exists()
    assert not (work/'asr_cache.json').exists()
