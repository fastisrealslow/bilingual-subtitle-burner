"""Run the actual FC archive outside the checkout, with production defaults."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location('fc_package_test_'+name, ROOT/'linyuan/fc'/f'{name}.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture(scope='module')
def packaged_health(tmp_path_factory):
    folder = tmp_path_factory.mktemp('fc-isolated-runtime')
    archive = module('package_code').build(folder/'fc.zip')
    with zipfile.ZipFile(archive) as z:
        z.extractall(folder/'runtime')
    env = {k: v for k, v in os.environ.items()
           if k not in ('PYTHONPATH', 'LINYUAN_CONTENT_POLICY', 'GITHUB_TOKEN', 'BILIBILI_COOKIES')}
    script = '''import sys,json
sys.path.insert(0,sys.argv[1])
import index,title_rewrite,headline_policy,stage_context,source_geometry,live_motion,artifact_range
def forbidden(*a,**k): raise AssertionError('health check must be read-only and local')
for name in ('gh','load_state','flush_logs','log_event','source_inventory','pick'):
    setattr(index,name,forbidden)
case=json.load(open(sys.argv[2]))
assert '不能为制造反差' in title_rewrite.error(case['title'],case['proof'],case['transcript'])
headline_policy.cover_fits('买入系统和卖出系统')
print(json.dumps(index.handler(json.dumps({'triggerName':'diagnose-config'}),None)))
'''
    output = subprocess.check_output([sys.executable, '-I', '-c', script,
        str(folder/'runtime'), str(ROOT/'tests/fixtures/linyuan_crisis_title_regression.json')],
        cwd=folder, env=env, text=True)
    return json.loads(output)


def test_packaged_runtime_passes_new_production_health(packaged_health):
    module('verify_production').verify_config_health(packaged_health)
    assert packaged_health['minimum_final_seconds'] == 20
    assert packaged_health['content_policy'] == 'reference_v1'


@pytest.mark.parametrize('field,value', [
    ('minimum_final_seconds', 120), ('content_policy', 'legacy120'),
    ('cover_styles', ['scene', 'photo', 'light', 'dark']),
    ('editorial_code_sha256', '0'*64),
])
def test_deploy_rejects_old_or_mismatched_runtime(packaged_health, field, value):
    with pytest.raises(SystemExit, match='does not match'):
        module('verify_production').verify_config_health({**packaged_health, field: value})


def test_packaging_rejects_missing_lazy_dependency(tmp_path, monkeypatch):
    package = module('package_code')
    monkeypatch.setattr(package, 'SOURCES', tuple(p for p in package.SOURCES
                                               if p != 'title_market_impression.py'))
    with pytest.raises(ValueError, match='missing runtime module title_market_impression'):
        package.build(tmp_path/'broken.zip')
