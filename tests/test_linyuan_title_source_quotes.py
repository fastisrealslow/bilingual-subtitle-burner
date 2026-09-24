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
