"""Published Q&A tail regression; no transcript edits or semantic approval."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import source_selection as S
import produce_cn as production


def actual():
    return json.loads((Path(__file__).parent / 'fixtures' /
                       'linyuan_published_acknowledgement_tail.json').read_text())


@pytest.fixture(autouse=True)
def reference_policy(monkeypatch):
    monkeypatch.setattr(S.editorial, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(S.editorial, 'MIN_SECONDS', 20)


def test_published_tail_stops_at_exact_previous_raw_sentence():
    case = actual(); cues = case['cues']; original = json.dumps(cues)
    assert '确认语' in S.boundary_error(cues, dict(start=0, end=len(cues)-1))
    picks = S.select(cues, whole_source=True, limit=None)
    assert [(p['start'], p['end']) for p in picks] == [(0, case['expected_end'])]
    assert cues[picks[0]['end']]['end'] == pytest.approx(41.24)
    assert cues[picks[0]['end']]['text'] == '考虑。'
    assert S.boundary_error(cues, picks[0]) is None
    assert json.dumps(cues) == original
    assert 'passed' not in picks[0]


@pytest.mark.parametrize('tail', ['好，了解了。', '嗯，知道了！',
                                   '好的，明白了。', '对，清楚了。'])
def test_pure_comprehension_variants(tail):
    cues = actual()['cues']; cues[-1]['text'] = tail
    assert '确认语' in S.boundary_error(cues, dict(start=0, end=len(cues)-1))
    assert S.select(cues, whole_source=True)[0]['end'] == len(cues)-2


@pytest.mark.parametrize('tail', ['我终于明白了。', '好的企业要看清楚了再投资。',
                                   '他说：“好的，明白了。”', '明白了风险还要控制仓位。',
                                   '了解了企业才能做出投资决定。', '好。'])
def test_substantive_claims_quotes_and_bare_particles_not_guessed_as_host(tail):
    cues = actual()['cues']; cues[-1]['text'] = tail
    assert S.acknowledgement_tail_start(S.sentence_units(cues)) is None


def test_acknowledgement_followed_by_complete_question_answer_is_interior():
    cues = actual()['cues'] + [dict(start=44, end=49, text='您对企业价格怎么看？'),
        dict(start=49, end=75, text='企业价格和需求都要看，我们只在价格合适的时候投资，不能只看过去。')]
    assert S.acknowledgement_tail_start(S.sentence_units(cues)) is None
    assert S.boundary_error(cues, dict(start=0, end=len(cues)-1)) is None


def test_confirmation_alone_is_not_a_complete_answer():
    cues = [dict(start=0, end=5, text='您明白了吗？'),
            dict(start=5, end=7, text='好的，明白了。')]
    assert S.acknowledgement_tail_start(S.sentence_units(cues)) is None


def test_separate_confirmation_sentences_trim_together_without_guessing_cue_time():
    cues = actual()['cues']; cues[-1]['text'] = '好的。'
    cues.append(dict(start=44, end=45, text='明白了。'))
    assert S.select(cues, whole_source=True)[0]['end'] == len(cues)-3
    # A raw cue containing both the conclusion and acknowledgement cannot be
    # shortened by interpolating a timestamp from punctuation.
    cues = actual()['cues']; cues[-2]['text'] += cues[-1]['text']; cues.pop()
    assert S.acknowledgement_tail_start(S.sentence_units(cues)) is None


def test_confirmation_must_not_pad_a_short_answer_to_duration_floor(monkeypatch):
    monkeypatch.setattr(S.editorial, 'MIN_SECONDS', 43)
    assert S.select(actual()['cues'], whole_source=True) == []


def test_final_argument_review_rejects_model_approved_old_tail(tmp_path, monkeypatch):
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY', 'true')
    cues = actual()['cues']; pick = dict(start=0, end=len(cues)-1)
    with patch.object(production, 'llm', side_effect=AssertionError('structural rejection first')):
        with pytest.raises(production.VisualQualityError, match='确认语'):
            production.review_complete_argument(cues, [pick], '林园', '', tmp_path, '')


def test_same_identity_cache_rechecks_tail_instead_of_trusting_old_pick(tmp_path, monkeypatch):
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST', 'true')
    cues = actual()['cues']
    identity = dict(editorial=S.editorial.plan_identity(cues, production.TARGET_SEC),
                    selector_version=S.VERSION, source_first=True)
    cache = tmp_path / 'highlights.json'
    cache.write_text(json.dumps(dict(identity=identity,
        picks=[dict(start=0, end=len(cues)-1)])))
    with patch.object(S, 'select', return_value=[]) as regenerate:
        assert production.pick_highlights(cues, '林园', '', tmp_path) == []
        regenerate.assert_called_once()
    assert json.loads(cache.read_text())['picks'] == []


def test_model_candidate_cannot_reintroduce_acknowledgement(tmp_path, monkeypatch):
    monkeypatch.delenv('SOURCE_EDITORIAL_FIRST', raising=False)
    cues = actual()['cues']
    bad = json.dumps(dict(picks=[dict(start=0, end=len(cues)-1, score=9, reason='complete')]))
    with patch.object(production, 'llm', return_value=bad), \
         patch.object(production, 'pick_argument_context', return_value=[]):
        assert production.pick_highlights(cues, '林园', '', tmp_path, allow_empty=True) == []
