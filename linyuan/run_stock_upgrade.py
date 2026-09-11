"""Upgrade audited stock through normal production, then recheck before admission."""
import argparse
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).parent/'fc'))
import index as fc
import source_supply
import caption_readability
import headline_policy
from stock_upgrade_plan import PLAN

STATE='linyuan/.automation/fc_state.json'


def api(path,body=None):
    cmd=['gh','api',f'repos/{fc.REPO}/{path}']
    if body is not None:cmd+=['--method','PUT','--input','-']
    r=subprocess.run(cmd,input=None if body is None else json.dumps(body),text=True,capture_output=True,check=True)
    return json.loads(r.stdout) if r.stdout.strip() else {}


def mutate(change):
    for attempt in range(12):
        doc=api(f'contents/{STATE}?ref=main');state=json.loads(base64.b64decode(doc['content']))
        result=change(state)
        body=dict(message='chore(stock): track audited unpublished media upgrade',sha=doc['sha'],branch='main',
                  content=base64.b64encode((json.dumps(state,ensure_ascii=False,indent=2)+'\n').encode()).decode())
        try:api(f'contents/{STATE}',body);return result
        except subprocess.CalledProcessError as exc:
            if '409' not in exc.stderr and '422' not in exc.stderr:raise
            if attempt==11:raise
            time.sleep(2+attempt)


def claim(state,plan):
    new=next((e for e in state['dispatched'] if e['slug']==plan['new_slug']),None)
    if new and new.get('stock_upgrade_status')=='verified':return False
    if new and new.get('stock_upgrade_status') in ('rendering','held'):
        raise ValueError('This exact upgrade is already claimed; inspect its receipt before retrying')
    originals=[e for e in state['dispatched'] if e['slug']==plan['old_slug']]
    if not originals:raise ValueError('Original stock dispatch missing')
    origin=max(originals,key=lambda e:e.get('ts',0))
    if origin.get('source_url')!=plan['source_url']:raise ValueError('Source URL changed')
    # Retain files and receipts; a hold prevents old media being published while
    # its replacement is produced. It is not a source-quality rejection.
    for e in originals:
        e.update(failed=True,failure_stage='stock-upgrade',stock_upgrade_status='held',
                 stock_upgrade_to=plan['new_slug'])
    entry={k:copy.deepcopy(origin[k]) for k in ('key','video_id','source_url','asset_url','title','source',
           'production_rules_version','required_presentation_version') if k in origin}
    entry.update(slug=plan['new_slug'],ts=int(time.time()),delay_hours=0,repair_of=plan['old_slug'],
                 failed=True,failure_stage='stock-upgrade',stock_upgrade_status='rendering')
    state['dispatched'].append(entry)
    return True


def promote(state,plan,metas,run_id):
    entry=next(e for e in state['dispatched'] if e['slug']==plan['new_slug'])
    if entry.get('stock_upgrade_status')=='verified':return dict(already_verified=True)
    if entry.get('stock_upgrade_status')!='rendering':raise ValueError('Upgrade claim changed')
    accepted=[];skipped=[]
    entry.update(parts_total=len(metas),published_parts=0,processed_part_indices=[],stock_upgrade_run_id=run_id)
    for i,meta in enumerate(metas):
        dup=fc.find_content_duplicate(meta.get('fingerprints') or {},state)
        reason=fc.editorial.source_reuse_error(meta,plan['source_url'],state) or (dup or {}).get('reason')
        if reason:
            fc.mark_part_processed(entry,i);skipped.append(dict(index=i,reason=reason))
        else:accepted.append(dict(index=i,title=meta['title'],sha256=meta['fingerprints']['sha256']))
    entry.update(failed=not bool(accepted),stock_upgrade_status='verified' if accepted else 'held')
    if accepted:entry.pop('failure_stage',None)
    return dict(accepted=accepted,skipped=skipped)


def exclude_duplicates(state,rows):
    for row in rows:
        for e in state['dispatched']:
            if e['slug']==row['slug']:
                fc.mark_part_processed(e,row['index'])
                e.setdefault('stock_excluded_parts',{})[str(row['index'])]=row.get('published_duplicate') or row['pending_duplicate']
                e['parts_total']=max(int(e.get('parts_total') or 0),int(row.get('parts_total') or row['index']+1))
                if set(range(e['parts_total'])).issubset(fc.processed_part_indices(e)):
                    e.update(failed=True,stock_upgrade_status='duplicate',failure_stage='stock-dedup')


