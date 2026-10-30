import copy
import json
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from asr_context import specification,matches
from qwen_asr_evidence import validated_words
from qwen_cpu_transcript import resume_recognition,resume_alignment
from prepare_asr_runtime import cached_evidence,mother_cache_key,restore_raw_qwen_evidence


def raw(context=None):
    report=dict(version=1,source_pcm_sha256='pcm',source_video_sha256='a'*64,
        model_id='Qwen/Qwen3-ASR-0.6B',model_revision='asr',device='cpu',threads=2,
        networking_during_inference=False,core_range=[0,1],duration=1,
        audio_preprocessing='ffmpeg-source-clock-v2',
        alignment=dict(model_id='Qwen/Qwen3-ForcedAligner-0.6B',model_revision='align',
                       device='cpu',threads=2,networking_during_inference=False),
        chunks=[dict(offset=0,duration=1,core_start=0,core_end=1,text='投资。',
                     words=[dict(text='投资',start=.1,end=.3)])])
    if context:report['recognition_context']=context
    return report


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False))
    return path


def test_default_has_no_context_and_preserves_original_hypotheses(monkeypatch):
    monkeypatch.delenv('QWEN_ASR_CONTEXT_PROFILE',raising=False)
    assert specification()=={'text':'','identity':{}}
    assert matches([raw()])
    assert len(validated_words([raw()],'pcm','a'*64,1))==1


def test_production_pilot_is_bound_to_one_verified_source_hash(monkeypatch):
    monkeypatch.delenv('QWEN_ASR_CONTEXT_PROFILE',raising=False)
    source='654a0744864b4606d6e4b340d1ac32ee30695b157050ecf2fb4883fbf0f7e084'
    identity=specification(source_sha=source)['identity']
    assert identity['profile']=='finance-v1'
    assert specification(source_sha='a'*64)['identity']=={}
    assert matches([raw(identity)],source_sha=source)
    assert not matches([raw()],source_sha=source)
    assert not matches([raw(identity)],source_sha='a'*64)
    monkeypatch.setenv('QWEN_ASR_CONTEXT_PROFILE','none')
    assert specification(source_sha=source)['identity']=={}
    assert specification('finance-v1',source_sha=source)['identity']==identity


def test_unknown_configured_profile_fails_closed(tmp_path,monkeypatch):
    monkeypatch.delenv('QWEN_ASR_CONTEXT_PROFILE',raising=False)
    write(tmp_path/'asr_production_config.json',{
        'source_overrides':{'mother':{'recognition_context_profile':'arbitrary-answer'}}})
    with pytest.raises(ValueError,match='Unsupported'):
        specification(root=tmp_path,source_sha='mother')
    assert specification(root=tmp_path,source_sha='other')['identity']=={}


def test_context_candidate_search_preserves_bounded_recovery(monkeypatch):
    import recover_mother_asr as recovery
    calls=[]
    def api(path):
        calls.append(path)
        return {}
    monkeypatch.setattr(recovery,'api',api)
    monkeypatch.delenv('QWEN_ASR_CONTEXT_PROFILE',raising=False)
    monkeypatch.delenv('GITHUB_WORKFLOW_REF',raising=False)
    source='654a0744864b4606d6e4b340d1ac32ee30695b157050ecf2fb4883fbf0f7e084'
    assert list(recovery.candidates(source+'-key'))==[]
    workflows=[p for p in calls if '/workflows/' in p]
    assert 'linyuan-context-preview.yml' in workflows[0]
    assert len(workflows)==3
    calls.clear()
    assert list(recovery.candidates('other-key'))==[]
    assert not any('context-preview' in p for p in calls)


def test_only_bounded_source_independent_named_vocabulary_is_supported(tmp_path):
    for name in ('custom','../source-answers.json','finance-v2'):
        with pytest.raises(ValueError):specification(name)
    write(tmp_path/'asr_finance_vocabulary.json',['林园','茅台'])
    first=specification('finance-v1',tmp_path)
    write(tmp_path/'asr_finance_vocabulary.json',['林园','茅台','股息'])
    second=specification('finance-v1',tmp_path)
    assert first['identity']!=second['identity']
    assert '不添加音频中没有的内容' in first['text']
    assert len(first['identity']['text_sha256'])==64


@pytest.mark.parametrize('words',[[],['x'*25],['a\n'],['词']*129,{'answer':'text'}])
def test_invalid_vocabulary_fails_closed(tmp_path,words):
    write(tmp_path/'asr_finance_vocabulary.json',words)
    with pytest.raises(ValueError):specification('finance-v1',tmp_path)


