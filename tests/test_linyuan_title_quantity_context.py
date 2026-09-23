import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_quantity_context as Q
import title_rewrite as T

SOURCE='那么到二零五七年，我们算了一下，中国的六十岁以上的老人会达到惊人的五，接近五亿，所以我们就做这五五亿人的生意。'


@pytest.mark.parametrize('copy',[
    '中国五亿老人是医药行业核心市场',
    '到2057年有五亿老人',
    '接近五亿六十岁以上老人',
    '到2057年五亿60岁以上老人',
])
def test_actual_79_approved_model_draft_loses_quantity_context(copy):
    assert Q.error(copy,copy,SOURCE)
    assert T.population_scope_error(copy,copy,SOURCE)


@pytest.mark.parametrize('copy',[
    '到2057年接近五亿60岁以上老人',
    '到二零五七年约五亿六十岁以上老人',
    '锁定医药赛道，别的不搞',
    '医药行业四亿人的市场',
])
def test_preserved_context_or_other_sourced_angle_is_not_blocked(copy):
    assert Q.error(copy,copy,SOURCE) is None


def test_unrelated_dates_do_not_supply_a_population_forecast():
    source='2057年是很遥远的未来。'+'这个话题已经说完了。'*20+'中国有五亿老人。'
    assert Q.error('中国五亿老人','中国五亿老人',source) is None
    assert Q.error('五亿企业营收','五亿企业营收','到2057年公司收入五亿') is None


def test_actual_split_asr_percent_retains_annual_unit():
    source='我们找到了一个年符合增长，未来二十年甚至三十年符合增长百分之三\n十的行业。'
    assert Q.error('医药未来三十年增长百分之三十','医药未来三十年增长百分之三十',source)
    assert Q.error('医药年复合增长30%','医药年复合增长30%',source) is None
    assert Q.error('医药三十年增长百分之三十','医药三十年增长百分之三十','医药三十年增长百分之三十。') is None


def test_actual_personal_choice_is_not_rejected_to_pad_title():
    units=['光伏能源我没有特意去研究，所以我们就没参与啊，没参与。']
    raw=dict(title='林园：没参与光伏能源投资',cover_title='没参与光伏能源投资',
        a_focus=dict(a_claim='林园没参与光伏能源投资',b_evidence_ids=[0]))
    candidate=T.bind_guest_candidate(raw,{},units,T.subject_catalog(units),[0])
    assert T._candidate_error(candidate,''.join(units),'林园',()) is None
    assert T.editorial_features(candidate)['concise']
    candidate['title']='林园：光伏能源'
    assert T._candidate_error(candidate,''.join(units),'林园',())


def test_old_cpu_approval_does_not_bypass_population_guard():
    title='林园：中国五亿老人是医药行业核心市场'
    item=dict(title=title,cover_title=title.split('：')[1],subject='中国',evidence=[SOURCE])
    proof=T._package(item,SOURCE,dict(method='cpu_text_review',appeal=5,
        reason='真实旧模型错误通过；这里检验新校验不信任旧判定。',**{k:True for k in T.CHECKS}),[])['title_rewrite']
    assert '人口数量' in T.error(title,proof,SOURCE)
