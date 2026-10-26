"""A percentage estimate must survive independently on each copy surface."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_quantity_context as Q
import title_rewrite as T
import produce_cn as P


@pytest.fixture
def actual():
    path = Path(__file__).with_name('fixtures') / 'linyuan_tentative_percentage_publication.json'
    row = json.loads(path.read_text())
    assert hashlib.sha256(row['raw_text'].encode()).hexdigest() == row['transcript_sha256']
    return row


def test_actual_published_title_and_cached_cpu_approval_are_rechecked(actual):
    title, cover, raw = actual['title'], actual['cover'], actual['raw_text']
    assert actual['title_rewrite']['review']['preserves_qualifiers'] is True
    assert '百分比' in T.forecast_copy_error(title, cover, raw)
    assert '百分比' in T.error(title, actual['title_rewrite'], raw)
    # This legacy envelope only stores selected evidence, omitting 可能.
    # Metadata alone cannot authenticate the full source; delivery must replay it.
    assert T.metadata_error(title, actual['title_rewrite']) is None
    assert '百分比' in P.title_quality_error(title, '林园', raw,
                                            rewrite_proof=actual['title_rewrite'])
    item = dict(title=title, cover_title=cover, **{k: actual['title_rewrite'][k]
                                                for k in ('subject', 'evidence')})
    assert '百分比' in T._candidate_error(item, raw, '林园', (), check_layout=False)


@pytest.mark.parametrize('source,copy', [
    ('需求量可能只有供应量的百分之三十。', '需求量只有供应量的30%'),
    ('需求占比也许是百分之二十五。', '需求占比为25％'),
    ('利润增速预计达到12.5%。', '利润增长百分之十二点五'),
    ('市场份额估计是百分之三十点零五。', '市场份额是30.05%'),
    ('成本占比大概只有百分之四十。', '成本占比只有40%'),
])
def test_other_percentages_and_written_number_variants(source, copy):
    assert Q.percentage_hedge_error(copy, '', source)
    assert Q.percentage_hedge_error('', copy, source)


@pytest.mark.parametrize('title,cover', [
    ('需求可能只有30%', '需求只有30%'),
    ('需求只有30%', '需求可能只有30%'),
    ('可能回暖，需求只有30%', ''),
    ('可能回暖。但需求只有30%', ''),
    ('需求不可能只有30%', ''),
    ('需求没可能只有30%', ''),
    ('不预计需求只有30%', ''),
    ('不是可能需求只有30%', ''),
    ('需求只有30%？', ''),
    ('他认为“可能回暖”，需求只有30%', ''),
])
def test_unrelated_or_negated_hedge_and_question_do_not_supply_the_qualifier(title, cover):
    assert Q.percentage_hedge_error(title, cover, '需求可能只有百分之三十。')


@pytest.mark.parametrize('source,title,cover', [
    ('需求可能只有百分之三十。', '需求可能只有30%', '需求估计只有30%'),
    ('需求可能只有百分之三十。', '供求不匹配', '供大于求'),
    ('需求可能只有百分之三十。', '医药市场需求', '需求只有20%'),
    ('需求只有百分之三十。', '需求只有30%', '需求只有30%'),
    ('可能经济回暖，需求只有30%。', '需求只有30%', ''),
    ('可能回暖。但是需求只有30%。', '需求只有30%', ''),
    ('可能走强但需求只有30%。', '需求只有30%', ''),
    # Different source occurrences cannot borrow one another's certainty.
    ('需求可能只有30%。另一个行业占比是30%。', '另一行业占比30%', ''),
    ('需求不可能只有30%。', '需求占比30%', ''),
    ('需求没有可能只有30%。', '需求占比30%', ''),
])
def test_retained_estimates_and_definite_or_ambiguous_sources_need_normal_review(source, title, cover):
    assert Q.percentage_hedge_error(title, cover, source) is None


def test_literal_fallback_cannot_drop_prefix_uncertainty():
    source = '过剩甚至可能是现在的需求量只有供应量的百分之三十。'
    with pytest.raises(ValueError, match='完整观点标题'):
        T._extractive(source, '林园', (), preferred='林园：需求量只有供应量的百分之三十',
                      only_preferred=True)


def test_actual_other_copy_angle_remains_eligible(actual):
    # Does not endorse the rest of the video: only this new guard is checked.
    assert Q.percentage_hedge_error(actual['cover'], actual['cover'], actual['raw_text']) is None


@pytest.mark.parametrize('raw', ['可能百分之3十', '可能百分之三0点五', '可能百分之三十点十'])
def test_unparseable_asr_numbers_stay_for_original_review(raw):
    assert Q.percentage_hedge_error('需求30%', '', raw) is None
