import importlib.util
from pathlib import Path
import subprocess
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
spec=importlib.util.spec_from_file_location('deployment_freshness',
    Path(__file__).resolve().parents[1]/'linyuan/fc/check_deploy_current.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_only_latest_runtime_can_pass_and_ledger_updates_are_not_code(monkeypatch):
    calls=[]
    monkeypatch.setattr(m.subprocess,'check_output',lambda args,**kw:calls.append(args) or '')
    m.assert_current()
    assert 'linyuan/caption_readability.py' in calls[0]
    assert 'linyuan/weibo_media.py' in calls[0]
    assert '.github/workflows/linyuan-produce-cn.yml' in calls[0]
    assert not any('fc_state.json' in p or 'source_inventory.json' in p for p in calls[0])


def test_new_runtime_or_policy_refuses_old_package(monkeypatch):
    monkeypatch.setattr(m.subprocess,'check_output',lambda *a,**kw:'linyuan/title_rewrite.py\n')
    with pytest.raises(SystemExit,match='拒绝覆盖'):m.assert_current()


def test_unavailable_main_read_never_turns_into_permission_to_deploy(monkeypatch):
    def unavailable(*a,**kw):raise subprocess.CalledProcessError(128,['git'])
    monkeypatch.setattr(m.subprocess,'check_output',unavailable)
    with pytest.raises(subprocess.CalledProcessError):m.assert_current()


def test_current_guard_runs_immediately_before_upload_not_before_slow_cpu_checks():
    workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/fc-production-deploy.yml').read_text()
    assert 'git fetch --no-tags origin main\n          python linyuan/fc/check_deploy_current.py\n          python linyuan/fc/deploy_code.py' in workflow
