"""Exercise Alibaba's actual timer envelope, not a synthetic direct trigger."""
import json
from pathlib import Path
import sys
import pytest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'linyuan/fc'),str(Path(__file__).resolve().parents[1]/'linyuan')]
import index as fc
import publish_clock as clock
import fc_cost_policy as policy


def event(payload=None):
    return dict(triggerName='publish',triggerTime='2026-11-14T13:55:00Z',
                payload=json.dumps(payload if payload is not None else dict(triggerName='publish-recovery',purpose='last_chance_daily_four')))


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(policy,'RUNNER_PUBLISH_ENABLED',True)
    monkeypatch.setattr(policy,'CI_CLOCK_ENABLED',True)


def test_real_timer_envelope_enters_domestic_recovery_not_another_ci_wakeup(monkeypatch):
    calls=[]
    monkeypatch.setattr(clock,'recover',lambda module:calls.append(module) or dict(published=0,recovery=True))
    monkeypatch.setattr(clock,'request',lambda *a:pytest.fail('Recovery timer must not only request CI'))
    assert fc.handler(json.dumps(event()),None)==dict(published=0,recovery=True)
    assert calls==[fc]


def test_real_envelope_when_four_done_performs_one_state_read_and_no_lease(monkeypatch):
    calls=[]
    monkeypatch.setattr(fc,'load_state',lambda:calls.append('state') or {})
    monkeypatch.setattr(fc,'catchup_deficit',lambda _:0)
    monkeypatch.setattr(fc,'run_with_lease',lambda *a,**k:pytest.fail('Completed day must not wait for a lease'))
    monkeypatch.setattr(clock,'request',lambda *a:pytest.fail('Completed day must not dispatch CI'))
    assert fc.handler(json.dumps(event()),None)==dict(published=0,reason='due_slots_already_complete')
    assert calls==['state']


def test_real_envelope_retains_shared_lease_and_original_catchup_gates(monkeypatch):
    calls=[];results=iter([dict(published=1),dict(published=0)])
    monkeypatch.setattr(fc,'load_state',lambda:{})
    monkeypatch.setattr(fc,'catchup_deficit',lambda _:1)
    def lease(kind,action,wait_seconds):
        assert kind=='publish' and wait_seconds==0
        calls.append('lease');return action()
    monkeypatch.setattr(fc,'run_with_lease',lease)
    monkeypatch.setattr(fc,'publish_catchup',lambda e:calls.append(e['triggerName']) or next(results))
    monkeypatch.setattr(fc,'flush_logs',lambda:None)
    monkeypatch.setattr(clock,'request',lambda *a:pytest.fail('No second workflow request'))
    r=fc.handler(json.dumps(event()),None)
    assert r['published']==1 and calls==['lease','publish-catchup','publish-catchup']


@pytest.mark.parametrize('bad',[{},dict(triggerName='publish-recovery'),
    dict(triggerName='publish-recovery',purpose='other'),
    dict(triggerName='reviewed-upload',purpose='last_chance_daily_four')])
def test_unknown_custom_messages_cannot_promote_to_domestic_recovery(bad):
    assert clock.event_route(event(bad),policy)=='publish'


def test_malformed_payload_and_non_timer_calls_keep_existing_route():
    r=event();r['payload']='not json';assert clock.event_route(r,policy)=='publish'
    r=event();r.pop('triggerTime');assert clock.event_route(r,policy)=='publish'
    assert clock.event_route(dict(triggerName='diagnose-config'),policy)=='diagnose-config'
    assert clock.event_route(dict(triggerName='publish-recovery'),policy)=='publish-recovery'


def test_staged_clock_never_enables_domestic_recovery(monkeypatch):
    monkeypatch.setattr(policy,'CI_CLOCK_ENABLED',False)
    assert clock.event_route(event(),policy)=='publish'