def run(plan):
    if not mutate(lambda state:claim(state,plan)):return dict(already_verified=True)
    deadline=time.time()+4*3600
    # Leave capacity for normal production and never flood the source queue.
    while time.time()<deadline:
        runs=api('actions/workflows/linyuan-produce-cn.yml/runs?per_page=100')['workflow_runs']
        active=sum(r['status'] in ('queued','in_progress') for r in runs)
        if active<6:break
        time.sleep(30)
    else:raise TimeoutError('Waiting for production capacity')
    args=['gh','workflow','run','linyuan-produce-cn.yml','--repo',fc.REPO,'--ref','main']
    inputs=dict(source=plan['source_url'],slug=plan['new_slug'],speaker='林园',
                occasion='未发布库存新版优化',source_platform='bilibili',auto_publish='false',
                include_full='false',prefer_live_video='true',recovery_run_id=str(plan['run_id']))
    for k,v in inputs.items():args+=['-f',f'{k}={v}']
    subprocess.run(args,check=True)
    result=None
    while time.time()<deadline:
        runs=api('actions/runs?event=workflow_dispatch&per_page=100')['workflow_runs']
        matches=[r for r in runs if r.get('display_title')=='中文源出片 · '+plan['new_slug']]
        if matches:
            result=max(matches,key=lambda r:r['id'])
            if result['status']=='completed':break
        time.sleep(30)
    else:raise TimeoutError('Production did not finish before monitoring deadline')
    receipt=dict(old_slug=plan['old_slug'],new_slug=plan['new_slug'],run_id=result['id'],
                 run_number=result['run_number'],conclusion=result['conclusion'],planned_parts=len(plan['parts']))
    artifacts=api(f"actions/runs/{result['id']}/artifacts")['artifacts']
    deliver=next((a for a in artifacts if a['name']=='deliver-'+plan['new_slug']),None)
    if not deliver:
        receipt.update(status='held',reason='没有通过质检的新成片；旧文件保留，暂停旧版投稿')
        mutate(lambda state:promote(state,plan,[],result['id']))
        return receipt
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(['gh','run','download',str(result['id']),'--repo',fc.REPO,
                        '--name',deliver['name'],'--dir',tmp],check=True)
        data=json.loads((Path(tmp)/'meta.json').read_text());metas=data if isinstance(data,list) else [data]
        for meta in metas:
            if (meta.get('subtitle_readability_version')!=caption_readability.VERSION
                    or meta.get('packaging_version')!=headline_policy.VERSION):
                raise ValueError('Replacement did not use current caption and packaging rules')
            error=source_supply.validate_part(meta,tmp)
            if error:raise ValueError(error)
            if meta.get('source_sha256')!=plan['source_sha256']:raise ValueError('Final source hash changed')
            if not any(meta.get('segments')==p['segments'] for p in plan['parts']):
                # Rendering retains start/end but may describe the same range differently.
                actual=[(s['start'],s['end']) for s in meta.get('segments',[])]
                if not any(actual==[(s['start'],s['end']) for s in p['segments']] for p in plan['parts']):
                    raise ValueError('Final source interval differs from audited stock')
        receipt.update(mutate(lambda state:promote(state,plan,metas,result['id'])))
        receipt.update(status='verified' if receipt.get('accepted') else 'held')
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--slug');parser.add_argument('--exclude-duplicates',action='store_true')
    args=parser.parse_args();manifest=json.loads(PLAN.read_text())
    if args.exclude_duplicates:
        mutate(lambda state:exclude_duplicates(state,manifest['excluded']));print('Excluded duplicate stock parts; original files and published receipts preserved')
    else:
        plan=next(p for p in manifest['batches'] if p['new_slug']==args.slug)
        try:receipt=run(plan)
        except Exception as exc:
            receipt=dict(old_slug=plan['old_slug'],new_slug=plan['new_slug'],status='error',reason=str(exc))
            raise
        finally:
            Path('stock-upgrade-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
            print(json.dumps(receipt,ensure_ascii=False))
