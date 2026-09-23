"""Do not lose a confirmed object's provenance at the reader/writer handoff."""
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T


def test_object_only_in_host_question_is_not_guest_evidence():
    units=['中石油粘性很强，您买了吗？','没买，因为它不符合我的标准。','它中石油还有可能被替代。']
    roles=['host','guest','guest']
    reading=dict(e_subject_name='中石油')
    bound=T.bind_answer_subject(reading,units,roles)
    assert bound['exact_source']==[units[2]]
    assert bound['evidence_ids']==[2]
    # Model-authored IDs from older experiments cannot alter program evidence.
    assert T.bind_answer_subject({**reading,'f_subject_evidence_ids':[0,1,99]},units,roles)==bound
    with pytest.raises(ValueError):
        T.bind_answer_subject({**reading,'e_subject_name':'粘性很强'},units,roles)
    with pytest.raises(ValueError):
        T.bind_answer_subject(reading,units,['host','guest','unknown'])


def test_reader_object_must_survive_both_copy_fields_and_evidence():
    subject=dict(name='中石油',evidence_ids=[2])
    item=dict(title='林园：我没买中石油',cover_title='没买中石油',evidence_ids=[1,2])
    assert T.require_answer_subject(item,subject)==item
    for changes in [dict(title='林园：我没买'),dict(cover_title='不符合标准'),dict(evidence_ids=[1])]:
        with pytest.raises(ValueError):T.require_answer_subject({**item,**changes},subject)


def test_answer_subject_is_optional_and_does_not_change_existing_reader_schema():
    assert 'e_subject_name' not in T.reading_schema(3,True)['properties']
    assert 'e_subject_name' in T.reading_schema(3,True,answer_subject=True)['properties']
    assert 'f_subject_evidence_ids' not in T.reading_schema(3,True,answer_subject=True)['properties']


def test_complete_handoff_keeps_subject_and_final_independent_review():
    units=['中石油上市时您买了吗？','没买，因为它不符合我的标准。','它中石油可能有替代产品。']
    title='林园：我没买中石油';stages=[]
    def model(prompt,schema):
        props=schema['properties']
        if 'c_sentence_roles' in props:
            stages.append('read')
            return json.dumps(dict(a_guest_answer='嘉宾说自己没有买中石油，认为它不符合自己的标准。',
                b_question_premise='主持人问中石油上市时买了没有。',
                c_sentence_roles=dict(u0000='host',u0001='guest',u0002='guest'),
                d_main_answer_quote=units[1],e_subject_name='中石油'))
        if 'c_candidates' in props:
            stages.append('write')
            assert '阅读阶段确认的本回答对象及嘉宾原句' in prompt
            assert units[0] not in prompt
            item=dict(title=title,cover_title='没买中石油',a_focus=dict(a_claim='林园没有买中石油。',b_evidence_ids=[1,2]))
            missing={**item,'cover_title':'没买，因为不符标准'}
            return json.dumps(dict(c_candidates=[item,missing,missing]))
        stages.append('review')
        assert units[0] in prompt  # critic still reads the whole dialogue
        return json.dumps(dict(reviews=[dict(a_analysis=dict(a_guest_answer='嘉宾没有买入中石油，认为不符合自己的标准。',
            b_question_premise='主持人问中石油上市时是否买入。',c_reason='原文明确回答没买，同一回答后文指明中石油。'),
            b_verdict=dict(index=0,appeal=4,**{k:True for k in T.CHECKS}))]))
    result=T.generate(''.join(units),source_cues=units,structured_model=model,
                      answer_focus=True,answer_subject=True)
    assert stages==['read','write','review']
    assert result['answer_focus_reading']['subject']['name']=='中石油'
    assert T.error(title,result['title_rewrite'],''.join(units)) is None


@pytest.mark.parametrize('name',['这个行业','这个赛道','这些公司','上述企业','当前市场'])
def test_literal_presence_does_not_make_a_vague_subject_concrete(name):
    # Real 8B source312 supplied 这个行业 after the first reader retry. The
    # literal lookup found it, but it still does not tell a viewer what it is.
    with pytest.raises(ValueError,match='具体名称'):
        T.bind_answer_subject(dict(e_subject_name=name),[name+'我也不知道投什么。'],['guest'])
    assert T.bind_answer_subject(dict(e_subject_name='科技赛道'),
        ['这不是针对AI，所有科技赛道。'],['guest'])['name']=='科技赛道'