def test_context_mismatch_invalidates_recognition_and_alignment_checkpoints(tmp_path,monkeypatch):
    monkeypatch.setenv('QWEN_ASR_CONTEXT_PROFILE','finance-v1')
    identity=specification()['identity'];old=raw();new=raw(identity)
    target=copy.deepcopy(new);target['chunks']=[]
    assert resume_recognition(target,write(tmp_path/'recognition.json',old))==0
    assert resume_alignment(new,write(tmp_path/'aligned.json',old),'align')==0
    with pytest.raises(ValueError,match='recognition context'):
        validated_words([old],'pcm','a'*64,1)
    assert resume_recognition(target,write(tmp_path/'recognition.json',new))==1
    assert resume_alignment(new,write(tmp_path/'aligned.json',new),'align')==1
    assert len(validated_words([new],'pcm','a'*64,1))==1
    monkeypatch.delenv('QWEN_ASR_CONTEXT_PROFILE')
    with pytest.raises(ValueError,match='recognition context'):
        validated_words([new],'pcm','a'*64,1)


def test_complete_old_artifact_never_satisfies_context_experiment(tmp_path,monkeypatch):
    monkeypatch.setenv('QWEN_ASR_CONTEXT_PROFILE','finance-v1')
    archive=tmp_path/'raw.zip'
    def zip_report(report):
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('_tmp/asr_raw_chunks.json',json.dumps([report]))
    zip_report(raw())
    revisions={'asr':'asr','aligner':'align'}
    assert not restore_raw_qwen_evidence(archive,tmp_path/'evidence','a'*64,revisions)
    zip_report(raw(specification()['identity']))
    assert restore_raw_qwen_evidence(archive,tmp_path/'evidence','a'*64,revisions)


def test_mother_cache_and_readiness_are_context_bound(tmp_path,monkeypatch):
    for name in ('qwen_cpu_transcript.py','qwen_asr_evidence.py','reviewed_asr_corrections.py'):
        (tmp_path/name).write_text(name)
    (tmp_path/'produce_cn.py').write_text('ASR_PIPELINE_VERSION = 1\n')
    write(tmp_path/'asr_finance_vocabulary.json',['林园','茅台'])
    source=tmp_path/'source_quality.json'
    write(source,{'source_sha256':'a'*64})
    aligned=tmp_path/'qwen_cpu/aligned.json'
    write(aligned,raw())
    choice=dict(source_sha256='a'*64,backend='qwen3',model_revisions={'asr':'asr','aligner':'align'})
    monkeypatch.delenv('QWEN_ASR_CONTEXT_PROFILE',raising=False)
    original=mother_cache_key(choice,tmp_path)
    assert cached_evidence(source,choice)
    monkeypatch.setenv('QWEN_ASR_CONTEXT_PROFILE','finance-v1')
    assert mother_cache_key(choice,tmp_path)!=original
    assert cached_evidence(source,choice) is None
    write(aligned,raw(specification()['identity']))
    assert cached_evidence(source,choice)


def test_repetition_redecode_retains_the_same_recognition_context():
    from qwen_cpu_transcript import retry_repetitive_transcript
    prefix=''.join(chr(0x4e00+i) for i in range(80))
    repeated=''.join(chr(0x5000+i) for i in range(40))
    replacement=prefix+repeated;values=iter((replacement[:60],replacement[60:]))
    calls=[]
    class Model:
        def transcribe(self,**kw):
            from types import SimpleNamespace
            calls.append(kw['context'])
            return [SimpleNamespace(text=next(values))]
    text,proof=retry_repetitive_transcript(Model(),[0]*480000,16000,
        prefix+repeated*3,context='同一个有限词表')
    assert text==replacement and proof['accepted']
    assert calls==['同一个有限词表','同一个有限词表']


def test_producer_cue_cache_cannot_treat_old_alignment_as_context_trial(tmp_path,monkeypatch):
    import hashlib
    import produce_cn as producer
    source=tmp_path/'video.mp4';source.write_bytes(b'actual mother fixture')
    source_sha=hashlib.sha256(source.read_bytes()).hexdigest()
    base=tmp_path/'config';base.mkdir()
    write(base/'asr_production_config.json',{'model_revisions':{'asr':'asr','aligner':'align'}})
    (base/'reviewed_asr_corrections.py').write_text('VERSION=1')
    (base/'qwen_asr_evidence.py').write_text('WORD_OWNERSHIP_VERSION=1')
    evidence=tmp_path/'evidence/aligned.json'
    report=raw();report['source_video_sha256']=source_sha
    write(evidence,report)
    monkeypatch.setattr(producer,'BASE',base)
    monkeypatch.setattr(producer,'ASR_BACKEND','qwen3')
    monkeypatch.setenv('QWEN3_EVIDENCE_DIR',str(evidence.parent))
    for name in ('QWEN3_ASR_DIR','QWEN3_ALIGNER_DIR','QWEN_ASR_CONTEXT_PROFILE'):
        monkeypatch.delenv(name,raising=False)
    assert 'recognition_context' not in producer._asr_cache_identity(source)
    monkeypatch.setenv('QWEN_ASR_CONTEXT_PROFILE','finance-v1')
    with pytest.raises(ValueError,match='缺少已验证'):
        producer._asr_cache_identity(source)
    report['recognition_context']=specification()['identity'];write(evidence,report)
    assert producer._asr_cache_identity(source)['recognition_context']==specification()['identity']
