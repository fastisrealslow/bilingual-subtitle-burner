from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import documented_company_story as D
import publishing_experiment as E


def source():
    return json.loads((Path(__file__).with_name('fixtures')/'linyuan_documented_company_source_20261030.json').read_text())


def verdict():
    return dict(a_evidence=dict(subject_sentence=8,event_sentence=9,detail_sentence=15),z_kind='company_case')


def test_actual_source_is_rechecked_only_after_its_unchanged_primary_rejections(tmp_path):
    f=source();cues=f['source_cues'];old=iter(f['original_responses']);calls=[]
    def call(prompt,schema):
        calls.append(schema)
        if 'a_evidence' in schema['properties']:return verdict()
        return next(old)
    report=E.choose_stories(cues,f['candidates'],call,tmp_path/'report.json')
    assert len(calls)==5 and next(old,None) is None
    assert len(report['selected'])==1 and len(report['rejected'])==7 and report['unresolved']==[]
    s=report['selected'][0]['story_selection']
    assert s['kind']=='company_case' and s['company_documentary_recall_version']==1
    assert s['event_quote']=='我说的这个，我写的这个文章是：招商银行只有两块多啊，到十五块，你们去对应指数，那就是牛市开始。'
    assert s['detail_quote']=='但这本书呢，确实记录的二零零四年到二零零六年那个结果啊。'
    assert D.selection_error(s) is None
    assert report['calls'][-1]['stage']=='documented_company_recall_v1'


@pytest.mark.parametrize('text',[
    '招商银行到十五块就是牛市。我以后要写文章记录这个判断。这本书记录了二零零六年的结果。',
    '招商银行到十五块就是牛市。我没有写过文章。这本书记录了二零零六年的结果。',
    '招商银行到十五块就是牛市。我当时说过这个判断。以后书里会记录二零二七年的结果。',
    '我当时写过文章。那本书记录了二零零六年的结果。',
    '招商银行到十五块就是牛市。我今天盈利实力最好。那本书记录了二零零六年的结果。',
])
def test_forecasts_plans_denial_or_unidentified_companies_do_not_get_a_review(text):
    cues=[dict(start=0,end=30,text=text)]
    assert D.request(cues,dict(start=0,end=0)) is None


def test_new_review_can_skip_and_does_not_manufacture_a_story():
    f=source();responses=iter(f['original_responses'])
    def call(prompt,schema):
        return {**verdict(),'z_kind':'skip'} if 'a_evidence' in schema['properties'] else next(responses)
    r=E.choose_stories(f['source_cues'],f['candidates'],call,None)
    assert r['selected']==[] and len(r['rejected'])==8 and not r['retryable']


def test_timeout_remains_unresolved_instead_of_a_clean_editorial_rejection():
    f=source();responses=iter(f['original_responses'])
    def call(prompt,schema):
        if 'a_evidence' in schema['properties']:raise RuntimeError('service unavailable')
        return next(responses)
    r=E.choose_stories(f['source_cues'],f['candidates'],call,None)
    assert r['retryable'] and len(r['unresolved'])==1 and len(r['rejected'])==7


@pytest.mark.parametrize('change',[dict(event_sentence=5000),dict(event_sentence=True),dict(detail_sentence=9)])
def test_model_cannot_invent_or_repeat_evidence(change):
    f=source();responses=iter(f['original_responses'])
    v=verdict();v['a_evidence'].update(change)
    def call(prompt,schema):return v if 'a_evidence' in schema['properties'] else next(responses)
    r=E.choose_stories(f['source_cues'],f['candidates'],call,None)
    assert r['selected']==[] and r['retryable'] and len(r['unresolved'])==1


def test_saved_documentary_marker_does_not_authorize_a_future_forecast():
    s=dict(kind='company_case',company_documentary_recall_version=1,
        event_quote='我以后会写文章讲招商银行。',detail_quote='书记录了二零零六年的结果。')
    assert D.selection_error(s)
    s['event_quote']='我写过文章讲招商银行。';s['company_documentary_recall_version']=999
    assert D.selection_error(s)


def test_review_uses_complete_original_answer_and_evidence_before_verdict():
    f=source();pick=f['candidates'][1];cues=deepcopy(f['source_cues']);before=deepcopy(cues)
    prompt,schema=D.request(cues,pick)
    units=E._selection_rows(cues,[pick])[0]['sentences']
    assert json.loads(prompt.split('\n')[-1])==units and cues==before
    assert sorted(schema['properties'])==['a_evidence','z_kind']
    assert schema['properties']['z_kind']['enum']==['skip','company_case']
