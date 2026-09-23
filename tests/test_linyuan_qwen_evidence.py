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


def test_reviewed_0907_mothers_only_replace_verified_phrases():
    from reviewed_asr_corrections import apply_reviewed_corrections
    cases=[
        ('a7c6c8ccefd617c019f215f817c46379626008ec51da2f8ad215bfc217148b9b',
         1064.2,'那个炒小白股是吧','他就炒小盘股是吧'),
        ('a7c6c8ccefd617c019f215f817c46379626008ec51da2f8ad215bfc217148b9b',
         1158.2,'给我听我都懒是吧我没有评判的标准','给我说听我都懒得听我们有判断的标准'),
        ('a7c6c8ccefd617c019f215f817c46379626008ec51da2f8ad215bfc217148b9b',
         1313.2,'这就是资本是足力的','这就是资本是逐利的'),
        ('40da16692854170b57b3ce38b20f4f8095d1f16ad2123bb68200a4864fed47dc',
         2502.2,'机器的时好时候','机器的是好时候'),
        ('40da16692854170b57b3ce38b20f4f8095d1f16ad2123bb68200a4864fed47dc',
         2951.2,'大概在115年16年的时候','大概在15年16年的时候'),
    ]
    for source,start,before,after in cases:
        words=[dict(text=char,start=start+i*.1,end=start+(i+1)*.1)
               for i,char in enumerate(before)]
        revised,changes=apply_reviewed_corrections(words,source)
        assert ''.join(w['text'] for w in revised)==after
        assert len(changes)==1
        assert changes[0]['source_sha256']==source
        untouched,_=apply_reviewed_corrections(words,'different-source')
        assert untouched==words


def test_actual_all_output_alignment_preserves_timing_and_unreviewed_words():
    import json
    from reviewed_asr_corrections import apply_reviewed_corrections
    cases=json.loads((Path(__file__).resolve().parents[1]/
        'linyuan/simulations/benchmark-20260921/verified-caption-word-windows.json').read_text())
    for case in cases:
        words=case['words'];before=copy.deepcopy(words)
        revised,changes=apply_reviewed_corrections(words,case['source_sha256'])
        assert words==before
        assert len(changes)==(1 if case['source_id']==49 else 2)
        assert [(w['start'],w['end']) for w in revised]==[(w['start'],w['end']) for w in words]
        assert ''.join(w['text'] for w in revised)==''.join(w['text'] for w in words).replace('陈以健','成瘾性').replace('符合增长','复合增长')
        assert apply_reviewed_corrections(words,'different-source')==(words,[])
        shifted=[dict(w,start=w['start']+100,end=w['end']+100) for w in words]
        assert apply_reviewed_corrections(shifted,case['source_sha256'])==(shifted,[])


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


def test_original_caption_confirms_investment_firm_without_global_name_replacement():
    from reviewed_asr_corrections import apply_reviewed_corrections
    source='312d4ce3bdcd58f11cacbe70fdb9e3992d5d9b66ddebf459e65c0dc8203fe710'
    text='我们凌源投资是在全球范围内'
    words=[dict(text=c,start=38.29+i*.16,end=38.29+(i+1)*.16) for i,c in enumerate(text)]
    original=[dict(w) for w in words]
    changed,proof=apply_reviewed_corrections(words,source)
    assert ''.join(w['text'] for w in changed)=='我们林园投资是在全球范围内'
    assert [(w['start'],w['end']) for w in changed]==[(w['start'],w['end']) for w in words]
    assert words==original and proof[0]['before']=='凌源投资'
    assert apply_reviewed_corrections(words,'unrelated-mother')==(words,[])
    shifted=[{**w,'start':w['start']+100,'end':w['end']+100} for w in words]
    assert apply_reviewed_corrections(shifted,source)==(shifted,[])
