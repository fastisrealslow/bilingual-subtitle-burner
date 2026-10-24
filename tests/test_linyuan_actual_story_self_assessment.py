"""Reject an actual normal-run false positive without relabeling its media."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import publishing_experiment as E
from fc import index as F

FIXTURES=Path(__file__).parent/'fixtures'


def test_actual_primary_source_self_assessment_cannot_enter_experiment_stock():
    actual=json.loads((FIXTURES/'linyuan_actual_primary_self_assessment_20261024.json').read_text())
    m=actual['metadata'];before=deepcopy(m)
    assert actual['run_id']==40051361843
    assert m['experiment_proof']['selection']['event_quote']=='我今天是最多的时候。'
    assert m['fingerprints']['sha256']=='031c9e1821821d4efd3b4b165f873e923a724546fb1f2ba1529469f9b0782ffc'
    assert 'self-assessment' in E.proof_error(m)
    assert F.artifact_quality_error(m)
    assert m==before and m['editorial_profile']==E.TREATMENT


@pytest.mark.parametrize('event',[
    '我今天是最多的时候。','我今天的盈利实力是最好的。',
    '我现在是最强的。','我们目前的经营能力是最好的。',
])
def test_synthetic_bare_rankings_are_not_past_actions(event):
    assert E.personal_event_error(event)


@pytest.mark.parametrize('event',[
    '我当年买入了茅台，后来一直没有卖。',
    '我今天买入了那家公司。',
    '我今天赚了两百万元。',
    '我当时对他说，我现在是最强的。',
])
def test_synthetic_concrete_actions_are_not_bare_rankings(event):
    assert E.personal_event_error(event) is None


def test_actual_published_company_case_is_unchanged():
    actual=json.loads((FIXTURES/'linyuan_actual_published_holding_20261024.json').read_text())
    before=deepcopy(actual['meta'])
    assert E.proof_error(actual['meta']) is None
    assert actual['meta']==before
