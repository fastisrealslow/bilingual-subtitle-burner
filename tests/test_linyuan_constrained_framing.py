import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
from stable_framing import StableFraming
from live_tracking import shot_crop, avoid_overlays


def test_constrained_headroom_does_not_fight_aesthetic_pan_zone():
    framing = StableFraming(30)
    framing.box = (144., 120., 468., 348.)
    marks = [(0., 0., 1., 16/480), (276/854, 0., 480/854, 44/480)]
    # Actual source preflight face, safely inside the published window, but
    # the old .18*face-height/.04*crop-height pan zone moves it needlessly.
    face = (297.52, 169.54, 158.42, 212.34)
    boxes = [shot_crop(framing, face, 854, 480, n, marks) for n in range(300)]
    assert set(boxes) == {(144, 120, 468, 348)}
    assert framing.proof()['constraint_held_frames'] == 300
    assert framing.proof()['max_pan_output_px'] == 0


def test_constrained_hold_never_hides_an_overlay_or_cuts_a_face():
    framing = StableFraming(30)
    framing.box = (420., 4., 1262., 938.)
    face = (730., 140., 350., 360.)
    marks = [(1480/1920, 35/1080, 1840/1920, 135/1080)]
    box = shot_crop(framing, face, 1920, 1080, 30, marks)
    assert box != (420, 4, 1262, 938)
    assert avoid_overlays(box, face, 1920, 1080, marks) == box
    assert framing.proof()['constraint_held_frames'] == 0
    assert framing.proof()['max_pan_output_px'] > 0
