import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import source_selection as S
import editorial_policy as E


def actual_cues():
    return json.loads((Path(__file__).parent / 'fixtures/linyuan_floor_request_actual.json').read_text())['cues']


def test_actual_published_tail_is_rejected_and_answer_alone_is_retained():
    cues = actual_cues()
    assert '下一问' in S.boundary_error(cues, dict(start=0, end=len(cues)-1))
    assert S.boundary_error(cues, dict(start=0, end=len(cues)-2)) is None


@pytest.mark.parametrize('tail', [
    '嗯，呃，这边朋友还是给我一个机会吧。',
    '那边的观众，请给我一次机会。',
    '前排朋友能不能再给我一个提问机会？',
    '后排的朋友，麻烦给我一次发言机会吧。',
    '请再给我一个提问机会。',
    '可不可以给我一次发言机会？',
])
def test_floor_request_variants(tail):
    cues = [dict(start=0, end=30, text='投资要有纪律，我只做自己能看懂的公司。'),
            dict(start=30, end=35, text=tail)]
    assert S.host_bridge(tail)
    assert S.boundary_error(cues, dict(start=0, end=1))


@pytest.mark.parametrize('text', [
    '市场给我一个机会，我就买入。',
    '请给我一个机会。',
    '我当年对他说，请再给我一个提问机会。',
    '这边朋友给我介绍了一个投资机会。',
    '现场的朋友还是给我一个机会吧，我想参与这家企业的经营。',
    '年轻人要珍惜每一次发言机会。',
    '为什么我不去搞？',
])
def test_business_examples_and_rhetorical_questions_are_not_floor_requests(text):
    assert not S.FLOOR_REQUEST.search(text)


def test_selector_ends_before_actual_request(monkeypatch):
    monkeypatch.setattr(E, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(E, 'MIN_SECONDS', 20)
    cues = actual_cues()
    picks = S.select(cues, limit=100, whole_source=True)
    assert picks
    assert any(p['end'] == len(cues)-2 for p in picks)
    assert all(p['end'] < len(cues)-1 for p in picks)


def test_complete_following_question_and_answer_still_valid():
    cues = [dict(start=0, end=30, text='投资要有纪律，我只做自己能看懂的公司。'),
            dict(start=30, end=33, text='请给我一次提问机会。'),
            dict(start=33, end=36, text='您怎么研究一家企业？'),
            dict(start=36, end=60, text='我先研究它的产品有没有市场，再看利润。')]
    assert S.boundary_error(cues, dict(start=0, end=3)) is None
