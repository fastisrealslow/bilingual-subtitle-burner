"""Check that price-change possibilities survive automatic packaging."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_rewrite as titles


ACTUAL = json.loads(
    (Path(__file__).parent / 'fixtures' / 'linyuan_published_price_forecast.json').read_text()
)
ERROR = '原话只说价格可能变化'


def test_actual_approved_copy_is_rejected_in_generation_and_delivery():
    proof = ACTUAL['proof']
    source = ACTUAL['transcript']
    candidate = dict(title=proof['title'], cover_title=proof['cover'],
                     subject=proof['subject'], evidence=proof['evidence'])
    assert ERROR in titles._candidate_error(candidate, source, '林园', (), check_layout=False)
    assert ERROR in titles.error(proof['title'], proof, source)


@pytest.mark.parametrize(('source', 'title', 'cover'), [
    ('旺季股价也许会上涨。', '林园：旺季股价也许会上涨', '旺季股价会上涨'),
    ('房价或下跌。', '林园：房价将下跌', '房价是否下跌？'),
    ('售价会波动吧，但货能供上。', '林园：售价会波动，但也许会缺货', '售价可能波动'),
])
def test_each_surface_requires_the_hedge_on_the_same_movement(source, title, cover):
    assert ERROR in titles.forecast_copy_error(title, cover, source)


@pytest.mark.parametrize(('source', 'title', 'cover'), [
    ('来年股价会下跌。', '林园：来年股价会下跌', '股价会下跌'),
    ('房价可能下跌，油价会上涨。', '林园：油价会上涨', '油价会上涨'),
    ('旺季售价可能波动，但货能供上。', '林园：售价也许会波动', '售价会不会波动？'),
    ('旺季售价可能波动，但货能供上。', '林园：售价可能波动，但货能供上', '货能供上'),
    ('去年价格会上涨，今年价格可能上涨。', '林园：谈去年的价格上涨', '去年价格上涨'),
])
def test_preserved_scope_and_explicit_counterexamples(source, title, cover):
    assert titles.price_forecast_modality_error(title, cover, source) is None
