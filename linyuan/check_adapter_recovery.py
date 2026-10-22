"""Read-only real failed-NetEase dispatch replay. Does not dispatch or publish."""
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
    repo=os.environ['GITHUB_REPOSITORY']
    return json.loads(subprocess.check_output(['gh','api',f'repos/{repo}/{path}',
        '-H','Accept: application/vnd.github.raw+json'],timeout=120))


def main():
    snapshot='c5b03c832b087320a35302cd155bdef336ce969c'
    state=read(f'contents/linyuan/.automation/fc_state.json?ref={snapshot}')
    latest={e['slug']:e for e in fc._latest_dispatches(state)}
    entry=latest['ly-story-71d80ec9f5'];before=copy.deepcopy(entry)
    run=read('actions/runs/39725792419')
    assert run['conclusion']=='failure' and run['display_title'].endswith(' · '+entry['slug'])
    writes=[]
    with patch.object(fc,'save_state',lambda st:writes.append('in_memory_only')),patch.object(fc,'log_event',lambda *a:None):
        assert fc._recover_changed_production_rule(state,entry,run)
        assert entry['source_check_attempts']==before['source_check_attempts']
        assert not entry['failed'] and 'source_check_run_id' not in entry
        entry['failed']=True
        assert not fc._recover_changed_production_rule(state,entry,run)
    assert len(writes)==1
    result=dict(snapshot=snapshot,slug=entry['slug'],old_run_id=run['id'],old_head_sha=run['head_sha'],
        source_url=entry['source_url'],recovery='download-netease-player-v1',
        passed=True,read_only=True,remote_state_written=False,dispatches=0,media_downloads=0,
        old_asr_imported=False,registered_stock=False,published=False)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
