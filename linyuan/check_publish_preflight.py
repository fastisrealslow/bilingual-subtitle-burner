"""Read-only replay of the four actual delayed 14:00 selections on Oct22."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent/'fc'))
import index as fc

SNAPSHOT='c5b03c832b087320a35302cd155bdef336ce969c'
CASES={'ly-story-c551c25a26':1,'ly-story-645b2651be':1,
       'ly-story-8d58b73ad7':1,'ly-1015-5e4dd0':2}


def original(path):
    repo=os.environ['GITHUB_REPOSITORY']
    raw=subprocess.check_output(['gh','api',f'repos/{repo}/contents/{path}?ref={SNAPSHOT}',
        '-H','Accept: application/vnd.github.raw+json'],timeout=180)
    return json.loads(raw)


def replay():
    state=original('linyuan/.automation/fc_state.json')
    inventory=original('linyuan/.automation/source_inventory.json')
    before=copy.deepcopy(state);entries={e['slug']:e for e in fc._latest_dispatches(state)}
    rows=[]
    for slug,expected in CASES.items():
        entry=entries[slug]
        records=[r for r in inventory['artifacts'] if r['slug']==slug]
        if len(records)!=1:raise ValueError('Need exact actual inspected artifact '+slug)
        record=records[0];daily=state.get('daily_publish') or {}
        old=fc.inventory_part_index(entry,record['artifact_id'],records,daily,now=1792648800)
        if old!=expected:raise ValueError('Old selection does not match actual incident '+slug)
        part=next(p for p in record['parts'] if p['index']==old)
        reason=fc.inventory_publication_error(part,entry,state)
        if not reason:raise ValueError('Actual duplicate must still be blocked '+slug)
        duplicate=fc.find_content_duplicate(part.get('fingerprints') or {},
                                            fc.experiment.comparison_state(part,state))
        if not duplicate:raise ValueError('Actual original fingerprint match disappeared '+slug)
        new=fc.inventory_part_index(entry,record['artifact_id'],records,daily,now=1792648800,
                                    publication_state=state)
        if new==old:raise ValueError('Duplicate still selected before media fetch '+slug)
        if new is not None:
            target=next(p for p in record['parts'] if p['index']==new)
            if fc.inventory_publication_error(target,entry,state):
                raise ValueError('Replacement is not currently publication eligible '+slug)
        rows.append(dict(slug=slug,artifact=record['artifact_id'],old_index=old,
                         blocked_reason=reason,duplicate_bvid=duplicate['bvid'],
                         duplicate_reason=duplicate['reason'],new_index=new))
    if state!=before:raise ValueError('Read-only preflight mutated publication history or counters')
    result=dict(snapshot=SNAPSHOT,historical_clock=1792648800,cases=rows,passed=True,read_only=True,
                media_downloads=0,published=False,history_mutated=False)
    Path('publish-preflight-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))


def main():
    # Replay the immutable BEFORE-14:00 selection, not artifact availability
    # or topic cooling on a future date. No file or record becomes live stock.
    with patch.object(fc.time,'time',return_value=1792648800):replay()


if __name__=='__main__':main()
