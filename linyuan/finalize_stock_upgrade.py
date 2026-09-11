"""Close this audited stock migration and stop its superseded empty batch."""
import base64
import json
from pathlib import Path
import subprocess
import time
from run_stock_upgrade import api,mutate,promote,STATE,PLAN

manifest=json.loads(PLAN.read_text())
plans=[b for b in manifest['batches'] if b['parts']]
deadline=time.time()+30*60
while time.time()<deadline:
    doc=api(f'contents/{STATE}?ref=main')
    state=json.loads(base64.b64decode(doc['content']))
    latest={e['slug']:e for e in state['dispatched']}
    pending=[p for p in plans if latest.get(p['new_slug'],{}).get('stock_upgrade_status') not in ('verified','held')]
    if not pending:break
    runs=api('actions/workflows/linyuan-produce-cn.yml/runs?event=workflow_dispatch&per_page=100')['workflow_runs']
    for plan in pending:
        matches=[r for r in runs if r.get('display_title')=='中文源出片 · '+plan['new_slug']]
        if not matches:continue
        run=max(matches,key=lambda r:r['id'])
        if run['status']!='completed':continue
        artifacts=api(f"actions/runs/{run['id']}/artifacts")['artifacts']
        if not any(a['name']=='deliver-'+plan['new_slug'] for a in artifacts):
            # Older workers recorded this result only in their artifact receipt.
            # Reflect the terminal hold without allowing a rejected clip to post.
            mutate(lambda s:promote(s,plan,[],run['id']))
    print('Waiting for '+', '.join(p['new_slug'] for p in pending),flush=True)
    time.sleep(30)
else:raise TimeoutError('Migration still has unfinished production; do not claim a final count')

# The two #650 ranges were removed after raw-ASR review. Their original
# matrix runner can be waiting in apt; never cancel any other unfinished job.
run_id=34571733872
jobs=api(f'actions/runs/{run_id}/jobs?per_page=100')['jobs']
active=[j for j in jobs if j['status']!='completed']
if active and all(j['name']=='upgrade (ly-0909-2d63c1-u0911)' for j in active):
    subprocess.run(['gh','api','--method','POST',f'repos/fastisrealslow/bilingual-subtitle-burner/actions/runs/{run_id}/cancel'],check=True)

results=[dict(old_run_number=p['old_run_number'],slug=p['new_slug'],
              planned_parts=len(p['parts']),status=latest[p['new_slug']]['stock_upgrade_status'],
              verified_parts=(int(latest[p['new_slug']].get('parts_total') or 0)-len(latest[p['new_slug']].get('processed_part_indices') or []))
                  if latest[p['new_slug']]['stock_upgrade_status']=='verified' else 0)
         for p in plans]
receipt=dict(snapshot_gross=34,current_at_snapshot=11,excluded_duplicates=len(manifest['excluded']),
             held_source_parts=len(manifest['editorial_holds']),planned_upgrade_parts=sum(len(p['parts']) for p in plans),
             results=results,generated_at=int(time.time()))
Path('stock-upgrade-final-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(receipt,ensure_ascii=False))
