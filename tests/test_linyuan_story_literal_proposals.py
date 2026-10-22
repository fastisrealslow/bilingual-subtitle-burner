"""Same-story source copy is a proposal, not a weaker approval pathway."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T
import publishing_experiment as E


def case():
    units=['您会因为市场情绪卖出吗？','我这辈子基本上卖出在我印象当中很少。',
           '从二零零零年以后，我基本上没有卖特别卖出的这个行为。',
           '就刚才我举的那个例子，比如说我们的白','酒，我可能是终身持有，不会卖。']
    selection=dict(kind='personal_story',subject_quote=units[1],event_quote=units[2],detail_quote=units[3]+units[4])
    return units,E.title_focus(selection)


def test_literal_proposal_keeps_original_words_scope_and_qualifier_across_the_exact_guest_seam():
    units,focus=case();before=list(units)
    catalog=T.subject_catalog(units,allowed_ids=[1,2,3,4])
    rows=T.bound_story_literal_candidates(focus,units,catalog,[1,2,3,4],'林园',set())
    assert len(rows)==1
    row=rows[0];body=row['title'].split('：',1)[-1]
    assert body=='我们的白酒，我可能是终身持有，不会卖'
    assert body in focus['source'][2] and row['cover_title']==body
    assert row['subject']=='白酒' and row['evidence_ids']==[3,4]
    assert row['evidence']==[units[3]+units[4]] and units==before
    assert row['proposal_method']=='same_story_intact_quote_v1'


def test_host_or_unknown_half_and_used_candidates_cannot_be_recovered_by_filling_evidence():
    units,focus=case();catalog=T.subject_catalog(units)
    assert T.bound_story_literal_candidates(focus,units,catalog,[1,2,3],'林园',set())==[]
    rows=T.bound_story_literal_candidates(focus,units,catalog,[1,2,3,4],'林园',set())
    assert not T.bound_story_literal_candidates(focus,units,catalog,[1,2,3,4],'林园',{rows[0]['title']})


def test_quote_does_not_remove_conditions_or_borrow_another_story():
    _,focus=case()
    conditional='如果公司的经营和未来的竞争格局以及我们自己的判断都没有改变，我们的白酒，我可能是终身持有，不会卖。'
    focus={**focus,'source':[conditional]}
    assert not T.story_literal_candidates(focus,conditional,[conditional])
    units,focus=case();source=''.join(units)+'我买茅台是因为消费升级。'
    assert all('茅台' not in t for t in T.story_literal_candidates(focus,source,[source]))


def test_literal_proposal_never_exceeds_the_existing_four_source_cue_limit():
    units,focus=case();text=focus['source'][2];chars=list(text)
    catalog=T.subject_catalog(chars,allowed_ids=list(range(len(chars))))
    assert not T.bound_story_literal_candidates(focus,chars,catalog,list(range(len(chars))),'林园',set())


def model_call(calls,reject=False):
    def call(prompt,schema):
        calls.append(schema)
        fields=schema.get('properties',{})
        if 'c_guest_spans' in fields:
            return json.dumps(dict(a_guest_answer='我基本很少卖出，白酒可能终身持有，不是一定永远不卖。',
                b_question_premise='主持人询问什么时候卖出',c_guest_spans=[dict(a_start=1,b_end=4)]),ensure_ascii=False)
        if 'reviews' in fields:
            # Require the SAME complete independent-review contract, not
            # quote-only readability or a constant passed flag.
            assert set(fields['reviews']['items']['required'])=={'a_analysis','b_verdict'}
            return json.dumps(dict(reviews=[dict(a_analysis=dict(
                a_guest_answer='白酒可能终身持有，基本很少卖出。',b_question_premise='询问卖出标准',
                c_reason='候选保留自己的白酒持仓范围、可能以及不会卖的原话，表达自然完整。'),
                b_verdict=dict(index=0,appeal=4,**{k:(not reject or k!='source_supported') for k in T.CHECKS}))]),ensure_ascii=False)
        return '{}'  # Rejected literal copy cannot fall into an unchecked path.
    return call


def test_literal_title_keeps_the_full_independent_fact_attribution_appeal_and_qualifier_gate():
    units,focus=case();calls=[]
    result=T.generate(''.join(units),source_cues=units,editorial_focus=focus,structured_model=model_call(calls))
    assert len(calls)==2 and result['editorial_selection']['proposal_method']=='same_story_intact_quote_v1'
    proof=result['title_rewrite']
    assert proof['review']['method']=='cpu_text_review'
    assert all(proof['review'][k] is True for k in T.CHECKS) and proof['review']['appeal']==4
    assert T.error(result['title'],proof,''.join(units)) is None


def test_literal_source_match_does_not_override_an_independent_negative_verdict():
    units,focus=case();calls=[]
    with pytest.raises(ValueError):
        T.generate(''.join(units),source_cues=units,editorial_focus=focus,structured_model=model_call(calls,reject=True))
    assert any('reviews' in x.get('properties',{}) for x in calls)
