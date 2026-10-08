"""A personal action cannot be reassigned to a country or institution."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import title_rewrite as T


def test_real_published_title_is_rejected_despite_positive_model_flags():
    case = json.loads((ROOT / 'tests/fixtures/linyuan_published_actor.json').read_text())
    proof = case['proof']
    assert all(proof['review'][key] is True for key in T.CHECKS)
    assert '主体' in T.error(case['title'], proof, case['transcript'])
    candidate = dict(title=case['title'], cover_title=proof['cover'],
                     subject=proof['subject'], evidence=proof['evidence'])
    assert '主体' in T._candidate_error(candidate, case['transcript'], '林园', (), False)


@pytest.mark.parametrize('actor', ['中国', '日本', '这家企业', '银行', '基金'])
@pytest.mark.parametrize('verb,obj', [('买','黄金'), ('持有','股票'), ('参与','这个项目')])
def test_other_topics_actors_and_actions(actor, verb, obj):
    source = f'{actor}不需要{obj}。我没{verb}{obj}。'
    bad = f'{actor}不{verb}{obj}'
    personal = f'我没{verb}{obj}'
    assert T.negative_actor_error('林园：'+bad, personal, source)
    assert T.negative_actor_error('林园：'+personal, bad, source)
    assert not T.negative_actor_error('林园：'+personal, personal, source)
    assert not T.negative_actor_error('林园：'+bad, bad,
                                     source+f'{actor}现在也不{verb}{obj}。')


@pytest.mark.parametrize('title,cover,source', [
    ('林园：黄金不买','黄金不买','我没买黄金。'),
    ('林园：没买黄金','林园没买黄金','我没买黄金。'),
    ('林园：中国不买黄金','中国不买黄金','中国黄金都不买。我没买股票。'),
    ('林园：我没买黄金','我没买黄金','中国也不需要黄金。我没买黄金。'),
    ('林园：公司不买设备','公司不买设备','我们公司目前不买设备。我没买车。'),
])
def test_real_support_and_topic_fronted_objects(title, cover, source):
    assert not T.negative_actor_error(title, cover, source)


@pytest.mark.parametrize('source', [
    '中国的黄金我没买。',
    '中国为什么不买黄金？我没买黄金。',
    '中国是否不买黄金？我没买黄金。',
])
def test_nearby_first_person_or_question_is_not_named_actor_evidence(source):
    assert T.negative_actor_error('林园：中国不买黄金','中国不买黄金',source)
