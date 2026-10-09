"""Refuse a late deployment superseded by newer runtime/policy on main."""
from pathlib import Path
import subprocess
from package_code import SOURCES

ROOT=Path(__file__).resolve().parents[2]
PATHS=tuple('linyuan/'+name for name in SOURCES)+(
    'linyuan/fc/check_deploy_current.py',
    '.github/linyuan-release-policy.json',
    '.github/workflows/fc-production-deploy.yml',
    '.github/workflows/linyuan-title-claim-check.yml',
    '.github/workflows/linyuan-produce-cn.yml',
)


def assert_current(ref='FETCH_HEAD'):
    # The workflow must fetch main immediately before this check. A fetch/API
    # error must fail closed, never skip the guard or silently deploy anyway.
    changed=subprocess.check_output(['git','diff','--name-only','HEAD',ref,'--',*PATHS],
        cwd=ROOT,text=True,timeout=30).splitlines()
    if changed:
        raise SystemExit('部署已被主线新版本替代，拒绝覆盖当前函数：'+', '.join(changed))
    print('当前运行时与最新主线一致；自动台账提交不影响部署验收。')


if __name__=='__main__':
    assert_current()
