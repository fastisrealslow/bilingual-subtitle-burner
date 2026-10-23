"""Closing invitations must retain an answer, including declarative requests."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import source_selection as s
import produce_cn as p


@pytest.fixture(autouse=True)
def reference_policy(monkeypatch):
    monkeypatch.setattr(s.editorial, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(s.editorial, 'MIN_SECONDS', 20)


def real_case():
    return json.loads((Path(__file__).parent / 'fixtures/linyuan_closing_summary_invitation.json').read_text())


def test_actual_published_request_is_not_the_previous_answer_ending():
    case = real_case()
    cues = case['cues']
    before = json.dumps(cues, ensure_ascii=False)
    assert s.boundary_error(cues, dict(start=0, end=len(cues)-1))
    picks = s.select(cues, whole_source=True, limit=None)
    assert [(x['start'], x['end']) for x in picks] == [(0, 33)]
    assert cues[picks[0]['end']]['end'] == pytest.approx(91.2)
    assert all(s.boundary_error(cues, pick) is None for pick in picks)
    assert json.dumps(cues, ensure_ascii=False) == before


@pytest.mark.parametrize('invitation', [
    '最后，请您用一句话总结一下自己的投资经历。',
    '在节目的最后，能不能请你回顾一下这些年的投资生涯。',
    '访谈结束前，请您概括一下您的从业感受。',
    '啊，那呃，如果说在，栏目的最后用一句或者是几句话总结一下您的一个，从业历程或者说您的一个心路历程。',
    '那么最后，您能否总结一下自己的经验？',
])
def test_declarative_and_question_requests_split_on_original_cues(invitation):
    assert s.closing_summary_request(invitation)
    assert s.question_unit(invitation)
    cues = [dict(start=0, end=4, text='您对企业投资有什么建议？'),
            dict(start=4, end=30, text='我投资企业先看需求，研究清楚才投入资金。'),
            dict(start=30, end=40, text=invitation)]
    assert s.boundary_error(cues, dict(start=0, end=2))
    picks = s.select(cues, whole_source=True, limit=None)
    assert [(x['start'], x['end']) for x in picks] == [(0, 1)]
    # A new summary Q&A can stand alone when its real answer follows.
    summary = [dict(start=0, end=10, text=invitation),
               dict(start=10, end=40, text='我投资企业先看需求，研究清楚才投入资金。')]
    assert s.boundary_error(summary, dict(start=0, end=1)) is None
    assert s.select(summary, whole_source=True)
    assert s.boundary_error(summary[:1], dict(start=0, end=0))


@pytest.mark.parametrize('text', [
    '最后我总结一下自己的投资经历。',
    '最后，我回顾一下这些年的投资生涯。',
    '比如主持人说最后请您总结一下投资经历，我会回答先看需求。',
    '假如最后主持人请您概括一下感受，应该先想清楚。',
    '如果有人最后请您总结一下投资经历，也不能夸大收益。',
    '他说“最后，请您总结一下投资经历”，我没有回答。',
    '最后我总结一下您刚才讲的企业需求。',
    '请您总结一下这家公司的财务情况。',
    '最后一个例子可以概括一下需求增长的原因。',
])
def test_guest_recaps_quotes_hypotheticals_and_ordinary_questions_are_not_closing_requests(text):
    assert not s.closing_summary_request(text)


def test_request_split_across_display_cues_keeps_real_boundary():
    invitation = '在节目的最后，请您用一句话总结一下投资经历。'
    cues = [dict(start=0, end=4, text='您对企业投资有什么建议？'),
            dict(start=4, end=30, text='我投资企业先看需求，研究清楚才投入资金。'),
            dict(start=30, end=35, text=invitation[:12]),
            dict(start=35, end=40, text=invitation[12:])]
    assert [(x['start'], x['end']) for x in s.select(cues, whole_source=True)] == [(0, 1)]
    assert s.boundary_error(cues, dict(start=0, end=3))


def test_shared_answer_and_invitation_cue_is_excluded_without_guessing_time():
    cues = [dict(start=0, end=4, text='您对企业投资有什么建议？'),
            dict(start=4, end=30, text='我投资企业先看需求，研究清楚才投入资金。'),
            dict(start=30, end=40, text='研究清楚以后才会有信心。最后，请您总结一下自己的投资经历。')]
    assert s.boundary_error(cues, dict(start=0, end=2))
    picks = s.select(cues, whole_source=True, limit=None)
    assert [(x['start'], x['end']) for x in picks] == [(0, 1)]


@pytest.mark.parametrize('current_identity', [False, True])
def test_cached_bad_range_is_rechecked(tmp_path, monkeypatch, current_identity):
    cues = real_case()['cues']
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST', 'true')
    identity = dict(editorial=p.editorial.plan_identity(cues, p.TARGET_SEC),
                    selector_version=s.VERSION if current_identity else 42, source_first=True)
    (tmp_path/'highlights.json').write_text(json.dumps(dict(identity=identity,
        picks=[dict(start=0, end=len(cues)-1, score=9)])))
    with patch.object(p, 'llm', side_effect=AssertionError('Source path needs no model')):
        picks = p.pick_highlights(cues, '林园', '', tmp_path, allow_empty=True)
    assert picks and all(x['end'] == 33 for x in picks)


def test_model_and_final_review_cannot_reintroduce_the_request(tmp_path, monkeypatch):
    cues = real_case()['cues']
    bad = dict(start=0, end=len(cues)-1, score=9)
    monkeypatch.delenv('SOURCE_EDITORIAL_FIRST', raising=False)
    with patch.object(p, 'llm', side_effect=[json.dumps(dict(picks=[bad])), '{"picks":[]}']), \
            patch.object(p, 'pick_argument_context', return_value=[]):
        assert p.pick_highlights(cues, '林园', '', tmp_path, allow_empty=True) == []
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY', 'true')
    with patch.object(p, 'automatic_only', return_value=True):
        with pytest.raises(p.VisualQualityError, match='总结邀请'):
            p.review_complete_argument(cues, [bad], '林园', '', tmp_path, '')
