"""Priority is not exclusivity: published proof, variants and normal contrasts."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_rewrite as T


def published():
    return json.loads((Path(__file__).parent / 'fixtures' /
                       'linyuan_published_priority_scope.json').read_text())


def test_real_publication_keeps_original_proof_and_cannot_reuse_approval():
    row = published()
    assert hashlib.sha256(row['raw_text'].encode()).hexdigest() == row['provenance']['raw_text_sha256']
    assert row['title_rewrite']['review']['preserves_qualifiers'] is True
    assert '优先范围' in T.error(row['title'], row['title_rewrite'], row['raw_text'])
    item = dict(title=row['title'], cover_title=row['cover'], subject='行业',
                evidence=row['title_rewrite']['evidence'])
    assert '优先范围' in T._candidate_error(item, row['raw_text'], '林园', (), check_layout=False)


@pytest.mark.parametrize('priority', ['首先看', '主要看', '优先看', '首先是看的', '主要是我们关注'])
@pytest.mark.parametrize('exclusive', ['只看', '仅看', '仅仅关注', '只盯着', '只会考虑'])
def test_same_object_priority_is_not_exclusive(priority, exclusive):
    source = f'我们{priority}现金流，还要考虑其他情况。'
    assert T.priority_scope_error(f'林园：{exclusive}现金流', '关注现金流', source)


@pytest.mark.parametrize('title,cover', [
    ('林园：先看行业发展', '只看行业发展'),
    ('林园：只盯行业发展', '先看行业发展'),
    ('林园：为什么只看行业发展？', '先看行业发展'),
    ('林园：只要看行业发展就够了', '先看行业发展'),
])
def test_title_and_cover_each_keep_scope_including_question_presupposition(title, cover):
    assert T.priority_scope_error(title, cover, '首先看行业发展，还考虑估值。')


@pytest.mark.parametrize('source', [
    '首先看行业发展。您是不是只看行业发展？',
    '首先看行业发展。我们不只看行业发展。',
    '首先看行业发展。不是只看行业发展。',
    '首先看行业发展。只看行业发展是不够的。',
    '首先看行业发展。只看价格。',
])
def test_question_negation_or_other_object_cannot_supply_exclusivity(source):
    assert T.priority_scope_error('林园：只看行业发展', '先看行业发展', source)


@pytest.mark.parametrize('title,cover,source', [
    ('林园：先看行业发展', '主要看行业发展', '首先看行业的发展，还要看估值。'),
    ('林园：不只看行业发展', '不能只看行业发展', '首先看行业发展。'),
    ('林园：只看行业发展', '只看行业发展', '我们首先看行业发展。这种情况下我们只看行业发展。'),
    ('林园：只看价格', '只看价格', '我们首先看行业发展。这里只看价格。'),
    ('林园：只看行业发展', '只看行业发展', '您首先看行业发展吗？我们只看行业发展。'),
    ('林园：只能看行业发展', '先看行业发展', '我们首先看行业发展。'),
    ('林园：行业发展是第一位的', '行业发展重要', '我们首先看行业发展。'),
])
def test_normal_copy_and_out_of_scope_phrasings_are_not_declared_failures(title, cover, source):
    assert T.priority_scope_error(title, cover, source) is None


def test_quote_fallback_uses_full_source_instead_of_narrow_evidence():
    source = '我们首先看行业发展。只看行业发展的观点不对。'
    quote = '只看行业发展'
    proof = T._package(dict(title='林园：' + quote, cover_title=quote,
                            subject='行业', evidence=[quote]),
                       source, dict(method='source_quote', quote=quote), [])['title_rewrite']
    assert '优先范围' in T.error('林园：' + quote, proof, source)


def test_literal_fallback_cannot_extract_exclusivity_from_rejected_view(monkeypatch):
    import headline_policy as H
    source = '我们首先看行业发展。只看行业发展的观点不对。'
    monkeypatch.setattr(H, 'title_candidates', lambda *a, **k: ['林园：只看行业发展'])
    monkeypatch.setattr(H, 'cover_copy', lambda *a, **k: dict(text='只看行业发展', kind='quote'))
    with pytest.raises(ValueError, match='未提炼出'):
        T._extractive(source, '林园', ())
