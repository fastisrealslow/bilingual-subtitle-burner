"""Published failure, related advice variants, and ordinary counterexamples."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_advice_support as A
import title_rewrite as T


def fixture():
    return json.loads((Path(__file__).parent / 'fixtures/linyuan_negative_advice.json').read_text())


def test_actual_published_proof_cannot_reuse_positive_review_or_host_question():
    f = fixture()
    proof = f['proof']
    item = dict(title=proof['title'], cover_title=proof['cover'],
                subject=proof['subject'], evidence=proof['evidence'])
    assert '不建议' in f['raw_text']  # Only the full dialogue has the host premise.
    assert all('不建议' not in q for q in proof['evidence'])
    assert '嘉宾原文证据' in T._candidate_error(item, f['raw_text'], '林园', ())
    assert '嘉宾原文证据' in T.error(proof['title'], proof, f['raw_text'])
    assert '嘉宾原文证据' in T.error(proof['title'], proof)
    assert proof == f['proof']


@pytest.mark.parametrize('copy', [
    '行业有泡沫，不推荐现在入市', '市场机会巨大但别进场',
    '医药公司不建议买入', '现在不要卖出股票', '行业龙头不宜继续持有',
])
def test_advice_requires_same_action_evidence_on_each_surface(copy):
    evidence = ['这个过程机会巨大，但我不想。', '行业发展需要资金投入。']
    assert A.error('林园：' + copy, '行业发展需要资金', evidence)
    assert A.error('林园：行业发展需要资金', copy, evidence)


@pytest.mark.parametrize('copy,evidence', [
    ('不建议现在进市场', ['不要在这个时候买入股票。']),
    ('别急着买股票', ['我不推荐现在进入市场。']),
    ('不推荐卖出股票', ['不要轻易减仓。']),
    ('不宜持有股票', ['不建议继续持仓。']),
    ('不建议买股票', ['买什么？不要买不懂的股票。']),
    ('投资机会巨大', ['行业大发展必须有泡沫。']),
    ('别人买股票也有机会', ['我们是在观察市场。']),
    ('区别不同投资方式', ['要了解不同的投资方式。']),
    ('不建议招聘太多人', ['人员安排要看经营需求。']),
])
def test_equivalent_advice_and_nontrading_copy_survive(copy, evidence):
    assert A.error('林园：' + copy, copy, evidence) is None


@pytest.mark.parametrize('quote', [
    '我不想参与。', '我没买股票。', '您是不建议进市场的。',
    '您是否不建议买股票？', '不是不建议买股票。',
    '我不建议卖出股票。', '我不建议继续持仓。',
    '为什么不建议买股票？',
])
def test_personal_choice_question_denial_or_different_action_is_not_buy_advice(quote):
    assert A.error('林园：不建议买股票', '市场不要进', [quote])


def test_ordinary_guest_claim_from_same_real_file_remains_eligible():
    f = fixture()
    item = dict(title='林园：行业大发展必须有泡沫', cover_title='行业发展必须有泡沫',
                subject='行业', evidence=['他一个行业要发展，必须要有泡沫，'])
    assert T._candidate_error(item, f['raw_text'], '林园', ()) is None


def test_positive_model_verdict_cannot_rescue_unsupported_advice(monkeypatch):
    f = fixture()
    proof = f['proof']
    item = dict(title=proof['title'], cover_title=proof['cover'],
                subject=proof['subject'], evidence=proof['evidence'])
    def call(prompt):
        assert '独立核对' not in prompt  # Rejected before a permissive reviewer.
        return json.dumps(dict(candidates=[deepcopy(item) for _ in range(3)]), ensure_ascii=False)
    def blocked(*args, **kwargs):
        raise ValueError('no eligible source quote in this test')
    monkeypatch.setattr(T, '_extractive', blocked)
    with pytest.raises(ValueError, match='no eligible source quote'):
        T.generate(f['raw_text'], model=call)
