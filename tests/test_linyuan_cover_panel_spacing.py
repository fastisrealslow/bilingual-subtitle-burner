"""Published titles must not paint over the photograph outside its face box."""
import json
from pathlib import Path
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import editorial_cover as C
from fc import index as FC


CASES = json.loads((Path(__file__).parent / 'fixtures' /
                   'linyuan_actual_cover_panel_overlap.json').read_text())


@pytest.mark.parametrize('case', CASES)
def test_actual_receipts_reproduce_panel_collision(case):
    proof = case['proof']
    assert proof['text_face_overlap'] is False
    assert max(b[2] for b in proof['text_boxes']) - proof['portrait_panel'][0] == 16


@pytest.mark.parametrize('headline', [c['cover'] for c in CASES] + [
    '不怕行业波动，继续研究企业', '老公司有赚头，新公司需持续投入',
])
def test_collision_is_rejected_without_rewriting_copy(tmp_path, headline):
    try:
        font = C.font_path()
    except ValueError:
        pytest.skip('Chinese font required')
    with pytest.raises(ValueError, match='安全区'):
        C.render(Image.new('RGB', (720, 1280), '#b98768'),
                 tmp_path / 'cover.jpg', [504, 416, 105, 143],
                 headline, '林园', font)
    assert not (tmp_path / 'cover.jpg').exists()


@pytest.mark.parametrize('headline', ['长期持有', '看懂公司，再做决定',
                                     '别只看股价，要看现金流'])
def test_renderer_preserves_copy_type_size_and_photo_gap(tmp_path, headline):
    try:
        font = C.font_path()
    except ValueError:
        pytest.skip('Chinese font required')
    proof = C.render(Image.new('RGB', (720, 1280), '#b98768'),
                     tmp_path / 'cover.jpg', [504, 416, 105, 143],
                     headline, '林园', font)
    assert ''.join(proof['headline_lines']) == headline.replace('，', '')
    assert proof['font_px'] == 112
    assert proof['portrait_panel'] == [832, 68, 1152, 652]
    assert all(b[2] <= proof['portrait_panel'][0] - 24 for b in proof['text_boxes'])
    assert proof['text_panel_gap'] >= 24
    assert FC.cover_quality_error(proof) is None
    bad = {**proof, 'text_boxes': [[*b[:2], 848, b[3]] for b in proof['text_boxes']]}
    assert FC.cover_quality_error(bad)
    assert FC.cover_quality_error({**proof, 'text_panel_gap': 0})


def test_unfittable_copy_is_rejected_without_truncating_or_small_type(tmp_path):
    try:
        font = C.font_path()
    except ValueError:
        pytest.skip('Chinese font required')
    with pytest.raises(ValueError, match='安全区|三行'):
        C.render(Image.new('RGB', (720, 1280)), tmp_path / 'cover.jpg',
                 [504, 416, 105, 143], '人均收入不减少十二三年投资回本', '林园', font)
    assert not (tmp_path / 'cover.jpg').exists()
