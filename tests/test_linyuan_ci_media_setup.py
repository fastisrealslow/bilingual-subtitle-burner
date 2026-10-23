from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_production_setup_has_independent_budget_and_shared_real_check():
    workflow = yaml.safe_load((ROOT / '.github/workflows/linyuan-produce-cn.yml').read_text())
    step = next(s for s in workflow['jobs']['produce']['steps']
                if s.get('name') == '安装 ffmpeg 与中文字体')
    assert step['timeout-minutes'] == 15
    assert step['run'] == 'bash ci_install_media_tools.sh'
    diagnostic = yaml.safe_load((ROOT / '.github/workflows/linyuan-production-setup-check.yml').read_text())
    assert diagnostic['permissions'] == {'contents': 'read'}
    assert diagnostic['jobs']['setup']['timeout-minutes'] == 15
    assert diagnostic['jobs']['setup']['steps'][-1]['run'] == 'bash linyuan/ci_install_media_tools.sh'


def test_actual_install_keeps_packages_signatures_and_bounded_network():
    script = ROOT / 'linyuan/ci_install_media_tools.sh'
    subprocess.run(['bash', '-n', str(script)], check=True)
    text = script.read_text()
    assert 'set -euo pipefail' in text
    assert 'cp /etc/apt/sources.list.d/ubuntu.sources "$task_apt_sources/ubuntu.sources"' in text
    assert 'https://archive.ubuntu.com/ubuntu' in text
    assert 'Acquire::Retries=2' in text
    assert 'Acquire::http::Timeout=30' in text
    assert 'Acquire::https::Timeout=30' in text
    assert text.count('timeout --signal=TERM --kill-after=15s 180s apt-get') == 2
    assert text.count('timeout --signal=TERM --kill-after=15s 420s apt-get') == 1
    assert 'Dir::Etc::sourcelist=/dev/null' in text
    assert 'Dir::Etc::sourceparts=$task_apt_sources' in text
    assert 'Acquire::ForceIPv4=true' in text
    assert 'APT::Update::Error-Mode=any' in text
    assert 'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg' in text
    assert 'ffmpeg fonts-noto-cjk poppler-utils libgl1 libglib2.0-0 zstd' in text
    assert "fc-match -f '%{file}' 'Noto Sans CJK SC:lang=zh-cn'" in text
    assert 'test -f "$font_path"' in text
    assert 'allow-unauthenticated' not in text
    assert 'trusted=yes' not in text


def test_complete_existing_tools_do_not_require_apt_or_waive_font_checks(tmp_path):
    import os
    directory=tmp_path/'bin';directory.mkdir()
    font=tmp_path/'NotoSansCJK.ttf';font.write_bytes(b'protocol fixture, not a font-quality certificate')
    marker=tmp_path/'unexpected-apt'
    scripts={
        'ffmpeg':"printf ' ... subtitles V->V\n'",
        'ffprobe':"printf 'ffprobe version test\n'",
        'pdftoppm':':','pdftotext':':','zstd':':','fc-cache':':',
        'ldconfig':"printf 'libGL.so.1 lib\nlibglib-2.0.so.0 lib\n'",
        'fc-match':f"printf '%s' '{font}'",
        'sudo':f"printf 'called' > '{marker}'; exit 90",
    }
    for name,body in scripts.items():
        path=directory/name;path.write_text('#!/usr/bin/env bash\n'+body+'\n');path.chmod(0o755)
    env={**os.environ,'PATH':str(directory)+os.pathsep+os.environ['PATH']}
    run=subprocess.run(['bash',str(ROOT/'linyuan/ci_install_media_tools.sh')],env=env,
                       text=True,capture_output=True,timeout=15)
    assert run.returncode==0 and 'Verified existing media tools' in run.stdout
    assert not marker.exists()
    (directory/'fc-match').write_text("#!/usr/bin/env bash\nprintf '%s' '/tmp/not-a-noto-font.ttf'\n")
    run=subprocess.run(['bash',str(ROOT/'linyuan/ci_install_media_tools.sh')],env=env,
                       text=True,capture_output=True,timeout=15)
    assert run.returncode!=0 and 'Verified existing media tools' not in run.stdout


