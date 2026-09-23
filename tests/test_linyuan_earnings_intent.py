"""Actual all100 source46: a personal target is not a company return promise."""
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import title_rewrite as T


def source46():
    rows=json.loads((ROOT/'linyuan/simulations/benchmark-20260921/all-current-title-corpus.json').read_text())
    case=next(r for r in rows if r['source_id']==46)
    return ''.join(c['text'] for c in case['cues'])


def test_actual_full_run_title_cannot_reuse_all_true_review():
    source=source46()
    item=dict(title='林园：我只投能改变命运的公司，它们在持有阶段能赚到一万倍。',
              cover_title='我只投能改变命运的公司',subject='公司',
              evidence=['所以一定是在持有阶段我要赚够这一万倍。'])
    assert '本人目标' in T.forecast_copy_error(item['title'],item['cover_title'],source)
    proof=T._package(item,source,dict(method='cpu_text_review',appeal=5,
        reason='测试旧模型全部通过仍须执行现行检查',**{k:True for k in T.CHECKS}),[])['title_rewrite']
    assert '本人目标' in T.error(item['title'],proof,source)
    assert '本人目标' in T._candidate_error(item,source,'林园',[],check_layout=False)


@pytest.mark.parametrize('title,cover',[
    ('林园：持有龙头，我要赚够一万倍','持有龙头，目标一万倍'),
    ('林园：持有龙头，目标是一万倍','持有龙头，我想赚一万倍'),
    ('林园：我只投能改变命运的公司','我只投能改变命运的公司'),
])
def test_preserve_bold_personal_voice_and_other_angles(title,cover):
    assert T.earnings_intent_error(title,cover,source46()) is None


def test_cover_is_checked_independently_and_future_is_not_intent():
    assert T.earnings_intent_error('林园：我要赚够一万倍','持有公司能赚一万倍',source46())
    assert T.earnings_intent_error('林园：未来持有公司赚一万倍','持有龙头赚一万倍',source46())
    assert T.earnings_intent_error('林园：持有公司能赚一万倍','持有公司能赚一万倍','我想赚到一万倍。')


def test_historical_result_and_different_number_are_not_personal_goal():
    assert T.earnings_intent_error('林园：持有这家公司赚了一万倍','持有公司赚一万倍','我过去持有赚了一万倍，下一次我还想赚一万倍。') is None
    assert T.earnings_intent_error('林园：这家公司能赚一万倍','这家公司能赚一万倍','我想赚十倍。') is None
