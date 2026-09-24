"""A portrait canvas must not hide a usable, identity-verified live window."""
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import produce_cn as P
import live_tracking


def test_portrait_requires_source_identity_and_valid_crop(monkeypatch):
    import cv2
    class Capture:
        def __init__(self, _): pass
        def set(self, *_): pass
        def read(self): return True, np.zeros((1920, 882, 3), dtype=np.uint8)
        def release(self): pass
    monkeypatch.setattr(cv2, 'VideoCapture', Capture)
    assert P.audio_card_live_crop(882, 1920) is None
    assert P.audio_card_live_crop(882, 1920, src='portrait.mp4') is None
    monkeypatch.setattr(live_tracking, 'reference_faces', lambda *a: [])
    args = dict(src='portrait.mp4', reference='linyuan.jpg', model_paths=('detector', 'recognizer'))
    assert P.audio_card_live_crop(882, 1920, **args) is None
    # An interior face, clear of the source's top and bottom text bands.
    monkeypatch.setattr(live_tracking, 'reference_faces', lambda *a: [(380, 540, 165, 210)] * 3)
    result = P.audio_card_live_crop(882, 1920, **args)
    assert result and 'scale=632:470' in result
    values = result.split(',')[0].split('=')[1].split(':')
    w, h, x, y = map(int, values)
    assert 0 <= x < 380 and x + w > 545 and x + w <= 882
    assert 0 <= y < 540 and y + h > 750 and y + h <= 1920
    # The same aspect now remains rejected if an overlay covers the face.
    assert P.audio_card_live_crop(882, 1920, exclusions=[(.4, .25, .65, .42)], **args) is None
