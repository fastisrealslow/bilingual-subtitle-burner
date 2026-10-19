"""Published evidence, relation variants and unrelated-claim controls."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_rewrite as T


def actual():
    return json.loads((Path(__file__).parent / 'fixtures' /
                       'linyuan_published_comparison_modality.json').read_text())


def test_real_model_pass_cannot_erase_comparison_possibility_in_drafts_or_cached_proofs():
    row = actual()
    raw = row['raw_text']
    assert hashlib.sha256(raw.encode()).hexdigest() == row['raw_sha256']
    assert row['before']['forecast'] is None and row['before']['cached'] is None
    assert row['original_editorial_review']['complete_argument'] is True
    proof = row['title_rewrite']
    assert '比较判断' in T.error(row['title'], proof, raw)
    item = dict(title=row['title'], cover_title=row['cover_title'],
                subject=proof['subject'], evidence=proof['evidence'])
    assert '比较判断' in T._candidate_error(item, raw, '林园', [], check_layout=False)
    assert row == actual()  # no source edits or hand-written replacement copy


@pytest.mark.parametrize('source', [
    '可能它的估值比传统行业还低。',
    '估值可能低于传统行业。',
    '估值比传统行业低，完全有可能。',
    '估值或许比传统的行业更低。',
    '估值啊，那比传统的行，业还低了，完全有可能。',
])
def test_comparison_qualifier_before_metric_inside_predicate_or_after_claim(source):
    assert T.comparison_modality_error('林园：科技估值低于传统行业', '估值低于传统行业', source)


@pytest.mark.parametrize('metric,target,direction', [
    ('市盈率', '消费企业', '高'), ('成本', '进口产品', '低'),
    ('毛利率', '制造企业', '高'), ('价格', '同类产品', '低'),
])
def test_category_variants_keep_same_metric_direction_and_comparator(metric, target, direction):
    source = f'{metric}可能比{target}{direction}。'
    claim = f'{metric}{direction}于{target}'
    assert T.comparison_modality_error('林园：' + claim, claim, source)


@pytest.mark.parametrize('title,cover', [
    ('科技估值可能低于传统行业', '科技估值低于传统行业'),
    ('科技估值低于传统行业', '科技估值可能低于传统行业'),
    ('可能需求增长，科技估值低于传统行业', '需求可能增长'),
])
def test_title_and_cover_need_their_own_relation_qualifier(title, cover):
    assert T.comparison_modality_error(title, cover, '科技估值可能比传统行业低。')


@pytest.mark.parametrize('source,title,cover', [
    ('科技估值可能比传统行业低。', '科技估值可能低于传统行业', '估值可能低于传统行业'),
    ('科技估值比传统行业低。需求可能增长。', '科技估值低于传统行业', '估值低于传统行业'),
    ('科技估值可能比传统行业低。成本比进口产品低。', '成本低于进口产品', '成本低于进口产品'),
    ('科技估值可能比传统行业低。', '估值高于传统行业', '估值高于传统行业'),
    ('科技估值可能比传统行业低。', '估值低于消费企业', '估值低于消费企业'),
    ('科技估值比传统行业低。估值可能比传统行业低。', '估值低于传统行业', '估值低于传统行业'),
    ('需求可能增长，但是估值比传统行业低。', '估值低于传统行业', '估值低于传统行业'),
    ('科技估值可能比传统行业低。', '公司拥有独特产品', '独特产品很重要'),
])
def test_clear_comparisons_unrelated_qualifiers_and_other_claims_need_normal_review(source, title, cover):
    # None means this narrow guard found no defect, never semantic approval.
    assert T.comparison_modality_error(title, cover, source) is None
