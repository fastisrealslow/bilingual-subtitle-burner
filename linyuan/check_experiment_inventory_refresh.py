"""Read-only stale-index counterfactual using the checkout's real stock.

No source/media/title approval, registry entry, publication or network write.
The captured state and inspection timestamps are never renewed or rewritten.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan/fc'),str(ROOT/'linyuan')]
import index as F
import publishing_experiment as E


def main():
    state_path=ROOT/'site/fc_state.json'
    inventory_path=ROOT/F.SOURCE_INVENTORY_KEY
    state=json.loads(state_path.read_text())
    inventory=json.loads(inventory_path.read_text())
    latest={e['slug']:e for e in F._latest_dispatches(state)}
    actual=[]
    for record in inventory['artifacts']:
        entry=latest.get(record['slug'])
        for part in record.get('parts',[]):
            if part.get('status')!='verified' or E.actual_profile(part)!=E.TREATMENT:continue
            error=(F.inventory_publication_error(part,entry,state) if entry else 'missing_dispatch')
            usable=bool(entry and not entry.get('failed') and not entry.get('uploading')
                and F.inventory_record_current(record,float(inventory['updated_at']))
                and int(part['index']) not in F.processed_part_indices(entry) and not error)
            actual.append(dict(slug=record['slug'],artifact_id=record['artifact_id'],
                index=part['index'],sha256=part['sha256'],resolution=part['resolution'],
                publication_error=error,usable_at_original_inspection=usable))
    eligible=[row for row in actual if row['usable_at_original_inspection']]
    assert eligible,'The real captured inventory must contain publishable treatment'
    # A future 14:00 AFTER the real snapshot's three-hour freshness window.
    # This is a counterfactual clock, not an observation or renewed inspection.
    expired=datetime.fromtimestamp(float(inventory['updated_at'])+3*3600+1,E.CST)
    local=expired.replace(hour=14,minute=0,second=0,microsecond=0)
    if local<expired:local+=timedelta(days=1)
    now=local.timestamp()
    assert E.assignment(now)['planned']==E.TREATMENT
    simulated=deepcopy(state)
    source_rows=deepcopy(simulated['dispatched'])
    receipts=deepcopy(simulated['published'])
    writes=[];requests=[]
    def api(method,path,body=None,**kwargs):
        if method=='GET' and path.startswith('/contents/'+F.SOURCE_INVENTORY_KEY):
            return json.dumps(inventory).encode()
        assert (method,path,body)==('POST',
            '/actions/workflows/linyuan-source-inventory.yml/dispatches',{'ref':'main'})
        requests.append(dict(method=method,path=path,body=body,intercepted=True))
    with patch.object(F.time,'time',return_value=now), \
         patch.object(F,'load_state',return_value=simulated), \
         patch.object(F,'save_state',side_effect=lambda st:writes.append(deepcopy(st))), \
         patch.object(F,'_collect_source_rejections',return_value=0), \
         patch.object(F,'log_event'),patch.object(F,'gh',side_effect=api):
        assert not F.source_inventory(simulated,inventory)['inventory_fresh']
        result=F.publish_handler()
    assert result==dict(published=0,inventory_unavailable=1,inventory_refresh_requested=1)
    assert simulated['dispatched']==source_rows and simulated['published']==receipts
    assert len(requests)==1 and len(writes)==1
    report=dict(passed=True,kind='read_only_stale_index_counterfactual',
        code_head=os.environ.get('GITHUB_SHA'),
        state_file_sha256=hashlib.sha256(state_path.read_bytes()).hexdigest(),
        inventory_file_sha256=hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
        actual_inspected_treatment=actual,actual_publishable_treatment=eligible,
        original_inventory_updated_at=inventory['updated_at'],
        simulated_clock=local.isoformat(),result=result,intercepted_requests=requests,
        original_source_rows_and_receipts_unchanged=True,
        inspection_timestamps_renewed=False,media_approved=False,
        sample_registered=False,stock_registered=False,network_writes=False,published=False)
    target=ROOT/'experiment-inventory-refresh-verification.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
