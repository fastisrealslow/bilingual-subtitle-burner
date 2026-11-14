import json
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import title_rewrite as T


@pytest.mark.parametrize('source,bad', [
    ('占百分之七十以上', '占百分之七'),
    ('占百分之二十五', '占百分之二十'),
    ('占百分之三点五', '占百分之三'),
    ('占百分之70', '占百分之7'),
    ('占百分之12.5', '占百分之12'),
])
@pytest.mark.parametrize('field', ['title', 'cover'])
def test_percentage_prefix_is_not_the_full_number(source, bad, field):
    title, cover = (bad, source) if field == 'title' else (source, bad)
    assert '截断' in T.quantity_range_error(title, cover, source)


@pytest.mark.parametrize('source,title,cover', [
    ('占百分之七十以上', '占百分之七十以上', '占七成'),
    ('占百分之七', '占百分之七', '占百分之七'),
    ('甲占百分之七十，乙占百分之七', '乙占百分之七', '乙占百分之七'),
    ('占百分之三点五', '占百分之三点五', '比例很低'),
    ('占百分之12.5', '占百分之12.5', '比例很低'),
])
def test_full_tokens_and_independent_values_remain_allowed(source, title, cover):
    assert not T.quantity_range_error(title, cover, source)


def test_related_production_proof_reproduces_public_title_mismatch():
    case = json.loads((ROOT / 'tests/fixtures/linyuan_percentage_prefix.json').read_text())
    assert case['byte_identical_to_published'] is False
    assert all(case['proof']['review'][k] is True for k in T.CHECKS)
    assert '截断' in T.error(case['title'], case['proof'], case['transcript'])
