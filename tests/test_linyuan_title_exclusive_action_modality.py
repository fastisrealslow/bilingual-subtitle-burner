import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))

import title_rewrite as rewrite


SOURCE = (
    '那些核心资产，核心资产高了，我不敢买啊，太贵啊，你怎么办？'
    '我只买可转债。你们现在去仔细看看那个可转债的价格，十年不遇的机遇。'
)
TITLE = '林园：核心资产太贵不敢买，只敢买十年不遇的可转债'
COVER = '核心资产太贵，只买可转债'


def test_published_claim_rejected_in_candidate_and_proof_readback():
    item = dict(title=TITLE, cover_title=COVER, subject='可转债', evidence=['我只买可转债。'])
    review = dict(method='cpu_text_review', appeal=5, reason='原文谈到核心资产与购买可转债的个人选择。')
    review.update(dict.fromkeys(rewrite.CHECKS, True))
    proof = rewrite._package(item, SOURCE, review, [item])['title_rewrite']

    assert '只敢' in rewrite._candidate_error(item, SOURCE, '林园', ())
    assert '只敢' in rewrite.error(TITLE, proof, SOURCE)


@pytest.mark.parametrize(('title', 'cover', 'source'), [
    ('林园：我只敢买可转债', COVER, SOURCE),
    ('林园：我只买可转债', '仅敢买可转债', SOURCE),
    ('林园：高波动股票不买，只敢持有国债', '只持有国债', '我不敢持有高波动股票。我们就只持有国债而已。'),
    ('林园：只敢参与港股', '个人选择港股', '我只参与港股。主持人问，你说我只敢参与港股？'),
    ('林园：只敢买可转债', COVER, SOURCE + '但我只敢买可转债基金。'),
])
def test_same_action_and_object_cannot_gain_an_exclusive_courage_claim(title, cover, source):
    assert rewrite.exclusive_action_modality_error(title, cover, source)


@pytest.mark.parametrize(('title', 'cover', 'source'), [
    ('林园：核心资产太贵不敢买，只买可转债', COVER, SOURCE),
    ('林园：只敢买可转债', '只敢买可转债', SOURCE + '但我只敢买可转债。'),
    ('林园：只敢买黄金', '只敢买黄金', SOURCE + '我只敢买黄金。'),
    ('林园：只敢买可转债基金', '只敢买可转债基金', SOURCE + '我只敢买可转债基金。'),
    ('林园：只敢持有可转债', '只敢持有可转债', SOURCE + '我只敢持有可转债。'),
    ('林园：我不只敢买可转债', '还敢买国债', SOURCE + '我不只敢买可转债，还敢买国债。'),
])
def test_related_source_does_not_override_supported_copy(title, cover, source):
    assert rewrite.exclusive_action_modality_error(title, cover, source) is None
