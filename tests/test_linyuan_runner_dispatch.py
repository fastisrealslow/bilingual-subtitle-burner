"""Moving hourly coordination must not remove recovery or domestic uploads."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'linyuan/fc'), str(ROOT / 'linyuan')]
import index as fc
import dispatch_on_runner as runner
import catchup_from_inventory as catchup
import verify_production as verify


@pytest.mark.parametrize('page,video,domestic', [
    ('https://www.bilibili.com/video/BVtest', '', False),
    ('https://weibo.com/123', 'https://cdn.invalid/video', False),
    ('https://news.qq.com/rain/a/test', '', True),
    ('https://news.qq.com/rain/a/test', 'https://cdn.invalid/video', True),
    ('https://example.invalid/interview', 'https://cdn.invalid/video', True),
    ('https://example.invalid/interview', '', False),
])
def test_original_domestic_download_routes_are_preserved(page, video, domestic):
    assert fc.dispatch_requires_mainland(dict(page_url=page, video_url=video)) is domestic


def test_runner_defers_domestic_source_without_downloading_or_rejecting_it(monkeypatch):
    state = dict(dispatched=[], published={}, pending_retry=[], rejected=[])
    candidate = dict(page_url='https://news.qq.com/rain/a/test', video_url='', key='mother')
    stock = dict(inventory_fresh=True, verified_portrait=0, verified_landscape=0,
                 daily_mix_usable=0)
    monkeypatch.setattr(fc, 'load_state', lambda: state)
    monkeypatch.setattr(fc, '_collect_source_rejections', lambda _: 0)
    monkeypatch.setattr(fc, 'source_inventory', lambda *a: stock)
    monkeypatch.setattr(fc, 'staging_release_id', lambda: 1)
    monkeypatch.setattr(fc, 'obsolete_review_candidates', lambda *a: [])
    monkeypatch.setattr(fc, 'gh', lambda method, path, **kw:
        b'[]' if fc.DATA_JSON in path else b'{}' if fc.SOURCE_INVENTORY_KEY in path
        else dict(workflow_runs=[]))
    def pick(items, st, limit, audit):
        audit.update(records=1, candidate_count=1)
        return [candidate]
    monkeypatch.setattr(fc, 'pick', pick)
    def forbidden(*a, **kw):
        raise AssertionError('Runner must not download/reject/mark this domestic source')
    for name in ('download', 'tencent_resolve_url', 'save_state', '_record_failure'):
        monkeypatch.setattr(fc, name, forbidden)
    assert fc._dispatch_admitted({'execution_backend': 'github'}) == {
        'dispatched': 0, 'requires_mainland_transfer': True}
    assert 'slug' not in candidate and state['dispatched'] == [] and state['rejected'] == []


@pytest.fixture
def runner_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fc, 'TOKEN', 'test')
    monkeypatch.setattr(fc, 'log_event', lambda *a: None)
    monkeypatch.setattr(fc, 'flush_logs', lambda: None)
    def forbidden(*a, **kw):
        raise AssertionError('Unexpected paid FC invocation')
    monkeypatch.setattr(catchup, 'run_inventory_task', forbidden)
    return tmp_path


def test_empty_runner_check_retires_timer_without_fc_invocation(monkeypatch, runner_env):
    sequence = []
    def dispatch(event):
        assert event == {'execution_backend': 'github'}
        sequence.append('admission')
        return dict(dispatched=0, attempted=0)
    monkeypatch.setattr(fc, 'dispatch_handler', dispatch)
    monkeypatch.setattr(runner, 'retire_timer', lambda: sequence.append('retire') or {})
    assert runner.main()['completed'] is True
    assert sequence == ['admission', 'retire']


@pytest.mark.parametrize('result', [{'admission_busy': 1}, {'inventory_unavailable': 1}])
def test_unproven_runner_keeps_original_timer(monkeypatch, runner_env, result):
    monkeypatch.setattr(fc, 'dispatch_handler', lambda _: result)
    monkeypatch.setattr(runner, 'retire_timer', lambda: pytest.fail('Do not retire yet'))
    receipt = runner.main()
    assert not receipt['completed'] and receipt['migration_deferred']


def test_runner_error_keeps_original_timer_and_failure_receipt(monkeypatch, runner_env):
    def fail(_): raise ConnectionError('GitHub unavailable')
    monkeypatch.setattr(fc, 'dispatch_handler', fail)
    monkeypatch.setattr(runner, 'retire_timer', lambda: pytest.fail('Do not retire on error'))
    with pytest.raises(ConnectionError): runner.main()
    assert not json.loads(Path('github-dispatch-receipt.json').read_text())['completed']


@pytest.mark.parametrize('status', ['Succeeded', 'Running', 'Failed'])
def test_domestic_transfer_uses_fc_only_after_local_dispatch_returns(monkeypatch, runner_env, status):
    sequence = []
    monkeypatch.setattr(fc, 'dispatch_handler', lambda _: sequence.append('local-return') or
                        dict(dispatched=1, requires_mainland_transfer=True))
    def remote(action):
        assert action == 'dispatch-source-inventory' and sequence == ['local-return']
        sequence.append('fc-transfer')
        return 'same-task-id', NS(status=status, return_payload='{"dispatched":1}')
    monkeypatch.setattr(catchup, 'run_inventory_task', remote)
    monkeypatch.setattr(runner, 'retire_timer', lambda: sequence.append('retire') or {})
    if status == 'Succeeded':
        assert runner.main()['completed']
        assert sequence == ['local-return', 'fc-transfer', 'retire']
    else:
        with pytest.raises(RuntimeError, match='unresolved'): runner.main()
        assert sequence == ['local-return', 'fc-transfer']
        receipt = json.loads(Path('github-dispatch-receipt.json').read_text())
        assert receipt['domestic_transfer']['task_id'] == 'same-task-id'


class TimerClient:
    def __init__(self, dispatch_enabled=True, publish_enabled=True):
        self.writes = []
        self.triggers = {}
        for name, cron, enabled in [('dispatch', '0 37 * * * *', dispatch_enabled),
                                   ('publish', '0 0 2,6,8,13 * * *', publish_enabled)]:
            self.triggers[name] = NS(trigger_name=name, trigger_type='timer', qualifier='LATEST',
                trigger_config=json.dumps(dict(cronExpression=cron, enable=enabled,
                                               payload=json.dumps(dict(triggerName=name)))))

    def list_triggers_with_options(self, *args):
        return NS(body=NS(triggers=list(self.triggers.values())))

    def update_trigger_with_options(self, function, name, request, headers, runtime):
        self.writes.append(name)
        self.triggers[name].trigger_config = request.body.trigger_config
        if getattr(request.body, 'qualifier', None):
            self.triggers[name].qualifier = request.body.qualifier


MODELS = NS(ListTriggersRequest=NS, UpdateTriggerRequest=NS, UpdateTriggerInput=NS)


def test_migration_only_disables_dispatch_and_is_idempotent():
    client = TimerClient()
    publish = client.triggers['publish'].trigger_config
    runner.retire_hourly_timer(client, 'fc', MODELS, NS())
    runner.retire_hourly_timer(client, 'fc', MODELS, NS())
    assert client.writes == ['dispatch']
    assert client.triggers['publish'].trigger_config == publish


def test_broken_publication_schedule_blocks_retirement():
    client = TimerClient(publish_enabled=False)
    with pytest.raises(RuntimeError, match='daily four'):
        runner.retire_hourly_timer(client, 'fc', MODELS, NS())
    assert client.writes == []


@pytest.mark.parametrize('enabled', [True, False])
def test_later_deployment_preserves_dispatch_migration_state(enabled):
    client = TimerClient(dispatch_enabled=enabled)
    proof = verify.configure_timers(client, 'fc', MODELS, NS())
    configs = {row['name']: row['config'] for row in proof}
    assert configs['dispatch']['enable'] is enabled
    assert configs['publish']['enable'] is True
    assert configs['publish']['cronExpression'] == '0 0 2,6,8,13 * * *'
