"""Lost GETs and asynchronous failures must never cause duplicate publications."""
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'linyuan/fc'), str(ROOT/'linyuan')]
import catchup_from_inventory as catchup
import invoke_reviewed_updates as reviewed
import state_read


@pytest.mark.parametrize('error', [ConnectionResetError(), TimeoutError(),
    urllib.error.URLError('reset'), urllib.error.HTTPError('https://test',503,'busy',{},None)])
def test_read_retries_transient_errors_and_returns_exact_receipt(monkeypatch, error):
    response=io.BytesIO(b'{"reviewed_updates_0910":{"BVexact":{"status":"verified"}}}')
    get=Mock(side_effect=[error,response])
    monkeypatch.setattr(state_read.urllib.request,'urlopen',get)
    monkeypatch.setattr(state_read.time,'sleep',lambda _:None)
    assert reviewed.persisted_receipts(bvids=('BVexact',))=={'BVexact':{'status':'verified'}}
    assert get.call_count==2


@pytest.mark.parametrize('code', [401,403,404])
def test_permanent_read_errors_fail_without_retries(monkeypatch,code):
    get=Mock(side_effect=urllib.error.HTTPError('https://test',code,'denied',{},None))
    monkeypatch.setattr(state_read.urllib.request,'urlopen',get)
    with pytest.raises(urllib.error.HTTPError):state_read.read_json_get('https://test')
    assert get.call_count==1


def test_retry_budget_and_invalid_json_never_invent_receipts(monkeypatch):
    get=Mock(side_effect=ConnectionResetError())
    monkeypatch.setattr(state_read.urllib.request,'urlopen',get)
    monkeypatch.setattr(state_read.time,'sleep',lambda _:None)
    with pytest.raises(ConnectionResetError):state_read.read_json_get('https://test')
    assert get.call_count==3
    get=Mock(return_value=io.BytesIO(b'invalid'))
    monkeypatch.setattr(state_read.urllib.request,'urlopen',get)
    with pytest.raises(json.JSONDecodeError):state_read.read_json_get('https://test')
    assert get.call_count==1
    with pytest.raises(ValueError,match='GET'):
        state_read.read_json_get(urllib.request.Request('https://test',data=b'upload'))
    assert get.call_count==1


def test_lost_acceptance_is_reconciled_without_another_invocation(monkeypatch):
    accepted=[]
    class Missing(Exception):code='AsyncTaskNotFound'
    def query(function,task_id,*args):
        if not accepted:raise Missing()
        assert task_id==accepted[0]
        return NS(body=NS(status='Succeeded',return_payload='{"published":1}'))
    def invoke(function,request,headers,runtime):
        accepted.append(headers.x_fc_async_task_id)
        raise TimeoutError('response lost after acceptance')
    models=NS(GetAsyncTaskRequest=NS,InvokeFunctionRequest=NS,InvokeFunctionHeaders=NS)
    client=NS(get_async_task_with_options=query,invoke_function_with_options=invoke)
    assert catchup.tracked_request(client,'fc',models,NS(),'publish-catchup','same-id').status=='Succeeded'
    assert accepted==['same-id']
    # Re-running the same request reads the existing task, without invoking again.
    catchup.tracked_request(client,'fc',models,NS(),'publish-catchup','same-id')
    assert accepted==['same-id']


@pytest.mark.parametrize('status',['Failed','Stopped','Expired','Invalid','Running','Unknown'])
def test_failed_or_unresolved_task_exits_unsuccessfully_and_keeps_receipt(monkeypatch,tmp_path,status):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(catchup,'state',lambda:{})
    monkeypatch.setattr(catchup.fc,'source_inventory',lambda _: {})
    monkeypatch.setattr(catchup,'inventory_action',lambda *args:'publish-catchup')
    task=NS(status=status,return_payload='{}') if status!='Unknown' else None
    monkeypatch.setattr(catchup,'run_inventory_task',lambda _:('same-id',task))
    with pytest.raises(SystemExit,match='retain task ID'):catchup.main()
    receipt=json.loads(Path('catchup-receipt.json').read_text())
    assert receipt['task_id']=='same-id' and receipt['status']==status
    assert receipt['outcome'] in ('failed','pending')


def test_successful_empty_dispatch_and_existing_video_are_not_new_publications(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    existing={'published':{'old':{'parts':[{'status':'published','bvid':'BVold'}]}}}
    monkeypatch.setattr(catchup,'state',lambda:existing)
    monkeypatch.setattr(catchup.fc,'source_inventory',lambda _: {})
    monkeypatch.setattr(catchup,'inventory_action',lambda *args:'dispatch-source-inventory')
    monkeypatch.setattr(catchup,'run_inventory_task',lambda _:('same-id',NS(status='Succeeded',return_payload='{"dispatched":0}')))
    catchup.main()
    receipt=json.loads(Path('catchup-receipt.json').read_text())
    assert receipt['new_bvids']==[]
    assert receipt['outcome']=='completed_without_new_publication'


def test_state_read_failure_preserves_successful_task_for_reconciliation(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(catchup,'state',Mock(side_effect=[{},ConnectionResetError()]))
    monkeypatch.setattr(catchup.fc,'source_inventory',lambda _: {})
    monkeypatch.setattr(catchup,'inventory_action',lambda *args:'publish-catchup')
    monkeypatch.setattr(catchup,'run_inventory_task',lambda _:('same-id',NS(status='Succeeded',return_payload='{"published":1}')))
    with pytest.raises(ConnectionResetError):catchup.main()
    receipt=json.loads(Path('catchup-receipt.json').read_text())
    assert receipt['task_id']=='same-id' and receipt['status']=='Succeeded'
    assert receipt['outcome']=='pending'


def test_fc_terminal_event_exposes_busy_noop_instead_of_empty_return_payload():
    task=NS(return_payload='',to_map=lambda:dict(events=[dict(status='Succeeded',
        eventDetail=json.dumps(dict(logTail=json.dumps(dict(published=0,publisher_busy=1)))))]))
    assert catchup.task_result(task)==dict(published=0,publisher_busy=1)
    assert catchup.task_result(NS(return_payload='not json')) is None


def test_successful_platform_status_without_publication_fails_gate(monkeypatch,tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(catchup,'state',lambda:{})
    monkeypatch.setattr(catchup.fc,'source_inventory',lambda _: {})
    monkeypatch.setattr(catchup,'inventory_action',lambda *args:'publish-catchup')
    monkeypatch.setattr(catchup,'run_inventory_task',lambda _:('same-id',NS(status='Succeeded',return_payload='{"published":0,"publisher_busy":1}')))
    with pytest.raises(SystemExit,match='no new receipt'):catchup.main()
    receipt=json.loads(Path('catchup-receipt.json').read_text())
    assert receipt['function_result']['publisher_busy']==1 and receipt['new_bvids']==[]