def test_full_linux_regression_uses_the_same_verified_media_setup():
    doc=yaml.safe_load((ROOT/'.github/workflows/linyuan-local-regression.yml').read_text())
    body=next(s['run'] for s in doc['jobs']['offline']['steps'] if s.get('name')=='安装开发依赖与中文字体')
    assert 'bash linyuan/ci_install_media_tools.sh' in body and 'apt-get' not in body
    setup=yaml.safe_load((ROOT/'.github/workflows/linyuan-production-setup-check.yml').read_text())
    assert setup['jobs']['setup']['strategy']['matrix']['replica']==[1,2]


def test_every_actual_runner_regression_caller_installs_yaml_before_tests():
    # Full developer CI already has PyYAML; that does not prove the smaller
    # production inventory/dispatch environments can import shared tests.
    for name in ('linyuan-source-inventory.yml', 'linyuan-dispatch-runner.yml'):
        doc = yaml.safe_load((ROOT / '.github/workflows' / name).read_text())
        callers = 0
        for job in doc['jobs'].values():
            previous = ''
            for step in job['steps']:
                body = step.get('run', '')
                if 'tests/test_linyuan_runner_dispatch.py' in body:
                    before_test = previous + body.split('tests/test_linyuan_runner_dispatch.py')[0]
                    assert any('pip install' in line and 'pyyaml' in line.lower()
                               for line in before_test.splitlines()), name
                    callers += 1
                previous += body + '\n'
        assert callers, name


def test_inventory_uses_same_real_bounded_setup_before_validation():
    doc = yaml.safe_load((ROOT / '.github/workflows/linyuan-source-inventory.yml').read_text())
    steps = doc['jobs']['inventory']['steps']
    setup_index = next(i for i, step in enumerate(steps)
                       if step.get('run') == 'bash linyuan/ci_install_media_tools.sh')
    validation_index = next(i for i, step in enumerate(steps)
                            if step.get('name') == '校验实际 MP4、字幕证据和剩余库存')
    assert setup_index < validation_index
    assert steps[setup_index]['timeout-minutes'] == 15
    assert 'apt-get' not in steps[validation_index]['run']
    assert doc['jobs']['inventory']['timeout-minutes'] == 35
    assert doc['concurrency'] == {'group': 'linyuan-source-inventory', 'cancel-in-progress': False}


def test_status_dependencies_exist_before_fallible_media_setup():
    doc = yaml.safe_load((ROOT / '.github/workflows/linyuan-source-inventory.yml').read_text())
    steps = doc['jobs']['inventory']['steps']
    runtime_index = next(i for i, s in enumerate(steps) if s.get('id') == 'python_runtime')
    media_index = next(i for i, s in enumerate(steps) if s.get('id') == 'media_setup')
    assert runtime_index < media_index
    assert steps[runtime_index]['timeout-minutes'] == 5
    assert 'runtime-requirements.txt' in steps[runtime_index]['run']
    status = next(s for s in steps if s.get('name') == '回读真实运行状态并更新日志页')
    assert status['if'] == "always() && steps.python_runtime.outcome == 'success'"
    receipt = next(s for s in steps if s.get('run') == 'python linyuan/inventory_preparation_failure.py')
    assert receipt['if'].startswith('always() &&')
    assert "steps.python_runtime.outcome != 'success'" in receipt['if']
    assert "steps.media_setup.outcome != 'success'" in receipt['if']
    assert 'continue-on-error' not in steps[media_index]


