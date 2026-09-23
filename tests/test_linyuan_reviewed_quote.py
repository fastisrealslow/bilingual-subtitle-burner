"""An explicitly curated literal quote should survive a model's summary style."""
import json
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import title_rewrite as T
import curated_editorial as curated


def fixture():
    data = json.loads((ROOT / 'tests/fixtures/linyuan_source42_documentary.json').read_text())
    units = [c['text'] for c in data['cues'] if c['start'] >= 119.51 and c['end'] <= 149.61]
    return data, units


def reader(units, calls, end=None):
    def model(prompt, schema):
        calls.append(schema)
        assert 'c_guest_spans' in schema['properties'], 'Do not rewrite the reviewed original into an abstract slogan'
        return json.dumps(dict(a_guest_answer='本人认为垄断公司有定价权，希望投资它们赚钱。',
            b_question_premise='无主持人提问',
            c_guest_spans=[dict(a_start=0, b_end=len(units)-1 if end is None else end)]), ensure_ascii=False)
    return model


def test_actual_source42_keeps_exact_quote_after_reader_and_current_gates(monkeypatch):
    data, units = fixture(); calls = []
    monkeypatch.setattr(curated.editorial, 'MIN_SECONDS', 20.)
    pick = curated.source_ranges(data['cues'], data['source_sha256'])[0][2][0]
    assert pick['editorial_prefer_exact_quote'] is True
    result = T.generate(''.join(units), source_cues=units, preferred=pick['editorial_title'],
        structured_model=reader(units, calls), prefer_reviewed_quote=True)
    assert len(calls) == 1
    assert result['title'] == pick['editorial_title']
    assert result['cover_title'] == '垄断了好，我有定价权，我说了算'
    proof = result['title_rewrite']
    assert proof['review']['attribution'] == 'reader_guest_passage'
    assert T.error(result['title'], proof, ''.join(units)) is None
    assert T.error(result['title'], proof, '这里没有这句原话。')


@pytest.mark.parametrize('mode', ['nonliteral', 'host_quote', 'duplicate'])
def test_curated_label_never_overrides_source_attribution_or_duplicates(mode):
    _, units = fixture(); calls = []
    title = '林园：垄断了好，我有定价权，我说了算'
    if mode == 'nonliteral': title = '林园：定价权才是真本事'
    with pytest.raises(ValueError):
        T.generate(''.join(units), source_cues=units, preferred=title,
            existing_titles=[title] if mode == 'duplicate' else [],
            structured_model=reader(units, calls, end=len(units)-2 if mode == 'host_quote' else None),
            prefer_reviewed_quote=True)


def test_no_reader_or_no_exact_quote_cannot_skip_attribution():
    _, units = fixture()
    for kwargs in ({}, {'preferred':'林园：垄断了好，我有定价权，我说了算'},
                   {'structured_model':reader(units, [])}):
        with pytest.raises(ValueError, match='嘉宾归属'):
            T.generate(''.join(units), prefer_reviewed_quote=True, **kwargs)
