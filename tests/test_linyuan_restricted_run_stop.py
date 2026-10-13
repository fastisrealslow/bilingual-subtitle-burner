import copy
import importlib.util
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('stop_restricted',ROOT/'scripts/stop_restricted_linyuan_runs.py')
stop=importlib.util.module_from_spec(spec);spec.loader.exec_module(stop)


def fixture():
    request=stop.REQUESTS[0];run_id,slug,bvid=request
    run=dict(id=run_id,head_branch='main',head_repository={'full_name':stop.REPO},
        path='.github/workflows/linyuan-produce-cn.yml',display_title='中文源出片 · '+slug,status='in_progress')
    url='https://www.bilibili.com/video/'+bvid
    state={'dispatched':[dict(slug=slug,ts=1,source_url=url)]}
    policies=[dict(source_url=url,no_reprint=1,reason='Known explicit ban')]
    return request,run,state,policies


def test_exact_restricted_run_can_stop_without_mutating_samples():
    request,run,state,policies=fixture();before=copy.deepcopy(state)
    assert stop.eligible(run,state,request,policies) and state==before


@pytest.mark.parametrize('field,value',[
    ('status','completed'),('id',1),('head_branch','feature'),
    ('head_repository',{'full_name':'other/repo'}),
    ('path','.github/workflows/linyuan-topic-preview.yml'),
    ('display_title','中文源出片 · other'),
])
def test_terminal_foreign_and_preview_runs_are_never_stopped(field,value):
    request,run,state,policies=fixture();run[field]=value
    assert not stop.eligible(run,state,request,policies)


def test_other_source_or_unknown_flag_never_authorizes_cancellation():
    request,run,state,policies=fixture();policies[0]['no_reprint']=0
    assert not stop.eligible(run,state,request,policies)
    policies[0]['no_reprint']=1;state['dispatched'][0]['source_url']='https://www.bilibili.com/video/BV18Zj9zcE4V'
    assert not stop.eligible(run,state,request,policies)


def test_large_state_uses_raw_json_media_type_not_empty_inline_metadata(monkeypatch):
    import json
    commands=[];state={'dispatched':[],'published':{},'long_error':'x'*1_100_000}
    def execute(command,**kwargs):
        commands.append(command)
        assert 'Accept: application/vnd.github.raw+json' in command
        return json.dumps(state)
    monkeypatch.setattr(stop.subprocess,'check_output',execute)
    assert stop.api('GET','contents/site/fc_state.json?ref=main',raw=True)==state
    assert len(commands)==1
    with pytest.raises(ValueError,match='read-only'):
        stop.api('POST','anything',raw=True)
