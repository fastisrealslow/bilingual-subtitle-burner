import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
from title_market_impression import impression_error


def test_rejects_both_actual_fields_of_the_frozen_source7_output():
    case = json.loads((ROOT / 'linyuan/simulations/benchmark-20260921/production-title-corpus.json').read_text())[0]
    source = ''.join(c['text'] for c in case['cues'])
    assert impression_error('林园：十二三倍估值，股民都亏钱，但政策会出手', '市场的估值判断', source)
    assert impression_error('市场的估值判断', '十二三倍估值，股民亏钱政策出手', source)


def test_retains_sourced_impression_without_banning_other_supported_angles():
    source = '这个位置所有的股民感觉的都是亏钱。但是值得投资的。'
    assert impression_error('我感觉股民都是亏钱的', '股民感觉都亏钱', source) is None
    assert impression_error('不挣钱，但值得投资', '不挣钱但值得投资', source) is None


def test_does_not_borrow_a_different_subject_or_a_different_impression():
    assert impression_error('股民都亏钱', '股民都亏钱', '股民去年都亏钱。') is None
    assert impression_error('投资者都亏钱', '投资者都亏钱', '股民感觉都是亏钱。') is None
    assert impression_error('股民都亏钱', '股民都亏钱', '股民感觉估值很低。账户统计显示股民都亏钱。') is None


def test_a_question_about_policy_does_not_erase_the_loss_premise():
    assert impression_error('股民都亏钱，政策会出手吗？', '股民感觉亏钱', '这个位置股民感觉都是亏钱。')


def test_source8_cannot_turn_not_bull_into_bear_or_a_feeling_into_a_statistic():
    source = '还没有进入牛市。但是我的感觉现在亏钱的人多。'
    assert impression_error('现在市场还是熊市', '还没有进入牛市', source)
    assert impression_error('现在还没有进入牛市', '亏钱的人多', source)
    assert impression_error('还没有进入牛市，我感觉亏钱的人多', '还没有进入牛市', source) is None


def test_an_actual_bear_market_assertion_is_not_banned():
    assert impression_error('现在市场还是熊市', '市场还是熊市', '还没有进入牛市。现在市场还是熊市。') is None
    assert impression_error('现在市场还是熊市', '市场还是熊市', '还没有进入牛市。您认为现在市场是熊市吗？')


def test_production_forecast_check_uses_the_same_guard():
    from title_rewrite import forecast_copy_error
    assert forecast_copy_error("股民都亏钱", "股民都亏钱", "这个位置所有的股民感觉的都是亏钱。")
