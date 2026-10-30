"""Cash-adjusted valuation must retain its basis on each display surface."""
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_rewrite as T


def actual():
    return json.loads((Path(__file__).parent / 'fixtures/linyuan_actual_cash_adjusted_valuation.json').read_text())


def test_actual_approved_proof_is_rejected_on_full_source():
    f = actual()
    assert all(f['proof']['review'][k] for k in T.CHECKS)
    assert T.cash_adjusted_valuation_error(f['title'], f['cover'], f['transcript'])
    assert '现金' in T.error(f['title'], f['proof'], f['transcript'])
    assert T.forecast_copy_error(f['title'], f['cover'], f['transcript'])


@pytest.mark.parametrize('basis', ['剔除现金后', '扣除账面现金后', '现金撇开以后', '除去现金后'])
@pytest.mark.parametrize('quantity', ['十五', '28', '四十'])
@pytest.mark.parametrize('surface', ['title', 'cover'])
def test_other_numbers_and_each_surface_require_their_own_basis(basis, quantity, surface):
    source = f'{basis}，公司的市盈率是{quantity}倍。'
    good = f'{basis}市盈率是{quantity}倍'
    bad = f'公司估值仅{quantity}倍'
    title, cover = (bad, good) if surface == 'title' else (good, bad)
    assert T.cash_adjusted_valuation_error(title, cover, source)
    assert T.cash_adjusted_valuation_error(good, good, source) is None


def test_arabic_number_matches_chinese_adjusted_number():
    source = '扣除现金后，市盈率是二十五倍。'
    assert T.cash_adjusted_valuation_error('估值25倍', '财务思维看公司', source)


@pytest.mark.parametrize('title,cover,source', [
    ('公司估值二十倍', '公司估值二十倍', '好公司的估值是二十倍。'),
    ('公司估值二十倍', '公司估值二十倍', '不剔除现金，公司的估值是二十倍。'),
    ('公司估值二十倍', '公司估值二十倍', '无需扣除现金，公司的估值是二十倍。'),
    ('公司现金很多', '公司现金很多', '扣除现金后，公司估值二十倍。'),
    ('公司收入翻两倍', '公司收入翻两倍', '扣除现金后，公司估值两倍。'),
    ('公司市盈率十二倍', '公司市盈率十二倍', '公司市盈率十二倍。扣除现金后，公司估值八倍。'),
    ('另一家公司估值二十倍', '另一家公司估值二十倍', '这家公司要剔除现金。另一家公司估值二十倍。'),
    ('公司估值三十倍', '公司估值三十倍', '扣除现金后，市盈率二十倍。行业平均三十倍。'),
])
def test_unrelated_numbers_and_unadjusted_claims_are_not_rejected(title, cover, source):
    assert T.cash_adjusted_valuation_error(title, cover, source) is None


def test_actual_source_with_explicit_basis_on_both_surfaces_is_not_rejected():
    f = actual()
    assert T.cash_adjusted_valuation_error(f['title'], '剔除现金后好公司估值二十倍以内', f['transcript']) is None


@pytest.mark.parametrize('source', [
    '现金并不算多，公司的估值是二十倍。',
    '现金不能剔除，公司的估值是二十倍。',
    '现金无需扣除，公司的估值是二十倍。',
])
def test_cash_amount_or_negated_adjustment_is_not_an_adjusted_basis(source):
    assert T.cash_adjusted_valuation_error('公司估值二十倍', '公司估值二十倍', source) is None
