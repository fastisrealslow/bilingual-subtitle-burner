"""Short speech must retain a predicate and pass the full source proof."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import headline_policy as H
import title_rewrite as T
import produce_cn as P


@pytest.mark.parametrize('text',['我没买中石油','光伏我没参与','不加杠杆','还没进入牛市','牛市还没来','我买医药'])
def test_complete_short_actions_are_not_padded(text):
    assert H.copy_length_ok(text,18)


@pytest.mark.parametrize('text',['投资风险','还有一个','股息率','我没有买','我不买了','这个便宜','因为不买','投什么'])
def test_short_labels_and_missing_objects_are_not_admitted(text):
    assert not H.copy_length_ok(text,18)


def test_short_source_choice_survives_binding_cover_and_production_gate():
    source='我没买中石油。'
    item=T.bind_guest_candidate(dict(a_focus=dict(a_claim='我没买中石油',b_evidence_ids=[0]),
        title='林园：我没买中石油',cover_title='我没买中石油'),{},[source],{'中石油':[0]},[0])
    assert item['cover_title']=='我没买中石油'
    assert T._candidate_error(item,source,'林园',()) is None
    proof=T._package(item,source,dict(method='cpu_text_review',appeal=4,
        reason='原文明确说没有买中石油，封面和标题保留个人动作及具体对象。',
        **{k:True for k in T.CHECKS}),[item])
    assert P.title_quality_error(proof['title'],'林园',source,rewrite_proof=proof['title_rewrite']) is None
    assert T.error(proof['title'],proof['title_rewrite'],'我买了中石油。')


def test_four_character_quote_cannot_skip_the_old_six_character_trace_gate():
    assert P.title_quality_error('林园：不加杠杆','林园','我从来不加杠杆。') is None
    assert '原话' in P.title_quality_error('林园：不加杠杆','林园','我们专注企业产品。')


def test_company_trait_cannot_become_profit_guarantee_despite_model_approval():
    source='我们看全世界能够赚钱长期赚钱的公司，它都有一个特性，就是独门生意，别人很难攻破。'
    item=dict(title='林园：独门生意是长期盈利的保障',cover_title='独门生意，长期盈利',subject='独门生意',evidence=[source])
    package=T._package(item,source,dict(method='cpu_text_review',appeal=5,
        reason='重放真实模型的错误全通过评分，不能让该评分替代原文中的实际关系。',
        **{k:True for k in T.CHECKS}),[item])
    assert '新增' in T.error(package['title'],package['title_rewrite'],source)
    natural={**item,'title':'林园：长期赚钱的公司，都有独门生意','cover_title':'长期赚钱的公司有独门生意'}
    assert T._candidate_error(natural,source,'林园',()) is None
