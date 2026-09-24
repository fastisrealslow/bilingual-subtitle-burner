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
    response=dict(analysis=dict(claim_quote=text,reasoning_quote=text,conclusion_quote=text,
        opening_quote=text,ending_quote=text,summary='现金流与支付能力',
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
    analysis=dict(claim_quote=question,reasoning_quote=answer,conclusion_quote=answer,
        opening_quote=question,ending_quote=answer,summary='医药需求',
        completeness_reason='保留观点与理由',audio_issues=[])
    verdict=dict(standalone_opening=True,complete_argument=True,reasoning_present=True,
        natural_ending=True,requires_audio_review=False)
    monkeypatch.setattr(p,'llm',lambda *a,**k:json.dumps(dict(analysis=analysis,verdict=verdict)))
    with pytest.raises(p.EditorialReviewUnavailable,match='采访者提问'):
        p.review_complete_argument(cues,[dict(start=0,end=1)],'林园','',tmp_path,'')
    assert not (tmp_path/'editorial_review.json').exists()
    analysis['claim_quote']=answer
    assert p.review_complete_argument(cues,[dict(start=0,end=1)],'林园','',tmp_path,'')['complete_argument'] is True
