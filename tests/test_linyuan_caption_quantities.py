"""Chinese dates and quantities keep the same source characters and clock."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import caption_boundary_quality as B
import caption_lines as C
import presentation as V
from caption_quantities import quantity_spans


def case():
    return json.loads((Path(__file__).parent / 'fixtures' /
                       'linyuan_chinese_quantity_boundaries.json').read_text())


def test_actual_published_year_is_whole_without_changing_source_or_clock():
    actual = case()
    entries = actual['raw_entries']
    original = json.dumps(entries, ensure_ascii=False)
    text = ''.join(e['zh'] for e in entries)
    assert hashlib.sha256(text.encode()).hexdigest() == actual['evidence']['raw_text_sha256']
    layout = V.live_card_layout()
    screens = C.one_line_screens(entries, layout)
    assert sum('二零二五年' in r['zh'] for r in screens) == 1
    assert ''.join(r['zh'] for r in screens) == text
    assert [a for r in screens for a in r['caption_chars']] == [
        a for r in entries for a in C.character_anchors(r)]
    assert all(len(r['zh']) <= layout['line_capacity'] for r in screens)
    assert json.dumps(entries, ensure_ascii=False) == original
    assert B.metrics(screens, entries)['entity_splits'] == 0
    # The final ASS writer also rechecks historic semantic-group caches.
    assert [r['zh'] for r in C.one_line_screens(screens, layout)] == [r['zh'] for r in screens]


def test_independent_checker_sees_actual_bad_year_boundary():
    entries = case()['raw_entries']
    atoms = [a for e in entries for a in C.character_anchors(e)]
    text = ''.join(a[0] for a in atoms)
    split = text.index('五年')
    bad = [C.caption_screen(atoms[:split], {}, V.live_card_layout()),
           C.caption_screen(atoms[split:], {}, V.live_card_layout())]
    assert B.metrics(bad, entries)['entity_splits'] == 1
    assert B.boundary_events(bad, entries)['entity_splits']


@pytest.mark.parametrize('quantity', [
    '二零二五年', '二〇二五年', '十五年', '三千万元', '三十亿元',
    '两点五倍', '三十个百分点', '二零二五年三月十二日',
    '2025年', '3.5亿元', '二十分钟',
])
def test_number_unit_variants_cannot_be_split_even_by_old_semantic_flags(quantity):
    protected = quantity
    prefix = '那么林总您多次提到'
    text = prefix + quantity + '市场会有风险。'
    # Uniform anchors are synthetic variants, never passed off as real timing.
    atoms = [(c, i*.16, (i+1)*.16) for i, c in enumerate(text)]
    cut = len(prefix) + 2
    entries = [dict(zh=''.join(a[0] for a in part), start_sec=part[0][1],
        end_sec=part[-1][2], caption_chars=part, semantic_group=True)
        for part in [atoms[:cut], atoms[cut:]]]
    screens = C.one_line_screens(entries, V.live_card_layout())
    assert sum(protected in s['zh'] for s in screens) == 1
    assert [a for s in screens for a in s['caption_chars']] == atoms
    assert B.metrics(screens, entries)['entity_splits'] == 0


@pytest.mark.parametrize('text', ['一方面看企业需求', '万一市场下跌', '两家公司各有特点',
                                '第一条原则是避免风险', '长期投资需要耐心'])
def test_ordinary_words_and_ordinals_are_not_quantity_spans(text):
    assert quantity_spans(text) == []


def test_caption_protection_does_not_change_title_dictionary():
    text = '那么林总您多次提到二零二五年'
    before = V.word_spans(text)
    assert any(text[a:b] == '二零二五年' for a, b in C.caption_word_spans(text))
    assert V.word_spans(text) == before
