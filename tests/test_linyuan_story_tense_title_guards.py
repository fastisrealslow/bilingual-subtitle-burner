"""Actual classifier/title failures and an unchanged published positive."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import publishing_experiment as E
import headline_policy as H

FIXTURES=Path(__file__).parent/'fixtures'


def test_actual_future_and_hypothetical_events_are_not_completed_experiences():
    actual=json.loads((FIXTURES/'linyuan_actual_false_story_tense_20261024.json').read_text())
    assert len(actual['cases'])==2
    for row in actual['cases']:
        assert E.personal_event_error(row['event'])


def test_actual_published_company_case_keeps_its_original_profile_and_proof():
    actual=json.loads((FIXTURES/'linyuan_actual_published_holding_20261024.json').read_text())
    meta=actual['meta'];before=deepcopy(meta)
    assert actual['published_bvid']=='BV13QHA6DEar'
    assert meta['fingerprints']['sha256']=='0e57dd3415c7aaa5d66fbb91442b2c7cc15cef7dfc85e993ada71088f3374919'
    assert E.proof_error(meta) is None and E.actual_profile(meta)==E.TREATMENT
    assert meta==before


@pytest.mark.parametrize('event',[
    '我当年买入了茅台，后来一直没有卖。',
    '我当时制定了如果亏损就退出的计划。',
    '我去年参加过会议，以后我还想买入那家公司。',
])
def test_synthetic_actual_actions_and_past_planning_are_not_future_only(event):
    assert E.personal_event_error(event) is None


@pytest.mark.parametrize('title',[
    '林园：见过五千人，要把对市场看法印证实',
    '林园：市场热时一级泡沫大，冷时不管什么市场都妈差',
])
def test_actual_approved_but_malformed_titles_require_a_new_draft(title):
    assert H.readability_error(title) and H.verbal_fragment(title)


@pytest.mark.parametrize('title',[
    '林园：这件事印证了我的看法',
    '林园：我想看看这个判断能否得到证实',
    '林园：我妈差点把房子卖了',
])
def test_nearby_complete_words_are_not_blocked(title):
    assert H.readability_error(title) is None
