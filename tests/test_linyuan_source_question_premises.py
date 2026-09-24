import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import editorial_policy as E
import source_selection as S


def test_actual_source311_keeps_premise_and_splits_next_complete_question(monkeypatch):
    monkeypatch.setattr(E, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(E, 'MIN_SECONDS', 20)
    data = json.loads((Path(__file__).parent/'fixtures/linyuan_source311_question_premises.json').read_text())
    cues = data['cues']
    picks = S.select(cues, limit=100, whole_source=True)
    spans = [(cues[p['start']]['start'], cues[p['end']]['end']) for p in picks]
    assert (566.68, 611.72) in spans
    assert (612.04, 716.28) in spans
    assert not any(a == 577.8 or a == 623.72 for a, b in spans)
    start = next(i for i, c in enumerate(cues) if c['start'] == 577.8)
    end = next(i for i, c in enumerate(cues) if c['end'] == 716.28)
    assert '提问前提' in S.boundary_error(cues, dict(start=start, end=end))


def test_backreference_without_explicit_host_premise_is_not_a_new_question():
    cues = [dict(start=0, end=8, text='我买股票先看企业赚不赚钱。'),
            dict(start=8, end=16, text='这一点是怎么做到的？我自己会看财报。')]
    units = S.sentence_units(cues)
    assert S.host_premise_start(units, cues, 1) is None


def test_premise_lookup_stops_at_another_question_or_thirty_seconds():
    for middle, next_time in [('您买了吗？', 20), ('这是另一段原话。', 41)]:
        cues = [dict(start=0, end=5, text='我记得您说过一句话，牛熊和赚钱没有关系。'),
                dict(start=6, end=15, text=middle),
                dict(start=next_time, end=next_time+5, text='这一点是怎么做到的呢？')]
        assert S.host_premise_start(S.sentence_units(cues), cues, 2) is None
