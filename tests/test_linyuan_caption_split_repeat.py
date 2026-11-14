"""A real phrase split over adjacent windows is not evidence of a late caption."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_linyuan_subtitle_sync import match_caption_window


def window(start, text, end=None):
    return dict(start_sec=start, end_sec=start+3 if end is None else end, text=text)


def test_actual_repeated_phrase_keeps_the_two_local_acoustic_witnesses():
    data = json.loads((Path(__file__).parent/'fixtures/linyuan_caption_split_repeat.json').read_text())
    original = json.dumps(data, ensure_ascii=False)
    result = match_caption_window(data['caption'], data['recognition'], data['frame_sec'])
    assert result['status'] == 'ambiguous_repeated_fragment'
    assert result['remote_candidate']['independent_start_sec'] == 45
    local = result['nearby_candidate']
    assert (local['independent_start_sec'], local['independent_end_sec']) == (18, 24)
    assert len(local['acoustic_windows']) == 2
    assert json.dumps(data, ensure_ascii=False) == original


@pytest.mark.parametrize('phrase,first,second', [
    ('企业盈利决定长期价值', '企业盈利', '决定长期价值'),
    ('风险控制需要完整判断', '风险控制需要', '完整判断'),
])
def test_other_phrases_split_at_real_contiguous_windows_are_ambiguous(phrase, first, second):
    result = match_caption_window(phrase, [window(0, first), window(3, second), window(60, phrase)], 3.5)
    assert result['status'] == 'ambiguous_repeated_fragment'


@pytest.mark.parametrize('recognition', [
    [window(0, '企业盈利'), window(9, '决定长期价值'), window(60, '企业盈利决定长期价值')],
    [window(0, '企业盈利', end=6), window(6, '决定长期价值', end=12), window(60, '企业盈利决定长期价值')],
    [window(0, ''), window(3, '决定长期价值'), window(60, '企业盈利决定长期价值')],
])
def test_missing_empty_or_overlong_windows_cannot_supply_a_local_witness(recognition):
    result = match_caption_window('企业盈利决定长期价值', recognition, 2)
    assert result['status'] == 'exact_fragment_window_match'
    assert result['independent_start_sec'] == 60
    assert result['outside_spoken_interval_sec'] > 2


def test_unique_local_evidence_and_insufficient_text_keep_their_meaning():
    result = match_caption_window('企业盈利决定长期价值', [window(0, '企业盈利决定长期价值')], 1)
    assert result['status'] == 'exact_fragment_window_match'
    assert result['outside_spoken_interval_sec'] == 0
    assert match_caption_window('投资', [window(0, '投资')], 1) == dict(status='wording_unconfirmed')
