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
    assert '/etc/apt/apt-mirrors.txt /etc/apt/sources.list.d/ubuntu.sources' in text
    assert 'https://archive.ubuntu.com/ubuntu' in text
    assert 'Acquire::Retries=2' in text
    assert 'Acquire::http::Timeout=30' in text
    assert 'Acquire::https::Timeout=30' in text
    assert text.count('timeout --signal=TERM --kill-after=15s 600s apt-get') == 2
    assert 'ffmpeg fonts-noto-cjk poppler-utils libgl1 libglib2.0-0 zstd' in text
    assert "fc-match -f '%{file}' 'Noto Sans CJK SC:lang=zh-cn'" in text
    assert 'test -f "$font_path"' in text
    assert 'allow-unauthenticated' not in text
    assert 'trusted=yes' not in text


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
