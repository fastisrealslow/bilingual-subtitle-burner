"""Scheduled auditing must select real distinct receipts and avoid repeated agents."""
from datetime import datetime, timezone, timedelta
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('daily_audit_runner', ROOT/'scripts/linyuan_daily_audit.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
NOW = datetime(2026, 9, 25, 9, tzinfo=timezone.utc)


def test_beijing_due_time_and_next_installation_date():
    assert not audit.due(NOW-timedelta(seconds=1), {})
    assert audit.due(NOW, {})
    assert audit.due(NOW+timedelta(hours=2), {})  # Wake catch-up.
    assert not audit.due(NOW, dict(not_before=(NOW+timedelta(days=1)).timestamp()))


def test_at_most_one_completed_run_and_two_bounded_attempts_per_day():
    base=dict(date='2026-09-25', finished_at=NOW.timestamp(), attempts=1)
    assert not audit.due(NOW+timedelta(hours=1), dict(base,status='completed'))
    assert not audit.due(NOW+timedelta(minutes=29), dict(base,status='failed'))
    assert audit.due(NOW+timedelta(minutes=31), dict(base,status='failed'))
    assert not audit.due(NOW+timedelta(hours=2), dict(base,status='failed',attempts=2))
    assert audit.due(NOW+timedelta(days=1), dict(base,status='completed'))


def test_latest_four_are_distinct_published_videos_across_dates_and_parts():
    def part(bvid,ts,status='published'):
        return dict(bvid=bvid,ts=ts,status=status,title=bvid)
    state=dict(published={
        'yesterday':part('BVold',1),
        'batch':dict(parts=[part('BV1',2),part('BV2',3)]),
        'today':dict(parts=[part('BV3',4),part('BV4',5)]),
        'duplicate_ledger':part('BV1',99),
        'pending':part('BVpending',100,'submitted'),
        'missing_time':dict(status='published',bvid='BVmissing'),
    })
    assert [r['bvid'] for r in audit.latest_publications(state)]==['BV4','BV3','BV2','BV1']
    assert audit.latest_publications(dict(published={}))==[]
    with pytest.raises(ValueError):audit.latest_publications({})


def test_lock_prevents_concurrent_audit(tmp_path):
    with (tmp_path/'run.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        assert audit.run(tmp_path,tmp_path,Path('/missing'),Path('/missing'),NOW)=='already_running'


def test_fetch_failure_is_recorded_not_reported_as_success(tmp_path,monkeypatch):
    def failed(*a,**k):raise subprocess.CalledProcessError(1,['git','fetch'])
    monkeypatch.setattr(audit.subprocess,'run',failed)
    with pytest.raises(subprocess.CalledProcessError):
        audit.run(tmp_path,tmp_path,Path('/missing'),Path('/missing'),NOW)
    state=json.loads((tmp_path/'state.json').read_text())
    assert state['status']=='failed' and state['attempts']==1 and state['error']
    assert not (tmp_path/'latest-report.md').exists()


def test_before_schedule_launches_no_agent(tmp_path,monkeypatch):
    def forbidden(*a,**k):raise AssertionError('No subprocess before due time')
    monkeypatch.setattr(audit.subprocess,'run',forbidden)
    assert audit.run(tmp_path,tmp_path,Path('/missing'),Path('/missing'),NOW-timedelta(seconds=1))=='not_due'


def test_completed_agent_preserves_report_and_requires_nonempty_output(tmp_path,monkeypatch):
    prompt=tmp_path/'prompt.md';prompt.write_text('Audit four published videos')
    def git(args,**kwargs):
        if 'worktree' in args:
            work=Path(args[-2]);(work/'linyuan/.automation').mkdir(parents=True)
            (work/'linyuan/.automation/fc_state.json').write_text('{"published": {}}')
    monkeypatch.setattr(audit.subprocess,'run',git)
    class Agent:
        returncode=0
        def __init__(self,args,**kwargs):
            self.report=Path(args[args.index('--output-last-message')+1])
            assert '--dangerously-bypass-approvals-and-sandbox' not in args
        def communicate(self,prompt,**kwargs):
            assert 'public_visibility_verified' in prompt
            self.report.write_text('未取得四条视频，检查未完成。')
    monkeypatch.setattr(audit.subprocess,'Popen',Agent)
    assert audit.run(tmp_path,tmp_path,Path('/fake-codex'),prompt,NOW)=='completed'
    state=json.loads((tmp_path/'state.json').read_text())
    assert 'video verdicts' in state['note']
    assert (tmp_path/'latest-report.md').read_text()=='未取得四条视频，检查未完成。'
    assert audit.run(tmp_path,tmp_path,Path('/fake-codex'),prompt,NOW)=='not_due'


def test_absent_report_is_failure_even_if_agent_exit_code_is_zero(tmp_path,monkeypatch):
    prompt=tmp_path/'prompt.md';prompt.write_text('Audit')
    def git(args,**kwargs):
        if 'worktree' in args:
            work=Path(args[-2]);(work/'linyuan/.automation').mkdir(parents=True)
            (work/'linyuan/.automation/fc_state.json').write_text('{"published": {}}')
    class Agent:
        returncode=0
        def __init__(self,*a,**k):pass
        def communicate(self,*a,**k):pass
    monkeypatch.setattr(audit.subprocess,'run',git)
    monkeypatch.setattr(audit.subprocess,'Popen',Agent)
    with pytest.raises(RuntimeError,match='no audit report'):
        audit.run(tmp_path,tmp_path,Path('/fake-codex'),prompt,NOW)
    assert json.loads((tmp_path/'state.json').read_text())['status']=='failed'
