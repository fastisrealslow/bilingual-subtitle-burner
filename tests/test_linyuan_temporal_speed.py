"""Past/current speed relations must survive editorial compression."""
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_rewrite as T


@pytest.mark.parametrize('source,title,cover', [
    ('企业比以前成长得更快了。', '企业成长没以前快', '企业成长慢了'),
    ('公司发展比过去快。', '公司发展比过去慢', '公司发展慢了'),
    ('行业比从前增长更快。', '行业增长没有以前快', '行业增长更慢'),
    ('企业赚钱比以前更快。', '企业赚钱没以前快', '企业赚钱更慢'),
    ('经济增长比过去更慢。', '经济增长比过去更快', '经济增长比过去更快'),
])
def test_explicit_comparison_cannot_reverse(source, title, cover):
    assert T.temporal_speed_error(title, cover, source)


@pytest.mark.parametrize('source,title,cover', [
    ('企业成长比以前更快。', '企业成长比以前快', '企业成长比以前快'),
    ('企业成长比以前慢。', '企业成长没以前快', '企业成长慢了'),
    ('如果企业成长比以前快，才会继续持有。', '企业成长慢了', '企业成长慢了'),
    ('企业成长比以前快吗？', '企业成长慢了', '企业成长慢了'),
    ('不是企业成长比以前更快。', '企业成长慢了', '企业成长慢了'),
    ('企业成长比以前快。公司发展比过去慢。', '公司发展慢了', '公司发展慢了'),
    ('甲企业成长比以前更快。', '乙企业成长慢了', '乙企业成长慢了'),
    ('企业成长比以前快。企业成长比以前慢。', '企业成长慢了', '企业成长慢了'),
    ('企业成长比以前快。', '以前企业成长慢了', '以前企业成长慢了'),
    ('企业成长比以前快。', '企业赚钱更慢', '企业赚钱更慢'),
    ('企业成长比以前快。', '企业成长比以前慢？', '企业成长比以前慢？'),
    ('企业以前成长比过去快。', '企业成长慢了', '企业成长慢了'),
    ('企业未来成长比以前快。', '企业成长慢了', '企业成长慢了'),
    ('企业成长比以前快。', '企业未来成长比以前慢', '企业未来成长比以前慢'),
])
def test_other_relations_are_left_for_semantic_review(source, title, cover):
    assert T.temporal_speed_error(title, cover, source) is None


def test_real_published_proof_and_fresh_candidate_reject_model_approval():
    case = json.loads((Path(__file__).parent / 'fixtures/linyuan_published_time_comparison.json').read_text())
    proof, source = case['proof'], case['transcript']
    assert all(proof['review'][k] is True for k in T.CHECKS)
    assert '时间比较方向' in T.error(case['title'], proof, source)
    item = dict(title=case['title'], cover_title=proof['cover'], subject=proof['subject'], evidence=proof['evidence'])
    assert '时间比较方向' in T._candidate_error(item, source, '林园', (), False)
    assert T.temporal_speed_error('林园：企业成长比以前快', '企业成长比以前快', source) is None


def test_cover_is_checked_independently_of_a_correct_title():
    assert T.temporal_speed_error('企业成长比以前快', '企业成长慢了', '企业比以前成长得更快。')
