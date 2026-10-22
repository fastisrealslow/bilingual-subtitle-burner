"""Replay the real exhausted connect timeout in memory; no dispatch or posts."""
import copy
import json
from pathlib import Path
import sys
from unittest.mock import patch

from check_adapter_recovery import read
sys.path.insert(0,str(Path(__file__).resolve().parent/'fc'))
import index as fc


def main():
    snapshot='b3fb8537991cb2dfcb32a7cd2ac7dc1782dd2c31'
    state=read(f'contents/linyuan/.automation/fc_state.json?ref={snapshot}')
    entry={e['slug']:e for e in fc._latest_dispatches(state)}['ly-story-71d80ec9f5']
    before=copy.deepcopy(entry);run=read('actions/runs/39745546456')
    assert run['conclusion']=='failure' and run['display_title'].endswith(' · '+entry['slug'])
    assert before['source_check_attempts']==2 and before['source_check_exhausted'] is True
    assert before['last_error']=='取源未完成：URLError: <urlopen error timed out>'
    writes=[]
    with patch.object(fc,'save_state',lambda _:writes.append('memory_only')),patch.object(fc,'log_event',lambda *a:None):
        assert fc._recover_changed_production_rule(state,entry,run)
        assert entry['source_check_attempts']==before['source_check_attempts']
        assert not entry['failed'] and 'source_check_run_id' not in entry
        entry['failed']=True;entry['source_check_run_id']=run['id']
        assert not fc._recover_changed_production_rule(state,entry,run)
    assert len(writes)==1
    result=dict(snapshot=snapshot,run_id=run['id'],source_url=entry['source_url'],
        recovery='download-netease-network-v2',passed=True,read_only=True,
        counters_reset=False,remote_state_written=False,dispatches=0,
        registered_stock=False,published=False,old_asr_imported=False)
    Path('netease-network-recovery-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
