"""Diagnostic cache accepts only exact actual requests and raw responses."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from check_company_qa_selection import RecordedCalls


def report():
    return dict(calls=[dict(prompt='same actual request',schema={'type':'object'},response='{"kind":"skip"}')])


def test_replay_uses_raw_response_not_saved_selection_or_verdict():
    rows=report();rows['selected']=[{'fake':'not approval'}]
    cache=RecordedCalls(rows)
    assert cache('same actual request',{'type':'object'})=='{"kind":"skip"}'
    cache.finish()
    with pytest.raises(AssertionError):cache('same actual request',{'type':'object'})


@pytest.mark.parametrize('change',['prompt','schema','error','missing_response'])
def test_changed_or_incomplete_model_evidence_requires_fresh_trial(change):
    rows=deepcopy(report())
    if change=='missing_response':rows['calls'][0].pop('response')
    elif change=='error':rows['calls'][0]['error']='timed out'
    else:rows['calls'][0][change]='different'
    with pytest.raises(AssertionError):RecordedCalls(rows)('same actual request',{'type':'object'})


def test_unused_actual_calls_cannot_be_silently_omitted():
    with pytest.raises(AssertionError):RecordedCalls(report()).finish()
