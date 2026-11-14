"""A receipt-bound title promoted a speaker's trial into general efficacy."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_rewrite as T


def actual():
    return json.loads((Path(__file__).parent / 'fixtures' /
                       'linyuan_actual_personal_experience_scope.json').read_text())


def test_actual_approved_proof_cannot_drop_personal_trial_in_either_field():
    f = actual()
    assert hashlib.sha256(f['transcript'].encode()).hexdigest() == f['provenance']['raw_sha256']
    assert all(f['title_rewrite']['review'][key] for key in T.CHECKS)
    assert T.personal_experience_scope_error(f['title'], f['cover'], f['transcript'])
    assert '个人体验' in T.error(f['title'], f['title_rewrite'], f['transcript'])
    scoped = '林园：我试过片仔癀，感觉有安神作用'
    assert T.personal_experience_scope_error(scoped, f['cover'], f['transcript'])
    assert T.personal_experience_scope_error(f['title'], '我感觉片仔癀有安神作用', f['transcript'])


@pytest.mark.parametrize('source,bad', [
    ('我亲自用过，这款耳塞有降噪的作用。', '耳塞能降噪'),
    ('我们已经体验过，这个垫子起到保暖效果。', '垫子有保暖效果'),
    ('本人感觉得到，涂层具有防水作用。', '涂层可防水'),
    ('至少我现在试了的，我周围试了，我自己试了的，它有安神的作用。', '产品还能安神'),
])
def test_other_effects_and_speakers_keep_the_same_scope(source, bad):
    assert T.forecast_copy_error('林园：' + bad, bad, source)
    assert T.personal_experience_scope_error('林园：' + bad + '？', bad + '？', source)
    good = '我自己试过，感觉' + bad
    assert T.personal_experience_scope_error('林园：' + good, good, source) is None


@pytest.mark.parametrize('copy', [
    '我试过片仔癀，感觉有安神作用',
    '我试过：片仔癀有安神作用',
    '据我体验：片仔癀有安神作用',
    '据我体验，片仔癀有安神作用',
    '片仔癀的安神效果是我自己的感受',
    '我觉得片仔癀能安神',
    '对我来说，片仔癀有安神作用',
    '片仔癀我自己试过',
    '我看好中药的长期需求',
    '安神药我没有买过',
])
def test_preserved_experience_and_other_points_are_not_blocked(copy):
    assert T.personal_experience_scope_error('林园：' + copy, copy, actual()['transcript']) is None


@pytest.mark.parametrize('source', [
    '这种耳塞有降噪作用。',
    '我没有试过，这个耳塞有降噪作用。',
    '如果我自己用过，我会说耳塞有降噪作用。',
    '有人说我自己用过，这个耳塞有降噪作用。',
    '我亲自用过，耳塞没有降噪作用。',
    '我试过，它不具有降噪效果。',
    '我试过，没有起到降噪作用。',
    '我试过，未能起到降噪作用。',
    '我试过，但不能说它有降噪作用。',
    '我用过别的耳塞。另外，这款耳塞有降噪作用。',
    '我试过别的产品，另外这个耳塞有降噪作用。',
    '我试过，研究表明耳塞有降噪作用。',
])
def test_no_personal_effect_evidence_does_not_invent_a_qualifier(source):
    assert T.personal_experience_scope_error('林园：耳塞能降噪', '耳塞能降噪', source) is None


def test_an_unrelated_personal_opinion_and_question_mark_do_not_supply_scope():
    source = '我自己试过，产品有保暖作用。'
    for bad in ('我觉得价格低。产品能保暖', '我觉得价格低，产品能保暖',
                '我喜欢这个产品，能保暖', '产品能保暖吗？'):
        assert T.personal_experience_scope_error('林园：' + bad, bad, source)


def test_generation_and_legacy_proof_use_full_source_even_when_evidence_omits_trial():
    f = actual()
    item = dict(title=f['title'], cover_title=f['cover'], subject='片仔癀',
                evidence=['它有安神的作用。'])
    assert '个人体验' in T._candidate_error(item, f['transcript'], '林园', [], check_layout=False)
    proof = T._package(item, f['transcript'], dict(method='cpu_text_review', appeal=4,
        reason='审核六项为真仍须核对完整原文的个人范围', **{key: True for key in T.CHECKS}), [])['title_rewrite']
    assert '个人体验' in T.error(f['title'], proof, f['transcript'])


def test_an_unrelated_personal_action_does_not_create_an_effect_qualifier():
    assert T.personal_experience_scope_error('林园：可转债是一种套利行为',
        '可转债是一种套利行为', '我们没太去关注。那个东西就是一种套利行为。') is None
