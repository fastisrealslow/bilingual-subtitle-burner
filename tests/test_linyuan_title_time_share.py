import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_quantity_context as Q
import title_rewrite as T

SOURCE = '股市上涨的时间只有百分之五，下跌的时间或者盘整的时间百分之九十五。'


def test_actual_receipt_bound_cover_and_cached_review():
    actual = json.loads((Path(__file__).parent / 'fixtures' /
                         'linyuan_actual_published_time_share.json').read_text())
    assert '时间分母' in Q.error(actual['title'], actual['cover_title'], actual['raw_text'])
    assert '时间分母' in T.error(actual['title'], actual['title_rewrite'], actual['raw_text'])


@pytest.mark.parametrize('subject,action,value', [
    ('股市', '上涨', '百分之五'), ('股票', '下跌', '20%'),
    ('市场', '盘整', '百分之八十'), ('指数', '横盘', '30％'),
])
def test_same_quantity_movement_and_subject_on_each_surface(subject, action, value):
    source = f'{subject}{action}的时间仅占{value}。'
    bad = f'{subject}{action}仅占{value}'
    good = f'{subject}{action}时间仅占{value}'
    assert Q.error(bad, good, source)
    assert Q.error(good, bad, source)
    assert Q.error(good, good, source) is None
    assert T.population_scope_error(good, bad, source)


@pytest.mark.parametrize('copy', [
    '股市上涨仅占百分之五', '股市上涨只有5%',
    '时间宝贵，股市上涨只有5%',
])
def test_missing_denominator(copy):
    assert Q.error(copy, copy, SOURCE)


@pytest.mark.parametrize('copy', [
    '股市上涨时间只有百分之五', '股市上涨只有5%，其余时间下跌盘整',
    '股市上涨交易日占5%', '股市上涨天数仅占5%',
    '我满仓等待上涨', '股票上涨5%', '股市下跌5%', '股市上涨50%',
])
def test_preserved_denominator_other_angle_or_unmatched_quantity(copy):
    assert Q.error(copy, copy, SOURCE) is None


@pytest.mark.parametrize('source', [
    '股市上涨百分之五。', '股市上涨5%。研究时间占5%。',
    SOURCE + '今年股市上涨百分之五。',
    '股市上涨的时间只有百分之五十。',
])
def test_price_change_or_other_statistic_does_not_impose_time_unit(source):
    assert Q.error('股市上涨5%', '股市上涨5%', source) is None


@pytest.mark.parametrize('extra', [
    '并不是股市上涨百分之五。', '股市上涨百分之五吗？',
    '不是说股市上涨百分之五。', '股市上涨百分之五？',
])
def test_negated_or_questioned_price_move_is_not_independent_support(extra):
    assert Q.error('股市上涨5%', '股市上涨5%', SOURCE + extra)
