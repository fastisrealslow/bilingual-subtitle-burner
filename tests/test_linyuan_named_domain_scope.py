import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import title_rewrite as T


def test_actual_approved_rewrite_cannot_resolve_personal_circle_from_host_topic():
    row = json.loads((ROOT / 'tests/fixtures/linyuan_deictic_domain_scope.json').read_text())
    assert row['old_error'] is None
    assert all(row['proof']['review'][key] is True for key in T.CHECKS)
    assert '领域归属' in T.error(row['title'], row['proof'], row['source'])
    item = dict(title=row['title'], cover_title=row['cover'],
                subject=row['proof']['subject'], evidence=row['proof']['evidence'])
    assert '领域归属' in T._candidate_error(item, row['source'], '林园', (), check_layout=False)
    # Cached proof with only selected evidence must also be checked.
    assert '领域归属' in T.error(row['title'], row['proof'])


@pytest.mark.parametrize('topic', ['科技', '医药', '消费', '新能源'])
@pytest.mark.parametrize('suffix', ['圈', '行业', '领域', '板块', '赛道'])
def test_question_topic_does_not_define_personal_domain(topic, suffix):
    source = f'您看好{topic}股吗？我没留意这些，我只关注自己熟悉的领域。'
    copy = f'专注{topic}{suffix}'
    assert T.named_domain_scope_error('林园：' + copy, '只关注熟悉的领域', source)
    assert T.named_domain_scope_error('林园：只关注熟悉的领域', copy, source)


@pytest.mark.parametrize('extra', [
    '你只研究科技股吗？', '我不研究科技股。', '我没有研究科技股。',
    '我只研究医药股。', '科技股最近很热。',
    '我只研究科技股？', '书里写着“我只研究科技股”。',
    '假如我只研究科技股。',
])
def test_question_negation_other_domain_and_topic_mentions_are_not_support(extra):
    source = '我只留意我这个圈圈的东西。' + extra
    assert T.named_domain_scope_error('林园：专注科技圈', '专注科技圈', source)


@pytest.mark.parametrize('copy,source', [
    ('只留意自己熟悉的圈子', '科技股？我没留意。我只留意我这个圈圈的东西。'),
    ('只关注熟悉的领域', '我只关注自己熟悉的领域。'),
    ('只关注能力范围内的领域', '我只关注自己熟悉的领域。'),
    ('专注科技圈', '我只留意我这个圈圈的东西。我只研究科技股。'),
    ('专注医药领域', '我只关注自己熟悉的领域。我一直研究医药股。'),
    ('专注消费行业', '我只研究自己熟悉的行业。我的圈子就是消费行业。'),
    ('只关注科技行业', '我只关注科技行业。'),
    ('科技股最近很热', '科技股最近很热。我只留意我这个圈圈的东西。'),
])
def test_generic_copy_explicit_personal_scope_and_unrelated_claims_keep_normal_review(copy, source):
    assert T.named_domain_scope_error('林园：' + copy, copy, source) is None
