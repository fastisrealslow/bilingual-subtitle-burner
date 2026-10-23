"""A real short source lost its intact initial question without 您/你."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import source_selection as S


def actual():
    return json.loads((Path(__file__).parent/'fixtures/linyuan_actual_initial_market_question.json').read_text())['cues']


def test_actual_initial_topic_question_keeps_the_whole_original_answer(monkeypatch):
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(S.editorial,'MIN_SECONDS',20)
    cues=actual();before=deepcopy(cues);diagnostics={}
    picks=S.select(cues,whole_source=True,limit=None,diagnostics=diagnostics)
    assert diagnostics['question_count']==1 and len(picks)==1
    assert picks[0]['start']==0 and picks[0]['end']==len(cues)-1 and cues==before
    assert not any(k in picks[0] for k in ('passed','title','story_selection','media_approved'))


@pytest.mark.parametrize('change',['interior','partial','long','no_object','no_question'])
def test_topic_prompt_cannot_invent_an_interview_turn_in_other_contexts(monkeypatch,change):
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    cues=actual();whole=True
    if change=='interior':
        cues=[dict(start=0,end=1,text='我们先谈一个原则。')]+[{**c,'start':c['start']+1,'end':c['end']+1} for c in cues]
    elif change=='partial':whole=False
    elif change=='long':cues[-1]['end']=900
    elif change=='no_object':cues[0]['text']='当前的事情怎么看？'
    else:cues[0]['text']='当下时点的券商走势很好。'
    diagnostics={};S.select(cues,whole_source=whole,limit=None,diagnostics=diagnostics)
    assert diagnostics['question_count']==0
