import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import title_rewrite as T


@pytest.mark.parametrize('asset', ['甲公司', '乙基金', '商业物业'])
@pytest.mark.parametrize('when', ['当初', '当年', '最初'])
@pytest.mark.parametrize('field', ['title', 'cover'])
def test_each_copy_preserves_historical_cost(asset, when, field):
    source = f'我们{when}买入{asset}的价格，现在一年能把本钱收回来。'
    good = f'{asset}一年收回当初成本'
    bad = f'{asset}一年收回本钱'
    title, cover = (bad, good) if field == 'title' else (good, bad)
    assert '历史买入成本' in T.source_payback_condition_error(title, cover, source)


@pytest.mark.parametrize('source,title,cover', [
    ('当初买入甲公司股价，现在一年能把本钱分走。', '甲公司一年收回历史成本', '甲公司一年收回原始本金'),
    ('当年买入甲公司的价格，现在一年能把本钱收回来。', '当年买入甲公司，一年回本', '当初买入甲公司，一年回本'),
    ('按现在甲公司的价格，一年能收回本钱。', '甲公司一年回本', '甲公司一年收回本钱'),
    ('当初买入甲公司价格，现在一年能把本钱分走。乙基金三年回本。', '乙基金三年回本', '乙基金三年收回本钱'),
    ('当初买入甲公司价格，现在一年能把本钱分走。', '甲公司持有合算', '甲公司继续持有'),
    ('当初买入甲公司价格，现在一年不能把本钱收回来。', '甲公司一年不能回本', '甲公司一年无法收回本钱'),
    ('当初买入甲公司价格，现在一年能把本钱分走。', '甲公司回本有什么前提？', '甲公司回本的条件是什么？'),
    ('当初买入甲公司价格很低，按现在甲公司股价，一年能把本钱收回来。', '甲公司一年回本', '甲公司一年收回本钱'),
])
def test_supported_and_unrelated_copies(source, title, cover):
    assert not T.source_payback_condition_error(title, cover, source)


def test_actual_published_proof_and_candidate():
    case = json.loads((ROOT / 'tests/fixtures/linyuan_published_historical_cost.json').read_text())
    proof = case['proof']
    assert all(proof['review'][key] is True for key in T.CHECKS)
    assert '历史买入成本' in T.error(case['title'], proof, case['transcript'])
    candidate = dict(title=case['title'], cover_title=proof['cover'], subject=proof['subject'], evidence=proof['evidence'])
    assert '历史买入成本' in T._candidate_error(candidate, case['transcript'], '林园', (), check_layout=False)
