from pathlib import Path

import yaml

ROOT=Path(__file__).resolve().parents[1]


def config():
    return yaml.load((ROOT/'.github/workflows/linyuan-apply-reported-tracking.yml').read_text(),Loader=yaml.BaseLoader)


def test_historical_replacement_is_manual_only_not_every_production_deploy():
    workflow=config()
    assert set(workflow['on'])=={'workflow_dispatch'}
    condition=workflow['jobs']['apply']['if']
    assert "github.event_name == 'workflow_dispatch'" in condition
    assert "github.ref == 'refs/heads/main'" in condition
    assert '||' not in condition and 'workflow_run' not in condition


def test_manual_entry_keeps_exact_original_guard_and_revision_invocation():
    steps=config()['jobs']['apply']['steps']
    commands=[s.get('run','') for s in steps]
    assert 'python -u linyuan/stop_prior_tracking_upload.py' in commands
    assert 'python -u linyuan/fc/invoke_tracking_revision.py' in commands
    assert all('linyuan-produce-cn.yml' not in command for command in commands)
