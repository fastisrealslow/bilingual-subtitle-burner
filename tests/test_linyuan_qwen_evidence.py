import copy
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from qwen_asr_evidence import validated_words


def test_aligner_punctuation_loss_is_restored_without_changing_negation():
    from qwen_asr_evidence import punctuated_words
    r=report();words=validated_words([r],'pcm','video',10)
    output=punctuated_words([r],words)
    assert ''.join(w['text'] for w in output)=='我不卖。'
    assert output[:3]==words
    assert [w['start'] for w in output]==[1,2,3,4]


def test_reviewed_phrase_cannot_change_another_source_or_time():
    from reviewed_asr_corrections import apply_reviewed_corrections,SOURCE_SHA
    words=[dict(text=c,start=182+i*.2,end=182+(i+1)*.2) for i,c in enumerate('钱是税出来的')]
    revised,changes=apply_reviewed_corrections(words,SOURCE_SHA)
    assert ''.join(w['text'] for w in revised)=='钱是睡出来的'
    assert ''.join(w['text'] for w in words)=='钱是税出来的'
    assert len(changes)==1 and changes[0]['evidence_url']
    assert apply_reviewed_corrections(words,'different-source')==(words,[])
    earlier=[dict(w,start=w['start']-100,end=w['end']-100) for w in words]
    assert apply_reviewed_corrections(earlier,SOURCE_SHA)==(earlier,[])


def report():
    return dict(source_pcm_sha256='pcm',source_video_sha256='video',device='cpu',
        networking_during_inference=False,model_id='Qwen/Qwen3-ASR-0.6B',model_revision='asr-revision',
        alignment=dict(device='cpu',networking_during_inference=False,
            model_id='Qwen/Qwen3-ForcedAligner-0.6B',model_revision='align-revision'),
        chunks=[dict(core_start=0,core_end=10,text='我不卖。',words=[
            dict(text='我',start=1,end=2),dict(text='不',start=2,end=3),dict(text='卖',start=3,end=4)])])


def test_offline_alignment_cannot_drop_a_negation():
    r=report()
    assert ''.join(w['text'] for w in validated_words([r],'pcm','video',10))=='我不卖'
    r['chunks'][0]['words'].pop(1)
    with pytest.raises(ValueError,match='changed or lost'):
        validated_words([r],'pcm','video',10)


def test_wrong_source_or_incomplete_audio_is_not_reusable():
    with pytest.raises(ValueError,match='different source'):
        validated_words([report()],'other-pcm','video',10)
    r=report();r['chunks'][0]['core_start']=1
    with pytest.raises(ValueError,match='missing or duplicated'):
        validated_words([r],'pcm','video',10)
    with pytest.raises(ValueError,match='missing or duplicated'):
        validated_words([report(),report()],'pcm','video',10)


def test_only_cpu_offline_evidence_can_enter_production():
    r=report();r['networking_during_inference']=True
    with pytest.raises(ValueError,match='inference provenance'):
        validated_words([r],'pcm','video',10)
