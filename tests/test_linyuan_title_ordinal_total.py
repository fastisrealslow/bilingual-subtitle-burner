import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_quantity_context as Q
import title_rewrite as T


def test_actual_approved_proof_rejected_at_both_entry_points():
    data = json.loads((Path(__file__).parent / 'fixtures/title_ordinal_total_actual.json').read_text())
    proof, source = data['proof'], data['source']
    assert data['error'] is None  # Recorded unmodified baseline result.
    assert '序数' in T.error(proof['title'], proof, source)
    item = dict(title=proof['title'], cover_title=proof['cover'],
                subject=proof['subject'], evidence=proof['evidence'])
    assert '序数' in T._candidate_error(item, source, '林园', (), check_layout=False)


@pytest.mark.parametrize('ordinal,total', [('四','四'), ('4','四'), ('十二','12'), ('二','两')])
@pytest.mark.parametrize('in_cover', [False, True])
def test_ordinal_reference_does_not_supply_a_total(ordinal, total, in_cover):
    source = f'这可能是您的第{ordinal}次机会。我的投资经历给我很多启发。'
    claim = f'{total}次机会让我积累经验'
    args = ('投资经历让我积累经验', claim) if in_cover else (claim, '投资经历让我积累经验')
    assert Q.ordinal_total_error(*args, source, ['我的投资经历给我很多启发。'])


@pytest.mark.parametrize('copy,source,evidence', [
    ('四次投资让我积累经验', '这是第四次投资。我做过4次投资。', ['我做过4次投资。']),
    ('第四次投资让我积累经验', '这是第四次投资。', ['这是第四次投资。']),
    ('投资经历让我积累经验', '这是第四次投资。', ['这是第四次投资。']),
    ('四次投资让我积累经验', '我做过四次投资。', ['我做过四次投资。']),
    ('两次投资让我积累经验', '第二次投资之后，我做过二次投资。', ['我做过二次投资。']),
    ('二次投资让我积累经验', '这是第十二次投资。', ['这是第十二次投资。']),
])
def test_supported_total_ordinal_and_unrelated_number(copy, source, evidence):
    assert Q.ordinal_total_error(copy, copy, source, evidence) is None


def test_ordinal_in_selected_evidence_is_still_not_total():
    assert Q.ordinal_total_error('四次创业让我成长', '', '这是第四次创业。', ['这是第四次创业。'])
    assert Q.ordinal_total_error('四次创业让我成长', '', '这是第十四次创业。', ['这是第十四次创业。']) is None


def test_full_source_count_cannot_replace_selected_guest_evidence():
    source = '您有四次投资，这是第四次吗？我只讲前两次。'
    assert Q.ordinal_total_error('四次投资让我成长', '', source, ['我只讲前两次。'])


def test_same_actual_source_without_invented_total_keeps_existing_review():
    data = json.loads((Path(__file__).parent / 'fixtures/title_ordinal_total_actual.json').read_text())
    proof = data['proof']
    item = dict(title=proof['title'].replace('四次', '几次'), cover_title=proof['cover'],
                subject=proof['subject'], evidence=proof['evidence'])
    assert T._candidate_error(item, data['source'], '林园', (), check_layout=False) is None
    package = T._package(item, data['source'], proof['review'], [])
    assert T.error(item['title'], package['title_rewrite'], data['source']) is None
