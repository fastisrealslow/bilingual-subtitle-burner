"""Preview must exercise production gates without publication or manual answers."""
import importlib.util
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
