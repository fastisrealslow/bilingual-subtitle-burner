"""Read-only replay of the actual unreported expired-ASR preparation failure."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent/'fc'))
import index as fc


def read(path):
    return json.loads(subprocess.check_output(['gh','api',
        f'repos/{os.environ["GITHUB_REPOSITORY"]}/{path}',
        '-H','Accept: application/vnd.github.raw+json'],timeout=120))


def main():
    snapshot='c5b03c832b087320a35302cd155bdef336ce969c'
    state=read(f'contents/linyuan/.automation/fc_state.json?ref={snapshot}')
    entry={e['slug']:e for e in fc._latest_dispatches(state)}['ly-1022-3c9631']
    before=copy.deepcopy(state)
    run=read('actions/runs/39727953226')
    artifacts=read('actions/runs/39727953226/artifacts')['artifacts']
    assert run['conclusion']=='failure' and run['display_title'].endswith(' · '+entry['slug'])
    writes=[]
    with patch.object(fc,'save_state',lambda st:writes.append('in_memory_only')),patch.object(fc,'log_event',lambda *a:None):
        assert fc._recover_preflight_failure(state,entry,run,artifacts)
        assert entry['failure_stage']=='asr-preparation-runtime'
        assert 'source_check_run_id' not in entry and not entry.get('failed')
        assert not fc._recover_preflight_failure(state,entry,run,artifacts)
    assert len(writes)==1 and state['published']==before['published']
    assert state['daily_publish']==before['daily_publish']
    print(json.dumps(dict(snapshot=snapshot,slug=entry['slug'],old_run_id=run['id'],
        old_head_sha=run['head_sha'],failure_stage=entry['failure_stage'],passed=True,
        read_only=True,remote_state_written=False,dispatches=0,media_downloads=0,
        old_asr_imported=False,registered_stock=False,published=False),ensure_ascii=False))


if __name__=='__main__':main()
