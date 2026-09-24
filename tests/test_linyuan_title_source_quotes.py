"""General regressions from the Sep24 published-video audit, not ID overrides."""
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import title_rewrite as T


@pytest.mark.parametrize('title,cover',[
    ('林园：茅台酒贵一点也没关系，只要有人愿意买。','茅台酒：贵一点也没关系'),
    ('林园：可以扩大生产，只要需求持续增长','可以扩大生产'),
    ('林园：只要企业经营正常，我就继续持有','我就继续持有'),
])
def test_sufficient_condition_cannot_disappear_from_cover(title,cover):
    assert T.cover_qualifier_error(title,cover)


@pytest.mark.parametrize('cover',[
    '只要需求持续增长，可以扩大生产',
    '扩大生产的前提是什么？',
])
def test_complete_condition_or_a_question_about_it_remains_allowed(cover):
    assert not T.cover_qualifier_error('林园：可以扩大生产，只要需求持续增长',cover)


def test_second_invented_source_quote_does_not_hide_behind_first_true_quote():
    reason='候选标题中的“贵一点也没关系”和“有人愿意买”直接引用了原话，与原文中“贵一点也没有”和“有人愿意买”的表述一致。'
    assert '有人愿意买' in T.review_source_quote_error(reason,'贵一点也没有。')
    assert T.review_source_quote_error('与原文中「需求一直增长」以及「产能充足」一致。','需求一直增长。')


@pytest.mark.parametrize('left,right', [('“','”'), ('‘','’'), ('"','"'), ("'","'"), ('「','」')])
def test_quote_style_cannot_bypass_verbatim_source_check(left, right):
    reason=f'候选表达对应原文{left}需求一直增长{right}和{left}产能充足{right}。'
    assert '产能充足' in T.review_source_quote_error(reason, '需求一直增长。')
    assert not T.review_source_quote_error(reason, '需求一直增长，产能充足。')


@pytest.mark.parametrize('title,cover,source', [
    ('林园：中国是全世界最好的经济，美国风险大', '中国是全世界最好的经济', '我说中国的经济是全世界最好的。'),
    ('林园：甲公司的现金流很好', '甲公司是很好的现金流', '甲公司的现金流很好。'),
    ('林园：这家企业是最高的股息率', '股息率还有比较优势', '这家企业的股息率是最高的。'),
])
def test_compression_must_not_turn_owner_into_its_attribute(title, cover, source):
    assert T.subject_attribute_error(title, cover, source)


@pytest.mark.parametrize('title,cover,source', [
    ('林园：中国的经济是全世界最好的', '中国经济是最好的', '我说中国的经济是全世界最好的。'),
    ('林园：中国是经济强国', '中国是经济强国', '中国的经济很好。'),
    ('林园：评价企业要看现金流', '现金流是关键', '这家企业的现金流很好。'),
    ('林园：核心是公司的现金流', '核心是现金流', '公司的现金流很好。'),
])
def test_complete_attributes_and_normal_predicates_remain_allowed(title, cover, source):
    assert not T.subject_attribute_error(title, cover, source)


def test_published_owner_attribute_mismatch_rejects_cached_and_new_candidates():
    case=json.loads((ROOT/'tests/fixtures/linyuan_published_subject_regression.json').read_text())
    proof=case['proof']
    assert all(proof['review'][k] is True for k in T.CHECKS)
    assert '丢失被评价的对象' in T.error(case['title'],proof,case['transcript'])
    item=dict(title=case['title'],cover_title=proof['cover'],subject=proof['subject'],evidence=proof['evidence'])
    assert '丢失被评价的对象' in T._candidate_error(item,case['transcript'],'林园',(),False)


@pytest.mark.parametrize('reason',[
    '原文中“需求一直增长”和“产能充足”对应标题，引用完整。',
    '原文说：“需求一直增长”，标题保留了需求变化。',
    '候选标题中的“需求看起来很好”是概括，原文具体说明了增长情况。',
    '原文的意思是需求良好，这是一种概括而不是逐字引用。',
])
def test_true_source_quotes_and_explicit_paraphrases_remain_allowed(reason):
    assert not T.review_source_quote_error(reason,'需求一直增长，产能充足。')


def test_real_published_proof_fails_current_gate_even_with_all_model_flags_true():
    case=json.loads((ROOT/'tests/fixtures/linyuan_published_condition_regression.json').read_text())
    proof=case['proof']
    assert all(proof['review'][k] is True for k in T.CHECKS)
    assert '明确前提' in T.error(case['title'],proof,case['transcript'])
    # Repairing cover compression alone must not launder the false source quote.
    item=dict(title=case['title'],cover_title='茅台酒贵的前提是什么？',
              subject=proof['subject'],evidence=proof['evidence'])
    repaired=T._package(item,case['transcript'],proof['review'],[])['title_rewrite']
    assert '原文中不存在' in T.error(item['title'],repaired,case['transcript'])
