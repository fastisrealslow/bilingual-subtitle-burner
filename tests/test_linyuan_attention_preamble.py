"""A declarative host introduction is not a completed guest answer."""
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import produce_cn as P
import source_selection as S


def actual():
    return json.loads((Path(__file__).parent / 'fixtures' /
                       'linyuan_published_attention_preamble.json').read_text())


def short_policy(monkeypatch):
    monkeypatch.setattr(S.editorial, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(S.editorial, 'MIN_SECONDS', 20)


def test_real_published_preamble_is_not_an_ending_or_a_duration_substitute(monkeypatch):
    short_policy(monkeypatch)
    case = actual()
    cues = case['cues']
    before = json.dumps(cues, ensure_ascii=False)
    assert hashlib.sha256(''.join(c['text'] for c in cues).encode()).hexdigest() == case['provenance']['raw_sha256']
    assert case['editorial_review']['natural_ending'] is True
    assert '下一问' in S.boundary_error(cues, dict(start=0, end=len(cues)-1))
    picks = S.select(cues, whole_source=True, limit=None)
    assert [(p['start'], p['end']) for p in picks] == [(0, 6)]
    assert cues[picks[0]['end']]['end'] == case['cues'][6]['end']
    assert S.boundary_error(cues, picks[0]) is None
    # Keeping an answer below the existing floor must still produce no clip.
    monkeypatch.setattr(S.editorial, 'MIN_SECONDS', 23)
    assert S.select(cues, whole_source=True, limit=None) == []
    assert json.dumps(cues, ensure_ascii=False) == before


@pytest.mark.parametrize('preamble', [
    '您的基金净值一直备受投资者关注。',
    '最近这段时间，你的产品业绩也受到大家关注。',
    '那么近三年来说，呃，您所管理的基金回撤引起了很多讨论。',
    '您的投资组合表现目前引发市场热议。',
    '你管理的账户收益受到不少人关心。',
])
def test_class_variants_need_a_following_question_and_answer(preamble):
    cues = [dict(start=0, end=5, text='您对消费行业怎么看？'),
            dict(start=5, end=130, text='我们研究企业长期需求，价格也必须合适。'),
            dict(start=130, end=140, text=preamble)]
    assert '下一问' in S.boundary_error(cues, dict(start=0, end=2))
    assert S.boundary_error(cues, dict(start=2, end=2))
    cues += [dict(start=140, end=145, text='您是怎么控制风险的？'),
             dict(start=145, end=280, text='我会分析企业的需求和买入价格，控制持仓风险。')]
    assert S.boundary_error(cues, dict(start=0, end=4)) is None
    picks = S.select(cues, whole_source=True, limit=None)
    assert [(p['start'], p['end']) for p in picks] == [(0, 1), (2, 4)]


@pytest.mark.parametrize('text', [
    '我的产品净值一直备受投资者关注。',
    '我们的基金业绩受到大家关注。',
    '市场上的产品净值引起投资者关注。',
    '我说：您的基金净值一直备受投资者关注。',
    '有人问，您的产品净值也备受投资者关注。',
    '“您的基金业绩备受关注。”这是主持人的原话。',
    '您的基金净值并未受到投资者关注。',
    '您的产品业绩不受市场关注。',
    '您的产品净值并非备受投资者关注。',
    '您的产品净值也很稳定。',
    '您的基金净值受到市场关注吗？',
    '如果您的产品净值受到关注，也不能因此承诺收益。',
    '比如您的产品业绩受到关注，投资仍然有风险。',
])
def test_guest_accounts_quotes_negation_questions_and_conditions_are_not_preambles(text):
    assert not S.host_attention_preamble(text)


def test_real_preamble_across_raw_cues_and_synthetic_splits():
    case = actual()
    for split in [1, 7, 13, 20]:
        text = case['cues'][-2]['text'] + case['cues'][-1]['text']
        cues = case['cues'][:-2] + [dict(start=22.64, end=26, text=text[:split]),
                                   dict(start=26, end=28.16, text=text[split:])]
        assert S.boundary_error(cues, dict(start=0, end=len(cues)-1))


@pytest.mark.parametrize('cache_version', [42, S.VERSION])
def test_old_and_current_identity_cache_cannot_restore_published_bad_tail(tmp_path, monkeypatch, cache_version):
    short_policy(monkeypatch)
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST', 'true')
    cues = actual()['cues']
    identity = dict(editorial=P.editorial.plan_identity(cues, P.TARGET_SEC),
                    selector_version=cache_version, source_first=True)
    (tmp_path/'highlights.json').write_text(json.dumps(dict(identity=identity,
        picks=[dict(start=0, end=len(cues)-1)])))
    with patch.object(P, 'llm', side_effect=AssertionError('No model approval')):
        picks = P.pick_highlights(cues, '林园', '', tmp_path)
    # The production selector has no source-EOF proof here. It must reject
    # the stale range rather than manufacture a newly approved short ending.
    assert picks == []


def test_model_candidate_cannot_restore_published_bad_tail(tmp_path, monkeypatch):
    short_policy(monkeypatch)
    monkeypatch.delenv('SOURCE_EDITORIAL_FIRST', raising=False)
    cues = actual()['cues']
    bad = json.dumps(dict(picks=[dict(start=0, end=len(cues)-1, score=9, reason='model pass')]))
    with patch.object(P, 'llm', side_effect=[bad, '{"picks":[]}']), \
            patch.object(P, 'pick_argument_context', return_value=[]):
        assert P.pick_highlights(cues, '林园', '', tmp_path, allow_empty=True) == []
