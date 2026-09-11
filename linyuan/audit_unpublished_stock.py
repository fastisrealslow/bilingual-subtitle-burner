"""Read actual delivery metadata and publication receipts before stock upgrades."""
import argparse
import collections
import json
import subprocess
import tempfile
import time
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parent/'fc'))
import index as fc
import caption_readability
import headline_policy


def api(path):
    return json.loads(subprocess.check_output(['gh','api',f'repos/{fc.REPO}/{path}']))


def delivery_meta(record):
    slug=record['slug']
    with tempfile.TemporaryDirectory() as tmp:
        cmd=['gh','release','download','deliver-'+slug,'--repo',fc.REPO,
             '--pattern',slug+'.meta.json','--dir',tmp]
        result=subprocess.run(cmd,capture_output=True,text=True)
        if result.returncode:
            subprocess.run(['gh','run','download',str(record['run_id']),'--repo',fc.REPO,
                            '--name','deliver-'+slug,'--dir',tmp],check=True,capture_output=True)
        files=list(Path(tmp).rglob('meta.json'))+list(Path(tmp).glob(slug+'.meta.json'))
        if not files:raise ValueError('Delivery metadata missing')
        value=json.loads(files[0].read_text())
        return value if isinstance(value,list) else [value]


def audit(state,inventory,loader=delivery_meta):
    latest={e['slug']:e for e in fc._latest_dispatches(state)}
    rows=[];errors=[];seen=[]
    for record in inventory['artifacts']:
        slug=record['slug'];entry=latest.get(slug,{})
        if entry.get('failed') or slug in fc.REVIEW_PAUSED_SLUGS:continue
        done=fc.processed_part_indices(entry)
        # Real successful receipt is authoritative even if progress lags behind.
        receipt=state.get('published',{}).get(slug,{})
        for part in receipt.get('parts',[]):
            if part.get('status') in ('published','skipped') and type(part.get('part_index')) is int:
                done.add(part['part_index'])
        pending=[p for p in record.get('parts',[]) if p['status']=='verified' and p['index'] not in done]
        if not pending:continue
        try:metas=loader(record)
        except Exception as exc:
            errors.append(dict(slug=slug,error=str(exc)));continue
        run=api(f"actions/runs/{record['run_id']}")
        for part in pending:
            i=part['index'];meta=metas[i]
            if meta.get('fingerprints',{}).get('sha256')!=part['sha256']:
                errors.append(dict(slug=slug,index=i,error='Inventory/delivery hash mismatch'));continue
            duplicate=fc.find_content_duplicate(meta.get('fingerprints') or {},state)
            pending_duplicate=None
            if not duplicate:
                for previous in seen:
                    reason=fc.fingerprint_duplicate(meta.get('fingerprints') or {},previous['fingerprints'])
                    if (not reason and meta.get('source_sha256')==previous['source_sha256']
                            and any(min(a['end'],b['end'])-max(a['start'],b['start'])>.3
                                    for a in meta.get('segments',[]) for b in previous['segments'])):
                        reason='同一母片的未发布区间重叠'
                    if reason:
                        pending_duplicate=dict(slug=previous['slug'],index=previous['index'],reason=reason)
                        break
            if not duplicate and not pending_duplicate:
                seen.append(dict(slug=slug,index=i,fingerprints=meta.get('fingerprints') or {},
                                 source_sha256=meta.get('source_sha256'),segments=meta.get('segments') or []))
            rows.append(dict(slug=slug,index=i,run_id=record['run_id'],run_number=run['run_number'],
                code_sha=run['head_sha'],created_at=run['created_at'],source_url=record.get('source_url'),
                artifact_id=record['artifact_id'],source_sha256=meta.get('source_sha256'),
                final=meta['final'],sha256=part['sha256'],title=meta.get('title'),
                duration_sec=meta.get('duration_sec'),render_mode=meta.get('render_mode'),
                segments=meta.get('segments'),caption_version=meta.get('subtitle_readability_version',0),
                packaging_version=meta.get('packaging_version',0),
                needs_render=int(meta.get('subtitle_readability_version') or 0)<caption_readability.VERSION,
                needs_packaging=int(meta.get('packaging_version') or 0)<headline_policy.VERSION,
                published_duplicate=duplicate,pending_duplicate=pending_duplicate))
    counts=collections.Counter('published_duplicate' if r['published_duplicate'] else
                               'pending_duplicate' if r['pending_duplicate'] else
                               'needs_render' if r['needs_render'] else
                               'needs_packaging' if r['needs_packaging'] else 'current' for r in rows)
    return dict(generated_at=int(time.time()),caption_version=caption_readability.VERSION,
                packaging_version=headline_policy.VERSION,inventory_updated_at=inventory['updated_at'],
                counts=dict(counts),rows=rows,errors=errors)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',default='stock-audit.json');args=parser.parse_args()
    state=fc.load_state()
    inventory=json.loads((Path(__file__).parent/'.automation/source_inventory.json').read_text())
    result=audit(state,inventory)
    Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(counts=result['counts'],errors=result['errors']),ensure_ascii=False))
    if result['errors']:raise SystemExit(1)
