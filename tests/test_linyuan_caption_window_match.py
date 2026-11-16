import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('caption_windows', ROOT / 'scripts/audit_linyuan_subtitle_sync.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def window(start, text):
    return dict(start_sec=start, end_sec=start+3, text=text)


def test_actual_remote_repeat_is_ambiguous_not_a_53_second_delay():
    case = json.loads((ROOT / 'tests/fixtures/linyuan_repeated_caption_window.json').read_text())
    result = audit.match_caption_window(case['caption'], case['recognition'], case['frame_sec'])
    assert case['old_result']['outside_spoken_interval_sec'] == 53.5
    assert result['status'] == 'ambiguous_repeated_fragment'
    assert result['nearby_candidate']['independent_start_sec'] == 69
    assert result['remote_candidate']['outside_spoken_interval_sec'] == 53.5
    assert 'outside_spoken_interval_sec' not in result


@pytest.mark.parametrize('phrase', ['企业必须回到生意本质', '需求变化需要认真研究'])
def test_nearby_shorter_repeat_preserves_both_witnesses(phrase):
    result = audit.match_caption_window(phrase, [window(0, phrase[:-1]), window(90, phrase)], 1)
    assert result['status'] == 'ambiguous_repeated_fragment'
    assert result['nearby_candidate']['independent_start_sec'] == 0


def test_unique_distant_phrase_still_reports_a_real_window_disagreement():
    result = audit.match_caption_window('企业必须回到生意本质',
        [window(0, '今天讨论天气变化'), window(90, '企业必须回到生意本质')], 1)
    assert result['status'] == 'exact_fragment_window_match'
    assert result['outside_spoken_interval_sec'] == 89


def test_nearby_complete_match_wins_over_an_identical_distant_repeat():
    result = audit.match_caption_window('企业必须回到生意本质',
        [window(90, '企业必须回到生意本质'), window(0, '企业必须回到生意本质')], 1)
    assert result['status'] == 'exact_fragment_window_match'
    assert result['independent_start_sec'] == 0
    assert result['outside_spoken_interval_sec'] == 0


def test_unrelated_nearby_fragment_cannot_hide_a_unique_distant_match():
    result = audit.match_caption_window('企业盈利很重要但是需求不明',
        [window(0, '但是需求'), window(90, '企业盈利很重要')], 1)
    assert result['status'] == 'exact_fragment_window_match'
    assert result['outside_spoken_interval_sec'] == 89


def test_no_sufficient_evidence_stays_unconfirmed():
    assert audit.match_caption_window('投资', [window(0, '投资')], 1) == dict(status='wording_unconfirmed')
