"""Paint and subtitle colors must agree without moving verified source pixels."""
import json
from pathlib import Path
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import presentation as V
import produce_cn as P
import editorial_cover as C


def test_black_card_caption_contrast_and_geometry(tmp_path, monkeypatch):
    try:
        font = C.font_path()
    except ValueError:
        pytest.skip('Chinese font required')
    monkeypatch.setenv('AUDIO_CARD_FONT_FILE', str(font))
    reference = tmp_path / 'portrait.png'
    Image.new('RGB', (640, 480), '#b98768').save(reference)
    for theme in ('light', 'contrast'):
        image = tmp_path / (theme + '.png')
        P.make_audio_card(image, '林园', '人均收入不减少，十二三年回本',
                         portrait_path=reference, require_portrait=True,
                         live_video=True, live_theme=theme)
        proof = json.loads(Path(str(image) + '.title-proof.json').read_text())
        assert ''.join(proof['headline_lines']) == '人均收入不减少十二三年回本'
        assert all(b[2] <= 682 and b[3] <= 325 for b in proof['text_boxes'])
        layout = V.live_card_layout(theme)
        assert layout['subtitle_region'] == V.layout_for(720, 1280, True)['subtitle_region']
        ass = tmp_path / (theme + '.ass')
        V.write_ass([dict(start_sec=0, end_sec=3, zh='人均收入比今天不减少')], ass, layout, 'Noto Sans CJK SC')
        style = next(line for line in ass.read_text(encoding='utf-8-sig').splitlines() if line.startswith('Style:'))
        pixel = Image.open(image).getpixel((100, 950))
        if theme == 'contrast':
            assert max(pixel) < 20 and '&H00FFFFFF' in style and '&H00000000' in style
        else:
            assert min(pixel) > 240 and '&H00422C18' in style
    with pytest.raises(ValueError):
        V.live_card_layout('unknown')


def test_encoded_card_checks_source_borders_independently_of_template(tmp_path):
    import cv2
    import numpy as np
    layout = V.live_card_layout('contrast')
    for background, dirty in [(0, False), (238, True)]:
        frame = np.full((1280, 720, 3), background, dtype=np.uint8)
        frame[360:830, 44:676] = (95, 140, 180)
        if dirty:
            frame[360:830, 44:80] = 0
        path = tmp_path / ('bad.mp4' if dirty else 'good.mp4')
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 12, (720, 1280))
        assert writer.isOpened()
        for _ in range(12):
            writer.write(frame)
        writer.release()
        if dirty:
            with pytest.raises(ValueError, match='黑色'):
                V.verify_render(path, layout)
        else:
            result = V.verify_render(path, layout)
            assert result['render_checks']['black_edge_hits'] == 0
            assert result['render_checks']['black_edge_scope'] == 'source_window'
