from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import editorial_evidence as e
from title_rewrite import relation_error


def analysis():
    return dict(claim_range=[1,1],reasoning_range=[2,3],conclusion_range=[3,3],
        summary='嘉宾说明选择及理由',completeness_reason='原问答保留观点、理由和结论',audio_issues=[])


def test_evidence_is_bound_to_original_punctuation_and_contiguous_sentences():
    units=e.sentences('您怎么看？我们暂时不买。需求还不确定，不能猜。等实际数据。')
    data=analysis();data['claim_quote']='凭空编写不能覆盖原文'
    bound=e.bind(data,units)
    assert bound['claim_quote']=='我们暂时不买。'
    assert bound['reasoning_quote']=='需求还不确定，不能猜。等实际数据。'
    assert bound['opening_quote']=='您怎么看？' and bound['ending_quote']=='等实际数据。'


def test_claim_endpoints_exclude_host_without_restricting_context_evidence():
    fields=e.schema(['提问？','判断。','追问？','回答。'],claim_excluded=[0,2])
    assert fields['claim_range']['items']['enum']==[-1,1,3]
    assert fields['reasoning_range']['items']['enum']==[-1,0,1,2,3]
    assert fields['conclusion_range']['items']['enum']==[-1,0,1,2,3]


@pytest.mark.parametrize('span',[[1,9],[2,1],[-1,1],[True,1],[1],['1',1]])
def test_invalid_ranges_never_become_source_evidence(span):
    data=analysis();data['claim_range']=span
    with pytest.raises(ValueError):e.bind(data,['提问？','判断。','理由。','结论。'])


def test_missing_claim_and_audio_issue_remain_explicit():
    data=analysis();data['claim_range']=[-1,-1];data['audio_issues']=[dict(sentence_id=2,reason='关键实体疑似识别错误')]
    bound=e.bind(data,['提问？','判断。','疑词。','结论。'])
    assert bound['claim_quote']=='' and bound['audio_issues'][0]['quote']=='疑词。'


@pytest.mark.parametrize('subject,object_',[
    ('股市回落','投资决策'),('价格变化','采购计划'),('天气变化','工作安排')])
def test_unchanged_decision_is_not_unaffected_activity(subject,object_):
    source=f'{subject}并不影响我们的{object_}。'
    assert relation_error(f'{subject}不影响{object_[:-2]}',f'{subject}不影响{object_}',source)
    assert relation_error(f'{subject}不影响{object_}',f'{subject}不影响{object_[:-2]}',source)
    assert relation_error(f'{subject}不影响我们的{object_}',f'{subject}不影响{object_}',source) is None


def test_unaffected_results_are_allowed_when_source_actually_says_them():
    assert relation_error('下雨不影响产量','下雨不影响产量','下雨不影响产量。') is None
