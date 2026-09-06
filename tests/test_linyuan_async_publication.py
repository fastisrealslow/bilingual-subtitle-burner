"""Never duplicate an accepted upload when the invocation response is lost."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'linyuan/fc'), str(ROOT/'linyuan')]
import publish_fresh_six as pub


class Task(NS):
    def to_map(self):
        return vars(self)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pub.time, 'sleep', lambda _: None)
    monkeypatch.setattr(pub, 'receipts', lambda state: state.get('found', {}))
    m = NS(GetAsyncTaskRequest=NS, InvokeFunctionRequest=NS, InvokeFunctionHeaders=NS)
    approved = {'slug':'reviewed', 'title':'Complete argument', 'source_url':'https://example.test/source'}
    return m, approved


def test_existing_completed_task_is_reconciled_without_invoking(setup, monkeypatch):
    m, approved = setup
    task = Task(status='Succeeded', return_payload='{"published":1}')
    monkeypatch.setattr(pub, 'state', lambda: {'found':{'abc':{'bvid':'BVconfirmed'}}})
    client = NS(get_async_task_with_options=lambda *args: NS(body=task))
    pub.publish_async_part(client, 'function', m, NS(), 'abc', approved)


def test_lost_accept_response_queries_same_id_without_second_upload(setup, monkeypatch):
    m, approved = setup
    accepted = []
    class Missing(Exception):
        code = 'AsyncTaskNotFound'
    def query(function, task_id, *args):
        if not accepted:
            raise Missing()
        assert task_id == accepted[0]
        return NS(body=Task(status='Succeeded', return_payload='{"published":1}'))
    def invoke(function, request, headers, runtime):
        accepted.append(headers.x_fc_async_task_id)
        raise TimeoutError('Response lost after acceptance')
    client = NS(get_async_task_with_options=query, invoke_function_with_options=invoke)
    monkeypatch.setattr(pub, 'state', lambda: {'found':{'abc':{'bvid':'BVconfirmed'}}})
    pub.publish_async_part(client, 'function', m, NS(), 'abc', approved)
    assert len(accepted) == 1


def test_failed_task_with_upload_intent_stops_without_replay(setup, monkeypatch):
    m, approved = setup
    task = Task(status='Failed', return_payload='{}')
    client = NS(get_async_task_with_options=lambda *args: NS(body=task))
    monkeypatch.setattr(pub, 'state', lambda: {'dispatched':[{'slug':'reviewed','uploading':True}]})
    with pytest.raises(SystemExit, match='no exact receipt'):
        pub.publish_async_part(client, 'function', m, NS(), 'abc', approved)


def test_absent_fc_async_config_is_initialized_with_no_replay():
    class Missing(Exception):
        code = 'AsyncConfigNotExists'
    saved = []
    def get(*args):
        if not saved:
            raise Missing()
        return NS(body=saved[0])
    def put(function, request, *args):
        saved.append(request.body)
    models = NS(GetAsyncInvokeConfigRequest=NS, PutAsyncInvokeConfigInput=NS, PutAsyncInvokeConfigRequest=NS)
    pub.prepare_async_tasks(NS(get_async_invoke_config_with_options=get,
        put_async_invoke_config_with_options=put), 'function', models, NS())
    assert saved[0].async_task is True
    assert saved[0].max_async_retry_attempts == 0
