"""Exercise the actual Actions release gate without accessing FC."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.safe_load((ROOT/'.github/workflows/fc-production-deploy.yml').read_text())


@pytest.mark.parametrize('policy,allowed', [
    (None, False), ({}, False), ({'deployment_paused':True}, False),
    ({'deployment_paused':'false'}, False), ({'deployment_paused':0}, False),
    ({'deployment_paused':False}, True),
])
def test_actual_workflow_gate_fails_closed_without_explicit_release(tmp_path, policy, allowed):
    if policy is not None:
        (tmp_path/'.github').mkdir()
        (tmp_path/'.github/linyuan-release-policy.json').write_text(json.dumps(policy))
    step=workflow()['jobs']['release-policy']['steps'][1]
    code=step['run'].split("python - <<'PY'\n",1)[1].rsplit('\nPY',1)[0]
    output=tmp_path/'outputs'
    subprocess.run([sys.executable,'-c',code],cwd=tmp_path,
                   env={**os.environ,'GITHUB_OUTPUT':str(output)},check=True,capture_output=True)
    assert output.read_text()=='allowed='+str(allowed).lower()+'\n'


def test_all_fc_jobs_depend_on_release_gate():
    jobs=workflow()['jobs']
    for name in ('title-portrait','title-landscape'):
        assert jobs[name]['needs']=='release-policy'
        assert jobs[name]['if']=="needs.release-policy.outputs.allowed == 'true'"
    assert set(jobs['editorial-check']['needs'])=={'title-portrait','title-landscape'}
    assert jobs['deploy']['needs']=='editorial-check'
    assert 'env' not in jobs['release-policy']
