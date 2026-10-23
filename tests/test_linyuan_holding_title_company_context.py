"""Actual titles had a valid guest action but no repeated company name."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import publishing_experiment as E
import title_rewrite as T


def actual():
    return json.loads((Path(__file__).parent/'fixtures/linyuan_actual_holding_company_context.json').read_text())


def bound_actual():
    f=actual();units=f['units'];focus=E.title_focus(f['selection'])
    guests=[i for span in f['actual_reader']['c_guest_spans'] for i in range(span['a_start'],span['b_end']+1)]
    subjects=T.subject_catalog(units,allowed_ids=guests)
    draft=f['actual_draft'];item=T.bind_guest_candidate(draft['c_candidates'][0],draft['b_focus'],units,subjects,guests)
    assert item['subject']==''
    return f,focus,T.bind_story_company_context(item,focus,units)


def test_real_rejected_draft_gets_only_its_question_object_not_host_facts():
    f,focus,item=bound_actual();before=deepcopy(f)
    assert item['subject']=='茅台'
    assert all('茅台' not in q for q in item['evidence'])
    assert item['subject_context']['question_quote']=='那未来会继续看好茅台？'
    assert T._candidate_error(item,''.join(f['units']),'林园',(),check_layout=False) is None
    assert E.title_focus_error(item['title'],focus,cover=item['cover_title']) is None
    assert f==before
    # The actual reader wrongly included the host's future inference in its
    # broad guest span. Even that real output must not authorize it as evidence.
    bad=T.bind_guest_candidate(dict(title='林园：茅台持有近二十年，至少未来几年不动',cover_title='茅台至少未来几年不动'),
        dict(a_claim='我们已经实际持有了近二十年',b_evidence_ids=[1,2,3,5]),f['units'],T.subject_catalog(f['units']),list(range(1,10)))
    with pytest.raises(ValueError,match='主持人推断'):
        T.bind_story_company_context(bad,focus,f['units'])
    assert E.title_focus_error(bad['title'],focus,cover=bad['cover_title'])


@pytest.mark.parametrize('change',['hypothetical','denied','future','company','different_source','missing_choice','question_as_fact'])
def test_question_context_never_authorizes_unconfirmed_actions(change):
    f,_,item=bound_actual();ctx=deepcopy(item['subject_context']);raw=''.join(f['units']);evidence=item['evidence'][:]
    if change=='hypothetical':raw='我们先做一个假设。'+raw
    elif change=='denied':raw+='这并不是我们的实际交易。'
    elif change=='future':ctx['event_quote']='我们未来可能会买入茅台。';raw+=ctx['event_quote']
    elif change=='company':ctx['subject']='腾讯'
    elif change=='different_source':raw='我们当年买了腾讯。我们仍然持有。'
    elif change=='missing_choice':evidence=evidence[:-1]
    else:evidence.append(ctx['question_quote'])
    assert T.company_subject_context_error(ctx,raw,'茅台',evidence)


def test_context_is_bound_in_copy_proof_and_replayed_against_original_dialogue():
    f,_,item=bound_actual();raw=''.join(f['units'])
    review=dict(method='cpu_text_review',appeal=4,reason='标题保留实际过去持仓时长与当前不动的选择，公司名仅由紧接回答的问题交代。',**{k:True for k in T.CHECKS})
    packaged=T._package(item,raw,review,[item]);proof=packaged['title_rewrite']
    assert T.error(item['title'],proof,raw) is None
    changed=deepcopy(proof);changed['subject_context']['subject']='腾讯'
    assert T.error(item['title'],changed,raw)
    changed=deepcopy(proof);changed.pop('subject_context')
    assert T.error(item['title'],changed,raw)
    assert T.error(item['title'],proof,None)
    assert T._binding('t','c','s',['e'])==T._binding('t','c','s',['e'],None)


def test_host_premise_stays_blocked_even_when_title_keeps_the_past_anchor():
    f=actual();focus=E.title_focus(f['selection'])
    for text in ['林园：茅台持有近二十年，至少未来五年不动',
                 '林园：茅台持有近二十年，未来几年继续持有',
                 '林园：茅台持有近二十年，除非行业消失才卖出']:
        assert E.title_focus_error(text,focus)


def test_company_context_does_not_relabel_the_question_as_a_guest_statement():
    f=actual();units=f['units'];guest_ids=[1,2,3,4]
    subjects=T.subject_catalog(units,allowed_ids=guest_ids)
    assert '茅台' not in subjects and 0 not in guest_ids and 5 not in guest_ids
    item=T.bind_guest_candidate(f['actual_draft']['c_candidates'][0],f['actual_draft']['b_focus'],units,subjects,guest_ids)
    before=deepcopy((units,guest_ids))
    item=T.bind_story_company_context(item,E.title_focus(f['selection']),units)
    assert item['subject']=='茅台' and item['evidence_ids']==[1,2,3,4]
    assert (units,guest_ids)==before


def test_factual_drafting_intersects_reader_with_actual_case_without_dropping_dialogue():
    f=actual();units=f['units'];reader_guest_ids=list(range(1,10));before=deepcopy((units,reader_guest_ids))
    ids=T.holding_title_guest_ids(E.title_focus(f['selection']),units,reader_guest_ids)
    assert ids==[1,2,3,4] and (units,reader_guest_ids)==before
    assert T.holding_title_guest_ids(E.title_focus(f['selection']),units,[1,2,3])==[1,2,3]
    assert T.holding_title_guest_ids(None,units,reader_guest_ids)==reader_guest_ids
