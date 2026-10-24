"""Actual completed activity missed by the broad story classifier."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import keynote_proposals as K
import publishing_experiment as E


@pytest.fixture(autouse=True)
def production_policy(monkeypatch):
    monkeypatch.setattr(K.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(K.editorial,'MIN_SECONDS',20)


def actual():
    cues=json.loads((Path(__file__).parent/'fixtures/linyuan_actual_keynote_activity_17.json').read_text())['cues']
    return cues,K.completed_activity_proposals(cues)[0]


def actual_activity_selection():
    cues,pick=actual();_,schema=E.completed_activity_request(cues,pick)
    verdict={k:v['enum'][0] for k,v in schema['properties'].items() if k!='kind'}
    verdict['kind']='personal_story'
    selection=E.validate_choices(cues,[pick],{'candidate_0':verdict})[0]['story_selection']
    selection['completed_activity_recall_version']=1
    return selection


def test_actual_motive_objects_are_not_lost_to_pos_labels_or_abstract_purpose():
    selection=actual_activity_selection();focus=E.title_focus(selection)
    assert {'想法','看法'}<=set(focus['details']) and '目的' not in focus['details']
    assert E.title_focus_error('林园：组织了多次活动，我想把看法说出来',focus) is None
    assert E.title_focus_error('林园：组织活动，为了表达想法',focus) is None
    assert E.title_focus_error('林园：组织活动很重要',focus)
    assert not E.title_focus(selection|{'completed_activity_recall_version':0}).get('activity_motive_source')


@pytest.mark.parametrize('title,cover',[
    ('林园：见五千人只为验证想法对不对','见五千人表达想法'),
    ('林园：组织活动，想把看法说出来','唯一目的是表达看法'),
    ('林园：活动有目的，就是看市场认不认','活动有目的就是看市场'),
    ('林园：组织活动，想把看法说出来','市场认可我的看法'),
])
def test_actual_failed_drafts_cannot_add_sole_motive_or_market_approval(title,cover):
    assert E.title_focus_error(title,E.title_focus(actual_activity_selection()),cover=cover)


@pytest.mark.parametrize('stage',['a_guest_answer','c_candidates','reviews'])
def test_actual_activity_guidance_keeps_actions_counts_and_uncertainty(stage):
    evidence=actual_activity_selection()
    prompt=E.story_title_prompt('原有事实核对',{'properties':{stage:{}}},evidence)
    assert evidence['event_quote'] in prompt and evidence['detail_quote'] in prompt
    assert '见过多少人不等于组织了一场同人数的活动' in prompt
    assert '不等于市场或别人已经认可' in prompt
    assert '不是已批准的事实' in prompt


@pytest.mark.parametrize('accept',[False,True])
def test_real_primary_skip_gets_one_strict_model_review_without_forcing_acceptance(tmp_path,accept):
    cues,pick=actual();before=deepcopy(cues);calls=[]
    def call(prompt,schema):
        calls.append((prompt,schema))
        if 'candidate_0' in schema['properties']:
            props=schema['properties']['candidate_0']['properties']
            return {'candidate_0':dict(kind='skip',**{k:v['enum'][0] for k,v in props.items() if k!='kind'})}
        props=schema['properties']
        return dict(kind='personal_story' if accept else 'skip',
            **{k:v['enum'][0] for k,v in props.items() if k!='kind'})
    report=E.choose_stories(cues,[pick],call,tmp_path/'actual.json')
    assert report['selection_completed'] and len(calls)==2 and cues==before
    assert report['calls'][1]['stage']=='completed_activity_recall_v1'
    assert bool(report['selected'])==accept
    if accept:
        proof=report['selected'][0]['story_selection']
        assert proof['completed_activity_recall_version']==1
        assert '组织了很多次活动' in proof['event_quote']
        assert '说出来' in proof['detail_quote'] and '能不能印证' in proof['detail_quote']
        assert proof['event_quote']!=proof['detail_quote']
        assert not any(k in report['selected'][0] for k in ('passed','title','media_approved'))


@pytest.mark.parametrize('replacement',['计划组织','没有组织过','将来组织','假设组织了'])
def test_actual_action_cannot_be_replaced_with_a_plan_denial_or_hypothesis(replacement):
    cues,pick=actual();cues=deepcopy(cues)
    for cue in cues:cue['text']=cue['text'].replace('织了很多次活动',replacement+'很多次活动')
    assert E.completed_activity_request(cues,pick) is None


def test_generic_proposal_and_only_future_result_cannot_get_activity_recall():
    cues,pick=actual()
    assert E.completed_activity_request(cues,{**pick,'selection_method':'model_proposed_source_sentences_v1'}) is None
    cues=deepcopy(cues)
    for cue in cues:cue['text']=cue['text'].replace('我就想','大家都说')
    assert E.completed_activity_request(cues,pick) is None
    assert E.completed_activity_detail_error('过三年我们就能见分晓。')


def test_same_sentence_or_invented_detail_is_not_a_recall_approval(tmp_path):
    cues,pick=actual();prompt,schema=E.completed_activity_request(cues,pick)
    props=schema['properties'];event=props['event_sentence']['enum'][0]
    assert event not in props['detail_sentence']['enum']
    assert E.completed_activity_event_error('如果我组织了活动，我会说出来。')
    assert E.completed_activity_detail_error('我明年就想把我的想法说出来。')


def test_model_cannot_substitute_a_question_for_the_schema_restricted_activity_subject(tmp_path):
    cues,pick=actual()
    question=next(s['id'] for s in E._selection_rows(cues,[pick])[0]['sentences'] if s['text']=='为什么？')
    def call(prompt,schema):
        if 'candidate_0' in schema['properties']:
            props=schema['properties']['candidate_0']['properties']
            return {'candidate_0':dict(kind='skip',**{k:v['enum'][0] for k,v in props.items() if k!='kind'})}
        props=schema['properties']
        return dict(kind='personal_story',subject_sentence=question,
                    event_sentence=props['event_sentence']['enum'][0],detail_sentence=props['detail_sentence']['enum'][0])
    report=E.choose_stories(cues,[pick],call,tmp_path/'bad-subject.json')
    assert not report['selected'] and not report['selection_completed']
    assert 'completed first-person activity' in report['unresolved'][0]['error']
