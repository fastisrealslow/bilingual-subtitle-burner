"""A real source100 pass lost its income condition on the approved cover."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_rewrite as T


TITLE = '林园：投资最坏结果十二三年回本，前提是人均收入不减少'
SOURCE = '十二三年你今天的投资能回本，这是基于人均收入比今天不减少。'


@pytest.mark.parametrize('cover', [
    '投资回本需十二三年', '只要坚持十二三年就能回本',
    '前提是坚持投资十二三年',
])
def test_dropped_or_replaced_condition_rejected_after_positive_model_review(cover):
    item = dict(title=TITLE, cover_title=cover, subject='人均收入', evidence=[SOURCE])
    verdict = dict(method='cpu_text_review', appeal=5,
                   reason='模拟本轮模型将条件丢失的封面误判为通过',
                   **{k: True for k in T.CHECKS})
    package = T._package(item, SOURCE, verdict, [item])
    assert '前提' in T._candidate_error(item, SOURCE, '林园', (), check_layout=False)
    assert '前提' in T.error(TITLE, package['title_rewrite'], SOURCE)


@pytest.mark.parametrize('cover', [
    '人均收入不减少，十二三年回本',
    '十二三年回本有什么前提？',
])
def test_complete_condition_or_honest_question_remains_reviewable(cover):
    item = dict(title=TITLE, cover_title=cover, subject='人均收入', evidence=[SOURCE])
    assert T._candidate_error(item, SOURCE, '林园', (), check_layout=False) is None


def test_trailing_condition_and_unrelated_question():
    assert T.cover_qualifier_error('林园：十二三年回本，但收入不降是前提', '投资多久才能回本？')
    assert not T.cover_qualifier_error('林园：十二三年回本，但收入不降是前提', '收入不降，十二三年回本')
    assert not T.cover_qualifier_error('林园：租客半年没交房租，我先让他腾房', '半年没交房租，我先让他腾房')


def test_exact_quote_path_cannot_bypass_cover_condition_check():
    quote = TITLE.split('：', 1)[1]
    item = dict(title=TITLE, cover_title='投资回本需十二三年', subject=quote, evidence=[quote])
    package = T._package(item, quote, dict(method='source_quote', quote=quote), [item])
    assert '前提' in T.error(TITLE, package['title_rewrite'], quote)


def test_income_premise_cannot_be_dropped_from_both_title_and_selected_evidence():
    source='十二三年你今天的投资能回本。这个是基于十二三年以后，我们的生活水平就是人人均收入啊，比今天不减少。'
    title='林园：A股估值便宜，但回本要十二三年'
    cover='投资回本需要十二三年'
    evidence=['十二三年你今天的投资能回本。']
    item=dict(title=title,cover_title=cover,subject='投资',evidence=evidence)
    verdict=dict(method='cpu_text_review',appeal=4,reason='模拟新100实验中标题与封面同时丢失限定',**{k:True for k in T.CHECKS})
    package=T._package(item,source,verdict,[item])
    assert '收入前提' in T.error(title,package['title_rewrite'],source)
    assert not T.source_payback_condition_error('林园：收入不降，十二三年投资回本', '收入不降，十二三年回本', source)
    assert not T.source_payback_condition_error('林园：十二三年回本有什么前提？', '回本有什么前提？', source)
    assert not T.source_payback_condition_error(title,cover,'这家企业十二三年能回本。')
    with pytest.raises(ValueError):
        T._extractive(source,'林园',[],preferred='林园：十二三年你今天的投资能回本')


@pytest.mark.parametrize('source, title, cover', [
    ('十二三年回本，目前估值也是十二三倍。', '林园：十二三倍估值，回本需十二三年', '十二三倍估值回本需十二年'),
    ('预计要花两三年时间做研究。', '林园：研究这家公司需要两三年', '研究公司要花两年时间'),
    ('十二到十三年回本。', '林园：十二到十三年回本', '十三年能收回投资成本'),
])
def test_observed_range_endpoint_must_not_become_exact_quantity(source, title, cover):
    assert '范围' in T.quantity_range_error(title, cover, source)


def test_range_variants_and_exact_quantities_with_other_units():
    assert not T.quantity_range_error('十二到十三年回本', '回本要十二三年', '回本需要十二三年。')
    assert not T.quantity_range_error('用了十二年时间', '用了十二年时间', '估值十二三倍，我研究了十二年。')
    assert not T.quantity_range_error('发生在二零零三年', '二零零三年发生', '两三年后发生在二零零三年。')
    assert T._quantity_intervals('十二三年 二十三年 两三倍') == [
        ('年',12,13,'十二三年'), ('年',23,23,'二十三年'), ('倍',2,3,'两三倍')]


@pytest.mark.parametrize('ending',['但未来趋势不确定','但未来仍需观察','但未来仍要看变化'])
def test_real_source28_approved_review_cannot_add_a_cautious_tail(ending):
    source='但是我们的对这个未来趋势的判断，现在就是牛市初期。'
    title='林园：牛市初期形态明显，'+ending
    item=dict(title=title,cover_title='牛市初期形态已经形成',subject='牛市',evidence=[source])
    verdict=dict(method='cpu_text_review',appeal=5,reason='模拟实际审核将确定判断当成未来不确定',
                 **{k:True for k in T.CHECKS})
    package=T._package(item,source,verdict,[item])
    assert '不确定' in T.error(title,package['title_rewrite'],source)
    assert not T.unsupported_hedge_error(title,item['cover_title'],['这个市场未来走势我判断不了，不知道。'])


def test_real_source32_business_increment_cannot_become_no_cost_profit():
    evidence=['它利润的扩大不需要再去我去花钱，来产生利润。']
    assert T.incremental_cost_error('林园：这种企业利润不靠我花钱，我投小钱产大钱',
                                    '利润不靠花钱，小钱产大钱',evidence)
    assert not T.incremental_cost_error('林园：我喜欢不必追加投入，利润还能扩大的生意',
                                        '不必追加投入，利润还能扩大',evidence)
    assert not T.incremental_cost_error('林园：这个活动不用花钱', '参与活动不需要花钱',
                                        ['这次活动完全免费，不需要花钱。'])


def test_real_library220_uncertainty_must_apply_to_the_same_claim():
    evidence=['十亿一个跟头，可能哪一年碰到运气好的，', '几十年的。它就是这么这么个规律。']
    assert T.unsupported_hedge_error('林园：运气能赚十亿，但得看它是不是规律。',
                                      '运气与规律的博弈', evidence)
    assert not T.unsupported_hedge_error('林园：赚到一亿后，再赚十亿就很容易了',
                                          '赚到一亿后再赚十亿很容易', evidence)
    assert not T.unsupported_hedge_error('林园：运气能赚十亿，但得看它是不是规律。',
                                          '运气与规律有什么关系？', ['这次可能有运气，是不是规律还不好说。'])


def test_real_short_source34_contrast_is_not_an_investment_refusal():
    source=['我们看好的不是治疗这三种病的药物，是防止并发症的相关产品。']
    assert T.personal_action_error('林园：药物治疗这三种病我不投，但预防并发症的产品我看好',
                                   '不投治疗药，看好防并发症',source)
    assert not T.personal_action_error('林园：我看好的是预防并发症的相关产品',
                                       '我看好预防并发症的产品',source)
    assert not T.personal_action_error('林园：经营不好的公司再便宜我也不买',
                                       '经营不好，再便宜也不买',['经营不好的公司我是不会买入的。'])


def test_real_source13_refusal_cannot_become_reassurance():
    source='中石油我没买，因为它不符合我的标准。石油也可能有替代产品，这个我把握不住。'
    item=dict(title='林园：中石油垄断地位让我安心，但石油有替代品风险',
              cover_title='中石油垄断地位让我安心',subject='中石油',evidence=[source])
    assert '原文没有' in T._candidate_error(item,source,'林园',(),check_layout=False)


def test_actual_14b_cover_cannot_turn_my_standard_into_objective_rejection():
    source="没买，因为我觉得它不符合我的标准。按我们的说法，如果到石油，就中石油全世界这一家。"
    title="林园：中石油不符合我的标准，所以我没买。"
    item=dict(title=title,cover_title="中石油不符合标准",subject="中石油",evidence=[source])
    verdict=dict(method="cpu_text_review",appeal=4,reason="实际14B通过但封面遗漏个人范围",**{k:True for k in T.CHECKS})
    package=T._package(item,source,verdict,[item])
    assert "个人标准" in T._candidate_error(item,source,"林园",(),check_layout=False)
    assert "个人标准" in T.error(title,package["title_rewrite"],source)
    for cover in ("没买中石油", "中石油不符合我的标准", "中石油不符合我投资标准"):
        assert not T.cover_qualifier_error(title,cover)
    assert not T.cover_qualifier_error("林园：这家公司不符合上市标准", "公司不符合上市标准")