def test_setup_failure_receipt_does_not_change_inventory_or_import_runtime(tmp_path):
    import json
    import os
    import sys
    script = ROOT / 'linyuan/inventory_preparation_failure.py'
    inventory = tmp_path / 'linyuan/.automation/source_inventory.json'
    inventory.parent.mkdir(parents=True)
    inventory.write_text(json.dumps({'updated_at': 123, 'inventory': {'verified_live': 13}}))
    before = inventory.read_bytes()
    for python_result, media_result, stage in [('success', 'failure', 'media_tools'),
                                               ('failure', 'skipped', 'python_runtime')]:
        # -I -S deliberately removes all third-party packages, including jieba.
        subprocess.run([sys.executable, '-I', '-S', str(script)], cwd=tmp_path,
                       env={**os.environ, 'INVENTORY_PYTHON_OUTCOME': python_result,
                            'INVENTORY_MEDIA_OUTCOME': media_result}, check=True, capture_output=True)
        receipt = json.loads((tmp_path / 'inventory-preparation-failure.json').read_text())
        assert receipt['stage'] == stage
        assert receipt['source_rejected'] is False
        assert receipt['inventory_refreshed'] is False
        assert receipt['previous_inventory_updated_at'] == 123
        assert inventory.read_bytes() == before


def test_monitor_reuses_working_tools_and_preserves_its_source_schedule(tmp_path):
    import os
    doc=yaml.safe_load((ROOT/'.github/workflows/linyuan-monitor.yml').read_text())
    step=next(s for s in doc['jobs']['monitor']['steps'] if s.get('name')=='安装系统依赖')
    assert step['timeout-minutes']==15 and 'apt-get' not in step['run']
    assert doc['jobs']['monitor']['timeout-minutes']==60
    assert doc['concurrency']==dict(group='monitor',**{'cancel-in-progress':False})
    assert (doc.get('on') or doc.get(True))['schedule']==[dict(cron='17 * * * *')]
    directory=tmp_path/'bin';directory.mkdir();marker=tmp_path/'unexpected-installer';pip=tmp_path/'pip-args'
    scripts={**{k:':' for k in ['ffmpeg','ffprobe','pdftotext']},
        'bash':f"printf called > '{marker}'; exit 90",
        'python':f"printf '%s\\n' \"$@\" > '{pip}'"}
    for name,body in scripts.items():
        path=directory/name;path.write_text('#!/bin/sh\n'+body+'\n');path.chmod(0o755)
    r=subprocess.run(['/bin/bash','-c',step['run']],cwd=ROOT/'linyuan',env={**os.environ,'PATH':str(directory)},
        text=True,capture_output=True,timeout=10)
    assert r.returncode==0,r.stderr
    assert not marker.exists() and '--retries' in pip.read_text() and '--timeout' in pip.read_text()


def test_monitor_missing_tools_use_the_existing_installer_before_pip(tmp_path):
    import os
    doc=yaml.safe_load((ROOT/'.github/workflows/linyuan-monitor.yml').read_text())
    step=next(s for s in doc['jobs']['monitor']['steps'] if s.get('name')=='安装系统依赖')
    directory=tmp_path/'bin';directory.mkdir();marker=tmp_path/'installer';pip=tmp_path/'pip'
    scripts={'ffmpeg':':','ffprobe':':',
        'bash':f"printf '%s' \"$1\" > '{marker}'; printf '#!/bin/sh\\nexit 0\\n' > '{directory}/pdftotext'; /bin/chmod +x '{directory}/pdftotext'",
        'python':f"test -f '{marker}' && printf called > '{pip}'"}
    for name,body in scripts.items():
        path=directory/name;path.write_text('#!/bin/sh\n'+body+'\n');path.chmod(0o755)
    r=subprocess.run(['/bin/bash','-c',step['run']],cwd=ROOT/'linyuan',env={**os.environ,'PATH':str(directory)},
        text=True,capture_output=True,timeout=10)
    assert r.returncode==0,r.stderr
    assert marker.read_text()=='ci_install_media_tools.sh' and pip.exists()
