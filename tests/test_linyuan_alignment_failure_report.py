"""The core-180 production failure must not collapse to 'render failed'.

Run 36295702652 logged 2/141 invalid words. Timings below are synthetic
variants; no unobserved model output is presented as a real transcript.
"""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from production_failure_report import report_missing
from production_diagnostics import failure_category


def test_real_artifact_overrun_retains_observed_words_and_exact_source(tmp_path):
    fixture=json.loads((Path(__file__).parent/'fixtures/linyuan_actual_alignment_overrun.json').read_text())
    error=fixture['error'];work=tmp_path/'_tmp';qwen=work/'qwen_cpu';qwen.mkdir(parents=True)
    (work/'source_quality.json').write_text(json.dumps(dict(source_sha256=error['source_video_sha256'])))
    (qwen/'recognition.json').write_text(json.dumps(dict(
        source_video_sha256=error['source_video_sha256'],source_pcm_sha256=error['source_pcm_sha256'],
        chunks=[fixture['chunk']])))
    (qwen/'alignment_error.json').write_text(json.dumps(error))
    path=tmp_path/'batch_report.json'
    assert report_missing(path,{'render':{'outcome':'failure'}},'artifact-replay')
    result=json.loads(path.read_text());detail=result['rejected'][0]['alignment_diagnostic']
    assert detail['invalid_words']==error['invalid_words']
    assert detail['source_video_sha256']==error['source_video_sha256']
    assert result['accepted']==0 and result['selection_completed'] is False


def evidence(tmp_path,core=180):
    work=tmp_path/'_tmp';qwen=work/'qwen_cpu';qwen.mkdir(parents=True)
    chunk=dict(core_start=core,offset=max(0,core-3),duration=36,text='合成测试原文')
    error=dict(version=1,source_video_sha256='video',source_pcm_sha256='pcm',
        model_revision='revision',word_count=141,
        invalid_words=[dict(index=139,text='测',start=core+34,end=core+35),
                       dict(index=140,text='试',start=core+35,end=core+36)],**chunk)
    files={'source_quality.json':dict(source_sha256='video'),
           'qwen_cpu/recognition.json':dict(source_video_sha256='video',
                source_pcm_sha256='pcm',chunks=[chunk]),
           'qwen_cpu/alignment_error.json':error}
    for name,data in files.items():(work/name).write_text(json.dumps(data))
    return work


@pytest.mark.parametrize('core',[0,180,420])
def test_source_bound_failure_is_preserved_without_accepting_media(tmp_path,core):
    evidence(tmp_path,core)
    path=tmp_path/'batch_report.json'
    assert report_missing(path,{'render':{'outcome':'failure'}},'any-source')
    result=json.loads(path.read_text());rejection=result['rejected'][0]
    assert rejection['stage']=='asr-alignment'
    assert rejection['alignment_diagnostic']['core_start']==core
    assert '2/141' in rejection['reason']
    assert failure_category(rejection['reason'])=='asr_alignment'
    assert result['retryable'] is True
    assert result['accepted']==0 and result['accepted_finals']==[]
    assert result['selection_completed'] is False


@pytest.mark.parametrize('change',[
    lambda d:d.update(source_video_sha256='other'),
    lambda d:d.update(source_pcm_sha256='other'),
    lambda d:d.update(text='changed'),
    lambda d:d.update(core_start=999),
    lambda d:d.update(invalid_words=[]),
    lambda d:d.update(word_count=0),
    lambda d:d.update(model_revision=''),
])
def test_foreign_or_invalid_diagnostic_does_not_relabel_runtime_failure(tmp_path,change):
    work=evidence(tmp_path);p=work/'qwen_cpu/alignment_error.json'
    data=json.loads(p.read_text());change(data);p.write_text(json.dumps(data))
    out=tmp_path/'batch_report.json';report_missing(out,{'render':{'outcome':'failure'}},'source')
    assert json.loads(out.read_text())['rejected'][0]['stage']=='workflow-runtime'


@pytest.mark.parametrize('payload',['not json','null','[]'])
def test_damaged_diagnostic_cannot_break_fallback_report(tmp_path,payload):
    work=evidence(tmp_path);(work/'qwen_cpu/alignment_error.json').write_text(payload)
    out=tmp_path/'batch_report.json';assert report_missing(out,{'render':{'outcome':'failure'}},'source')
    assert json.loads(out.read_text())['rejected'][0]['stage']=='workflow-runtime'


def test_completed_core_does_not_mislabel_later_render_failure(tmp_path):
    work=evidence(tmp_path)
    (work/'qwen_cpu/aligned.json').write_text(json.dumps(dict(chunks=[dict(core_start=180,words=[])])))
    out=tmp_path/'batch_report.json';report_missing(out,{'render':{'outcome':'failure'}},'source')
    assert json.loads(out.read_text())['rejected'][0]['stage']=='workflow-runtime'


def test_other_failed_steps_and_existing_verdict_are_preserved(tmp_path):
    evidence(tmp_path);out=tmp_path/'batch_report.json'
    assert not report_missing(out,{'source_gate':{'outcome':'failure'}},'source')
    assert report_missing(out,{'render':{'outcome':'failure'},'model':{'outcome':'failure'}},'source')
    assert json.loads(out.read_text())['rejected'][0]['stage']=='workflow-runtime'
    before=out.read_bytes()
    assert not report_missing(out,{'render':{'outcome':'failure'}},'source')
    assert out.read_bytes()==before


@pytest.mark.parametrize('reason,category',[
    ('Forced alignment produced invalid word timing at core 180: 2/141 words','asr_alignment'),
    ('Current user is in debt','cloud_account_billing'),
    ('HTTP Error 503','service_or_timeout'),
    ('原文中未找到连续候选','selection'),
    ('工作流步骤失败，未形成素材不合格结论：render','service_or_timeout'),
])
def test_categories_keep_account_network_and_footage_separate(reason,category):
    assert failure_category(reason)==category
