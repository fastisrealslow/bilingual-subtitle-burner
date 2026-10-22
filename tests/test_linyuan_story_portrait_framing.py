"""Choose a real moving-window canvas; do not weaken any picture gate."""
import inspect
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import produce_cn as P


@pytest.mark.parametrize('requested',['auto','landscape'])
def test_native_treatment_other_layouts_are_unchanged(requested):
    assert not P.story_portrait_window_required(True,1920,1080,requested)


def test_only_explicit_wide_treatment_uses_portrait_moving_window(monkeypatch):
    monkeypatch.setenv('OUTPUT_LAYOUT','portrait')
    assert P.story_portrait_window_required(True,1920,1080)
    assert not P.story_portrait_window_required(False,1920,1080)
    assert not P.story_portrait_window_required(True,1080,1920)
    with pytest.raises(ValueError):P.story_portrait_window_required(True,1920,1080,'fake-portrait')


def test_portrait_request_cannot_reenter_native_cleanup_or_static_approval():
    source=inspect.getsource(P._produce_one)
    assert 'if not portrait_story and (proposed_native' in source
    assert "if portrait_story:\n        strategy='audio_card'\n        crop_w,crop_h=AUDIO_CARD_WIDTH,AUDIO_CARD_HEIGHT" in source
    assert source.index('if portrait_story:')<source.index("if strategy != 'audio_card':")
    assert 'if strategy == "audio_card" and not use_live_video and require_live_video:' in source
    for check in ('render_tracked(','verify_live_region_after_render(',
                  'verify_prepared_live_motion(','verify_final_live_identity('):
        assert check in source
