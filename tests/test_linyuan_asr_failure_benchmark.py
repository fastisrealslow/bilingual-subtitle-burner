"""Diagnostic audio selection is source-bound, not an editorial approval."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import asr_failure_benchmark as b


def source():
    cues=[dict(start=0,end=2,text='开场。'),dict(start=38,end=41,text='必须六百万'),
          dict(start=41,end=44,text='以上基金。')]
    chunks=[dict(core_start=n,core_end=n+30,offset=max(0,n-3),duration=36,text='原始假设')
            for n in (0,30,60)]
    return cues,dict(chunks=chunks)


def test_suspect_across_cues_keeps_the_exact_original_decode_window_and_control():
    cues,report=source()
    windows=b.failure_windows(cues,[dict(suspect_quote='六百万以上基金')],report)
    assert [w['core_start'] for w in windows]==[0,30]
    assert windows[1]['offset']==27 and windows[1]['duration']==36
    assert windows[0]['sample_kind']=='same_source_control'
    assert windows[1]['sample_kind']=='actual_rejected_text'
    assert cues[1]['text']=='必须六百万' and report['chunks'][1]['text']=='原始假设'


@pytest.mark.parametrize('quote',['不存在','',None])
def test_unknown_or_missing_quote_is_not_guessed(quote):
    cues,report=source()
    with pytest.raises(ValueError):b.failure_windows(cues,[dict(suspect_quote=quote)],report)


def test_repeated_quote_cannot_select_an_arbitrary_time():
    cues,report=source();cues.append(dict(start=70,end=72,text='六百万以上基金'))
    with pytest.raises(ValueError):
        b.failure_windows(cues,[dict(suspect_quote='六百万以上基金')],report)


def test_two_models_are_immutable_and_never_claim_human_gold_or_production_approval():
    assert b.MODELS['0.6b']==('Qwen/Qwen3-ASR-0.6B','5eb144179a02acc5e5ba31e748d22b0cf3e303b0')
    assert b.MODELS['1.7b']==('Qwen/Qwen3-ASR-1.7B','7278e1e70fe206f11671096ffdd38061171dd6e5')
    script=Path(b.__file__).read_text()
    assert 'production_approved=False' in script and 'registered_stock=False' in script
    assert "reference_kind='paired hypotheses; no human gold transcript or CER'" in script
    assert "pcm_sha!=recognition['source_pcm_sha256']" in script
    assert 'offline_only()' in script and "device_map='cpu'" in script


def test_lexical_diagnostic_uses_actual_rejected_artifact_without_word_hints():
    assert b.CASES['lexical']['artifact']==12458840653
    assert b.CASES['lexical']['sha']=='8f379d9cecd36dc6b22d8022beed3a3a698aa217e8e0e48fdf4e2a575294b210'
    assert set(b.CASES['lexical'])=={'url','sha','artifact'}


def test_missing_or_extra_audio_case_is_not_silently_compared(tmp_path):
    p=tmp_path/'lexical';p.mkdir();(p/'manifest.json').write_text('[]')
    assert b.diagnostic_manifests(tmp_path,['lexical'])==[p/'manifest.json']
    for cases in ([],['lexical','lexical'],['fund'],['unknown']):
        with pytest.raises(ValueError):b.diagnostic_manifests(tmp_path,cases)


def test_all_selected_negative_audits_must_bind_the_original_text(tmp_path):
    import json,zipfile
    from editorial_policy import text_digest
    cues=[dict(text='公司原词。'),dict(text='另一处原词。')]
    archive=tmp_path/'actual.zip'
    def write(second_digest):
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('_tmp/story_selection.json',json.dumps(dict(selected=[dict(start=0,end=0),dict(start=1,end=1)])))
            for n,digest in enumerate((text_digest(cues[0]['text']),second_digest),1):
                z.writestr(f'_tmp/transcript_audit_{n}.json',json.dumps(dict(transcript_sha256=digest,issues=[dict(suspect_quote=cues[n-1]['text'])])))
    write(text_digest(cues[1]['text']))
    with zipfile.ZipFile(archive) as z:
        assert len(b.original_issues(z,cues))==2
    write('0'*64)
    with zipfile.ZipFile(archive) as z,pytest.raises(ValueError):b.original_issues(z,cues)
