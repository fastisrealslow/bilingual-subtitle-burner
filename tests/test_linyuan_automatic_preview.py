"""Preview must exercise production gates without publication or manual answers."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'linyuan'))
import produce_cn as p


def test_production_text_model_keeps_cpu_only_explicit_rollback_and_quality_gates():
    text=(ROOT/'.github/workflows/linyuan-produce-cn.yml').read_text()
    doc=yaml.safe_load(text)
    # PyYAML treats unquoted YAML 1.1 `on` as True.
    model=doc.get('on',doc.get(True))['workflow_dispatch']['inputs']['local_text_model']
    assert model['default']=='qwen3.5:9b'
    assert 'qwen3:8b' in model['options']
    assert doc['env']['RUN_TEXT_MODEL']=="${{ inputs.local_text_model || 'qwen3.5:9b' }}"
    assert doc['env']['LINYUAN_AUTOMATIC_ONLY']=="${{ inputs.reviewed_parts == '' && (inputs.target_parts == '' || inputs.target_parts == '0') && 'true' || 'false' }}"
    assert 'CUDA_VISIBLE_DEVICES' in text
    assert 'ollama-qwen35-9b-v1' in text


def script(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_preview_matches_production_steps_and_has_no_publication_capability():
    builder=script('build_linyuan_automatic_preview')
    doc=yaml.safe_load(builder.TARGET.read_text())
    assert doc==builder.build()
    assert doc['permissions']=={'contents':'read','actions':'read'}
    assert doc['env']['LINYUAN_AUTOMATIC_ONLY']=='true'
    assert doc['env']['RUN_REVIEWED_PARTS']==''
    text=builder.TARGET.read_text()
    for forbidden in ('ALIYUN_', 'fc/', 'workflow run', 'action-gh-release', 'publish_bilibili'):
        assert forbidden not in text
    steps=doc['jobs']['preview']['steps']
    preflight=next(s for s in steps if s.get('name','').startswith('CPU离线ASR'))
    assert preflight['env']['LINYUAN_TITLE_DRAFT_PROFILE']=='production'
    render=next(s for s in steps if s.get('id')=='render')
    assert '--max-outputs "$RUN_MAX_OUTPUTS"' in render['run']
    assert '\n+' not in render['run']
    # Validate actual generated Bash, including continuations, after replacing
    # expression placeholders with inert values (no network or rendering).
    import re
    for step in steps:
        if 'run' in step:
            body=re.sub(r'\$\{\{.*?\}\}', '', step['run'],flags=re.S)
            subprocess.run(['bash','-n'],input=body,text=True,check=True,capture_output=True)
        if step.get('uses','').startswith('actions/upload-artifact@'):
            assert step['with']['name'].startswith('preview-')


@pytest.mark.parametrize('key,value',[
    ('title','预填的标题'),('selected_parts','1'),('auto_publish',True),
    ('max_outputs',True),('max_outputs',4),('source','http://example.com/video'),
    ('occasion','访谈\nLINYUAN_AUTOMATIC_ONLY=false')])
def test_preview_rejects_editorial_answers_and_env_injection(key,value):
    with pytest.raises(ValueError):
        script('linyuan_preview_request').parse({'source':'https://example.com/video',key:value})


def test_automatic_mode_calls_real_completeness_review_instead_of_skip(monkeypatch,tmp_path):
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true')
    calls=[]
    monkeypatch.setattr(p,'review_complete_argument',lambda *args:calls.append(args) or {'checked':True})
    assert p.argument_record_for_render([],[], '林园','',tmp_path,'')=={'checked':True}
    assert len(calls)==1


def test_automatic_mode_refuses_manual_copy_and_selection(monkeypatch,tmp_path):
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true')
    with pytest.raises(p.VisualQualityError,match='manually supplied'):
        p.copywrite([],[],'林园','访谈','',tmp_path,reviewed_title='预填答案')
    with pytest.raises(p.VisualQualityError,match='editorial overrides'):
        p.review_complete_argument([],[{'editorial_title':'预填答案'}],'林园','',tmp_path,'')
    assert p.reviewed_native_cleanup({'source_sha256':'6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d'},1920,1080,459,747.24) is None
    assert p.reviewed_source_live_crop({},1920,1080) is None


def test_automatic_copy_cache_cannot_reuse_assisted_identity(monkeypatch):
    monkeypatch.delenv('LINYUAN_AUTOMATIC_ONLY',raising=False)
    assisted=p._copy_style_identity('林园')
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true')
    automatic=p._copy_style_identity('林园')
    assert automatic!=assisted and automatic['automatic_only'] is True


def test_automatic_source_ranking_is_not_a_manual_editorial_override(monkeypatch,tmp_path):
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true')
    text='企业需要现金流，因为支付货款需要现金。'
    response=dict(analysis=dict(claim_range=[0,0],reasoning_range=[0,0],conclusion_range=[0,0],
        summary='现金流与支付能力',
        completeness_reason='观点与理由在同一句中',audio_issues=[]),verdict=dict(
        standalone_opening=True,complete_argument=True,reasoning_present=True,
        natural_ending=True,requires_audio_review=False))
    calls=[]
    monkeypatch.setattr(p,'llm',lambda *a,**k:calls.append(k) or json.dumps(response))
    result=p.review_complete_argument([dict(start=0,end=140,text=text)],
        [dict(start=0,end=0,editorial_rank=0,selection_method='source_complete_answer_v1')],
        '林园','',tmp_path,'')
    assert calls and result['automatic_only'] is True and result['complete_argument'] is True


@pytest.mark.parametrize('model,profile',[
    ('qwen3:8b','production'),('qwen3.5:9b','answer_subject')])
def test_preview_supports_bounded_model_and_automatic_profile(model,profile):
    result=script('linyuan_preview_request').parse(dict(source='https://example.com/video',
        local_text_model=model,draft_profile=profile))
    assert result['RUN_TEXT_MODEL']==model
    assert result['LINYUAN_TITLE_DRAFT_PROFILE']==profile


@pytest.mark.parametrize('field,value',[
    ('local_text_model','unlisted-model'),('draft_profile','manual-answer'),
    ('local_text_model','qwen3.5:9b\nRUN_REVIEWED_PARTS=1')])
def test_preview_rejects_unbounded_model_or_profile(field,value):
    with pytest.raises(ValueError):
        script('linyuan_preview_request').parse({'source':'https://example.com/video',field:value})


def test_automatic_boundary_rejects_old_model_approval_before_model_or_cache(monkeypatch,tmp_path):
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true')
    cues=json.loads((ROOT/'tests/fixtures/linyuan_automatic_topic_tail.json').read_text())['cues']
    monkeypatch.setattr(p,'llm',lambda *a,**k:pytest.fail('structural failure reached model'))
    with pytest.raises(p.VisualQualityError,match='换题'):
        p.review_complete_argument(cues,[dict(start=0,end=len(cues)-1)],'林园','',tmp_path,'')
    assert not (tmp_path/'editorial_review.json').exists()


def test_automatic_claim_cannot_use_interviewer_question(monkeypatch,tmp_path):
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true')
    question='您对医药行业怎么看？'
    answer='医药需求长期存在，因为人会衰老，所以我们长期关注。'
    cues=[dict(start=0,end=10,text=question),dict(start=10,end=140,text=answer)]
    analysis=dict(claim_range=[0,0],reasoning_range=[1,1],conclusion_range=[1,1],
        summary='医药需求',
        completeness_reason='保留观点与理由',audio_issues=[])
    verdict=dict(standalone_opening=True,complete_argument=True,reasoning_present=True,
        natural_ending=True,requires_audio_review=False)
    monkeypatch.setattr(p,'llm',lambda *a,**k:json.dumps(dict(analysis=analysis,verdict=verdict)))
    with pytest.raises(p.EditorialReviewUnavailable,match='采访者提问'):
        p.review_complete_argument(cues,[dict(start=0,end=1)],'林园','',tmp_path,'')
    assert not (tmp_path/'editorial_review.json').exists()
    analysis['claim_range']=[1,1]
    assert p.review_complete_argument(cues,[dict(start=0,end=1)],'林园','',tmp_path,'')['complete_argument'] is True


def test_direct_cli_defaults_to_automatic_without_workflow_environment():
    import os
    env=dict(os.environ);env.pop('LINYUAN_AUTOMATIC_ONLY',None)
    result=subprocess.run([sys.executable,str(ROOT/'linyuan/produce_cn.py'),
        '--source','missing.mp4','--slug','cli-test','--target-parts','1'],
        env=env,capture_output=True,text=True,timeout=30)
    assert result.returncode!=0
    assert 'Automatic mode cannot use reviewed ranges or fixed editorial structures' in result.stderr


@pytest.mark.parametrize('speaker,address',[('林园','林远'),('张伟','张韦'),('李明','陈明')])
def test_ambiguous_named_addressee_is_not_silently_corrected_or_attributed(monkeypatch,tmp_path,speaker,address):
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true')
    cues=[dict(start=0,end=10,text=f'想问一下{address}总，您怎么看这个行业？'),
          dict(start=10,end=140,text='这个行业需求稳定，我们长期关注。')]
    monkeypatch.setattr(p,'llm',lambda *a,**k:pytest.fail('ambiguous addressee reached costly model inference'))
    with pytest.raises(p.EditorialReviewUnavailable,match='称呼'):
        p.review_complete_argument(cues,[dict(start=0,end=1)],speaker,'',tmp_path,'')
    assert address in cues[0]['text']


def test_final_reason_can_close_answer_and_old_prompt_cache_is_rechecked(monkeypatch,tmp_path):
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true')
    cues=[dict(start=0,end=60,text='我们暂时不买这家公司。'),
          dict(start=60,end=140,text='它的需求还不确定，所以我们继续观察。')]
    response=dict(analysis=dict(claim_range=[0,0],reasoning_range=[1,1],conclusion_range=[1,1],
        summary='等待需求明确',completeness_reason='最后一句完整解释了继续观察的理由',audio_issues=[]),
        verdict=dict(standalone_opening=True,complete_argument=True,reasoning_present=True,
                     natural_ending=True,requires_audio_review=False))
    calls=[]
    def review(*a,**k):
        calls.append(a)
        return json.dumps(response,ensure_ascii=False)
    monkeypatch.setattr(p,'llm',review)
    proof=p.review_complete_argument(cues,[dict(start=0,end=1)],'林园','',tmp_path,'')
    assert proof['conclusion_quote']==proof['reasoning_quote']==cues[-1]['text']
    proof['review_prompt_version']=8
    (tmp_path/'editorial_review.json').write_text(json.dumps(proof,ensure_ascii=False))
    checked=p.review_complete_argument(cues,[dict(start=0,end=1)],'林园','',tmp_path,'')
    assert len(calls)==2 and checked['review_prompt_version']==10


def test_real_question_plus_answer_cannot_be_claim_range_endpoint(monkeypatch,tmp_path):
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true')
    texts=['嗯。','即便是您持有的是重仓股，为什么这些上市公司会愿意接待您来调研？',
        '我也不知道为什么，搞不清楚。','可能我这个人还是一个好打交道的人。',
        '我想采访别人，可能人家拒绝你。']
    response=dict(analysis=dict(claim_range=[2,3],reasoning_range=[3,4],conclusion_range=[4,4],
        summary='嘉宾解释调研接待',completeness_reason='保留完整解释',audio_issues=[]),
        verdict=dict(standalone_opening=True,complete_argument=True,reasoning_present=True,
            natural_ending=True,requires_audio_review=False))
    calls=[]
    def call(messages,*args,**kwargs):
        fields=kwargs['response_schema']['properties']['analysis']['properties']
        assert 1 not in fields['claim_range']['items']['enum']
        assert 1 in fields['reasoning_range']['items']['enum']
        assert '明确提问句编号：[1]' in messages[0]['content']
        calls.append(messages)
        return json.dumps(response)
    monkeypatch.setattr(p,'llm',call)
    cues=[dict(start=i*30,end=(i+1)*30,text=t) for i,t in enumerate(texts)]
    proof=p.review_complete_argument(cues,[dict(start=0,end=4)],'林园','',tmp_path,'')
    assert len(calls)==1 and proof['claim_quote']==''.join(texts[2:4])
