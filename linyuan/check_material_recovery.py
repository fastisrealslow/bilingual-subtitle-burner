"""Read-only replay: unrelated workflow bookkeeping cannot repair ASR text."""
import copy
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

from check_adapter_recovery import read
import index as fc


def raw(path,ref):
    return subprocess.check_output(['gh','api',
        f'repos/{os.environ["GITHUB_REPOSITORY"]}/contents/{path}?ref={ref}',
        '-H','Accept: application/vnd.github.raw+json'],timeout=120).decode()


def main():
    snapshot='8517e18ba5113feab87f260d51d965cefdb29ec8'
    bookkeeping='10ef724b747fbb80ae8e56438e8872a13e98b421'
    state=read(f'contents/linyuan/.automation/fc_state.json?ref={snapshot}')
    entry={e['slug']:e for e in fc._latest_dispatches(state)}['ly-story-3fa68cb6f9']
    before=copy.deepcopy(entry);run=read('actions/runs/39393321032')
    assert run['conclusion']=='failure' and run['display_title'].endswith(' · '+entry['slug'])
    assert entry['failed'] and entry['failure_evidence']['run_id']==run['id']
    assert entry['last_error'].startswith('原始ASR存在影响理解的疑点，禁止猜改后发布')
    assert not entry['automatic_rule_recoveries']
    audit_old=raw('linyuan/transcript_audit.py',run['head_sha'])
    assert audit_old==raw('linyuan/transcript_audit.py',bookkeeping)
    path='.github/workflows/linyuan-produce-cn.yml'
    workflow_old=raw(path,run['head_sha']);workflow_new=raw(path,bookkeeping)
    diff=''.join(difflib.unified_diff(workflow_old.splitlines(True),workflow_new.splitlines(True)))
    changes=[line for line in diff.splitlines() if line[:1] in ('+','-') and not line.startswith(('+++','---'))]
    assert changes==['+        id: asr_prepare','+          PRODUCTION_JOB_STATUS: ${{ job.status }}']
    assert workflow_old!=workflow_new  # The previous filename-only rule reopened it.
    writes=[]
    with patch.object(fc,'gh',lambda method,resource:dict(files=[dict(filename=path,patch=diff)])),\
            patch.object(fc,'save_state',lambda _:writes.append('memory_only')),\
            patch.object(fc,'log_event',lambda *a:None):
        assert not fc._recover_changed_production_rule(state,entry,run)
    assert entry==before and not writes
    result=dict(snapshot=snapshot,run_id=run['id'],bookkeeping_commit=bookkeeping,
        audit_sha256=hashlib.sha256(audit_old.encode()).hexdigest(),workflow_changes=changes,
        passed=True,read_only=True,counters_reset=False,history_rewritten=False,
        remote_state_written=False,dispatches=0,registered_stock=False,published=False)
    Path('material-recovery-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
