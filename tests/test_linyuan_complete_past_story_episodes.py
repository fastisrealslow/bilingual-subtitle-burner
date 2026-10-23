"""Real complete source narrative and explicit hypothetical-context controls."""
from copy import deepcopy
import json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import source_selection as S
import story_episodes as Q
import publishing_experiment as E


def actual():
    cues=json.loads((ROOT/'tests/fixtures/linyuan_actual_company_episode.json').read_text())['cues']
    parents=[dict(start=0,end=len(cues)-1,selection_method='source_question_answer_v2')]
    return cues,parents


def test_real_full_answer_yields_one_intact_event_without_changing_original_cues_or_parent():
    cues,parents=actual();before=deepcopy((cues,parents))
    options=Q.propose(cues,parents,S.sentence_units(cues))
    assert len(options)==1
    p=options[0];text=''.join(c['text'] for c in cues[p['start']:p['end']+1])
    assert cues[p['start']]['start']==1318.36 and cues[p['end']]['end']==1343.88
    assert text.startswith('有一次新闻联播播出来') and text.endswith('两个公司都不在了。')
    assert '因为我那时候持有长虹。' in text and 'TCTC' not in text
    assert 'TCTC' in p['past_episode_context']['parent_text']  # Full parent never hidden.
    assert Q.context_error(p['past_episode_context'],text) is None
    assert S.boundary_error(cues,p) is None and (cues,parents)==before


@pytest.mark.parametrize('prefix',['我们先做一个假设。','如果是这样。','打个比方。','这是别人说的。',
                                   '但我并没有持有过长虹。'])
def test_prior_hypothesis_transfer_or_denial_cannot_be_cropped_into_a_real_event(prefix):
    cues,parents=actual();cues[0]['text']=prefix+cues[0]['text']
    assert not Q.propose(cues,parents,S.sentence_units(cues))


@pytest.mark.parametrize('change',['parent_hash','parent_hypothesis','offset','clock','raw','missing','boolean'])
def test_publisher_replays_complete_context_and_raw_source_clock(change):
    cues,parents=actual();p=Q.propose(cues,parents,S.sentence_units(cues))[0]
    context=deepcopy(p['past_episode_context']);text=context['parent_text'][context['child_offset']:]
    meta=dict(segments=[dict(start=context['child_start_sec'],end=context['parent_end_sec'])])
    if change=='parent_hash':context['parent_sha256']='f'*64
    elif change=='parent_hypothesis':
        context['parent_text']='假设。'+context['parent_text'];context['child_offset']+=3
        context['parent_sha256']=Q.digest(context['parent_text'])
    elif change=='offset':context['child_offset']+=1
    elif change=='clock':meta['segments'][0]['start']+=2
    elif change=='raw':text=text.replace('持有','没有持有')
    elif change=='missing':context.pop('parent_text')
    else:context['version']=True
    assert Q.context_error(context,text) or Q.binding_error(context,meta,E.digest(text))


def test_event_cannot_be_future_conditional_or_denied():
    assert Q.actual_event('因为我那时候持有长虹。')
    for s in ['我那时候如果持有长虹。','我当时可能买过长虹。','我当年没有买过长虹。','我明年会持有长虹。']:
        assert not Q.actual_event(s)


def test_nested_story_and_parent_cannot_both_become_same_batch_stock():
    accepted=[dict(segments=[dict(start=1318.36,end=1343.88)],
        experiment_proof=dict(selection=dict(past_episode_context={'synthetic':True})))]
    assert Q.overlaps_episode([dict(start=1218.6,end=1343.88)],{},accepted)
    assert not Q.overlaps_episode([dict(start=1344,end=1400)],{},accepted)
    assert not Q.overlaps_episode([dict(start=100,end=130)],{},[
        dict(segments=[dict(start=110,end=140)])])  # Other old batch behavior unchanged.
