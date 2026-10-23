"""Model identity can change for an explicit pilot; never silently relabel raw ASR."""
import hashlib,json,sys,zipfile
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import prepare_asr_runtime as R
from qwen_asr_evidence import validated_words


def report():
    return dict(source_video_sha256='a'*64,source_pcm_sha256='b'*64,device='cpu',
        networking_during_inference=False,model_id='Qwen/Qwen3-ASR-1.7B',
        model_revision=R.ASR_MODELS['Qwen/Qwen3-ASR-1.7B'],duration=1,
        audio_preprocessing='ffmpeg-source-clock-v2',
        alignment=dict(model_id='Qwen/Qwen3-ForcedAligner-0.6B',model_revision='align',
            device='cpu',networking_during_inference=False),
        chunks=[dict(core_start=0,core_end=1,text='测试',words=[
            dict(text='测',start=0,end=.5),dict(text='试',start=.5,end=1)])])


def test_default_identity_is_unchanged_and_larger_model_requires_exact_explicit_pair():
    assert R.recognition_model({})==R.DEFAULT_ASR_MODEL
    model='Qwen/Qwen3-ASR-1.7B'
    assert R.recognition_model(dict(asr_model_id=model,model_revisions=dict(asr=R.ASR_MODELS[model])))==model
    for choice in [dict(asr_model_id='unbounded/model'),dict(asr_model_id=model),
                   dict(asr_model_id=model,model_revisions=dict(asr=R.ASR_MODELS[R.DEFAULT_ASR_MODEL]))]:
        with pytest.raises(ValueError):R.recognition_model(choice)


def test_real_evidence_loader_cannot_consume_large_model_under_small_model_label(tmp_path):
    row=report();archive=tmp_path/'raw.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('asr_raw_chunks.json',json.dumps([row]))
    rev=dict(asr=row['model_revision'],aligner='align')
    assert not R.restore_raw_qwen_evidence(archive,tmp_path/'wrong','a'*64,rev)
    assert R.restore_raw_qwen_evidence(archive,tmp_path/'right','a'*64,rev,row['model_id'])
    assert not R.restore_raw_qwen_evidence(archive,tmp_path/'cross-source','c'*64,rev,row['model_id'])
    assert not R.restore_raw_qwen_evidence(archive,tmp_path/'cross-revision','a'*64,dict(asr='wrong'),row['model_id'])


def test_whole_source_pcm_and_word_ownership_guards_remain_with_explicit_large_model():
    row=report()
    with pytest.raises(ValueError):validated_words([row],'b'*64,'a'*64,1)
    assert len(validated_words([row],'b'*64,'a'*64,1,model_id=row['model_id']))==2
    for pcm,source,duration in [('c'*64,'a'*64,1),('b'*64,'c'*64,1),('b'*64,'a'*64,2)]:
        with pytest.raises(ValueError):validated_words([row],pcm,source,duration,model_id=row['model_id'])


def test_larger_fresh_recognition_releases_text_ram_without_changing_text_model(monkeypatch):
    import produce_cn as P
    monkeypatch.setattr(P,'TEXT_BACKEND','local')
    monkeypatch.setattr(P.shutil,'which',lambda _: '/tool/ollama')
    calls=[]
    monkeypatch.setattr(P.subprocess,'run',lambda args,**kw:calls.append((args,kw)))
    P.release_local_text_weights_for_asr(R.DEFAULT_ASR_MODEL)
    assert not calls
    P.release_local_text_weights_for_asr('Qwen/Qwen3-ASR-1.7B')
    assert len(calls)==1 and calls[0][0]==['/tool/ollama','stop',P.LOCAL_LLM_MODEL]
    assert calls[0][1]['check'] is True and calls[0][1]['timeout']==30
