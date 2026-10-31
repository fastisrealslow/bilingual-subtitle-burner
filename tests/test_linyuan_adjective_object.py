"""Complete copy must retain the noun after an attributive adjective."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import headline_policy as H
import produce_cn as P
import title_rewrite as T


def actual():
    return json.loads((Path(__file__).parent / 'fixtures' /
                       'linyuan_actual_published_adjective_object.json').read_text())


def test_actual_published_copy_is_rejected_despite_positive_model_proof():
    row = actual()
    before = deepcopy(row)
    assert row['editorial_review']['standalone_opening'] is True
    assert all(row['title_rewrite']['review'][k] for k in T.CHECKS)
    assert T.copy_fragment(row['title'])
    assert '残句' in T.error(row['title'], row['title_rewrite'], row['raw_text'])
    assert row == before


@pytest.mark.parametrize('copy', [
    '行业能否持续产生强势', '能否持续产生优秀，值得研究',
    '企业需要培育一批优质', '团队要培养真正卓越的',
    '我们筛选更优秀', '投资要挑选优质', '长期寻找最优秀',
    '研究行业能否找到强势。',
])
def test_incomplete_adjective_objects_fail_all_copy_entrypoints(copy):
    assert H.readability_error(copy)
    assert not H.complete(copy)
    assert T.copy_fragment(copy)
    assert P.title_quality_error('林园：' + copy, '林园', copy)
    assert not H.title_candidates(copy + '。')


@pytest.mark.parametrize('copy', [
    '行业能否持续产生强势公司', '企业要培育一批优质产品',
    '团队需要培养真正卓越的人才', '投资要筛选优秀企业',
    '投资要挑选优质资产', '研究行业能否找到强势企业',
    '行业能否持续强势', '企业要保持强势', '优质企业值得长期投资',
    '企业可以产生强势现金流', '企业需要培养竞争优势',
    '我们找到了优秀企业，也找到了优质资产',
])
def test_complete_objects_and_state_predicates_remain_eligible(copy):
    assert H.readability_error(copy) is None
    assert not T.copy_fragment(copy)


@pytest.mark.parametrize('bad_field', ['title', 'cover_title'])
def test_generation_checks_title_and_cover_independently(bad_field):
    source = '我们研究行业能否持续产生强势公司，企业必须能够长期存在。'
    item = dict(title='林园：行业能否持续产生强势公司',
                cover_title='行业能否持续强势', subject='行业', evidence=[source])
    item[bad_field] = ('林园：' if bad_field == 'title' else '') + '行业能否持续产生强势'
    assert '残句' in T._candidate_error(item, source, '林园', ())


def test_cached_cover_cannot_borrow_the_titles_complete_object():
    row = actual()
    proof = deepcopy(row['title_rewrite'])
    title = '林园：行业能否持续产生强势公司'
    proof.update(title=title, cover='能否持续产生强势')
    proof['review']['copy_sha256'] = T._binding(
        title, proof['cover'], proof['subject'], proof['evidence'])
    assert '残句' in T.error(title, proof, row['raw_text'])


def test_source_quote_fallback_does_not_promote_a_fragment():
    bad = '行业能否持续产生强势'
    assert H.quote_candidates(bad + '。') == []
    with pytest.raises(P.VisualQualityError):
        P._fallback_quote_title([dict(text=bad + '。', start=0, end=4)], [0], '林园')
