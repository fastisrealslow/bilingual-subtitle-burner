"""Reframe already accepted, unpublished stock without repeating source ASR."""
import argparse
import json
import os
import subprocess
from pathlib import Path

import batch_delivery
import landscape
import run_stock_upgrade as stock
import source_supply

SLUGS = {'ly-0910-interview-clean-v4-wide0911', 'ly-0909-2e376f-wide0911'}
SUPERSEDED_RUN = 34583060777


def reserve(state, plan, run_id):
    entry=next((e for e in state['dispatched'] if e['slug']==plan['new_slug']),None)
    if entry and entry.get('stock_upgrade_status')=='verified':
        raise ValueError('横版已验收入库，禁止重复重做')
    if entry:
        if (entry.get('stock_upgrade_status')!='rendering'
                or entry.get('repair_of')!=plan['old_slug']
                or entry.get('landscape_run_id') not in (None,run_id)):
            raise ValueError('横版库存认领状态变化，拒绝覆盖其他任务')
    else:
        stock.claim(state,plan)
        entry=next(e for e in state['dispatched'] if e['slug']==plan['new_slug'])
    entry['landscape_run_id']=run_id


def render(plan, directory, run_id):
    # This explicit migration only resumes the cancelled queue-waiting batch.
    previous=stock.api(f'actions/runs/{SUPERSEDED_RUN}')
    if previous['status']!='completed':
        raise ValueError('原试产任务仍运行，拒绝并发认领')
    runs=stock.api('actions/workflows/linyuan-produce-cn.yml/runs?per_page=100')['workflow_runs']
    if any(r.get('display_title')=='中文源出片 · '+plan['new_slug'] for r in runs):
        raise ValueError('该横版已派发普通生产，不能重复生产')
    stock.mutate(lambda state:reserve(state,plan,run_id))
    directory.mkdir(parents=True,exist_ok=True)
    subprocess.run(['gh','run','download',str(plan['run_id']),'--repo',stock.fc.REPO,
                    '--name','deliver-'+plan['old_slug'],'--dir',str(directory)],check=True)
    data=json.loads((directory/'meta.json').read_text())
    rows=data if isinstance(data,list) else [data]
    if len(rows)!=1:raise ValueError('首批横版必须为一条已验收连续片段')
    meta=rows[0]
    error=source_supply.validate_part(meta,directory)
    if error:raise ValueError('原成片验收失败：'+error)
    expected=plan['parts'][0]
    if (meta.get('source_sha256')!=plan['source_sha256']
            or [(s['start'],s['end']) for s in meta['segments']]!=[(s['start'],s['end']) for s in expected['segments']]
            or meta['title']!=expected['title']):
        raise ValueError('原成片与已复核选段不一致')
    result=landscape.reframe(meta,directory,directory/'_tmp'/'landscape')
    result['landscape_reframe'].update(original_slug=plan['old_slug'],original_run_id=plan['run_id'])
    error=source_supply.validate_part(result,directory)
    if error:raise ValueError('横版实际成片验收失败：'+error)
    batch_delivery.write_json(directory/'meta.json',result)
    batch_delivery.archive_accepted(directory,plan['new_slug'])
    print(json.dumps(dict(status='rendered',title=result['title'],resolution=result['resolution']),ensure_ascii=False))


def admit(plan,directory,run_id):
    artifacts=stock.api(f'actions/runs/{run_id}/artifacts')['artifacts']
    if not any(a['name']=='deliver-'+plan['new_slug'] and not a['expired'] for a in artifacts):
        raise ValueError('横版交付文件尚未上传，不能入库')
    meta=json.loads((directory/'meta.json').read_text())
    error=source_supply.validate_part(meta,directory)
    if error:raise ValueError(error)
    def promote(state):
        entry=next(e for e in state['dispatched'] if e['slug']==plan['new_slug'])
        if entry.get('landscape_run_id')!=run_id:raise ValueError('横版任务认领变化')
        return stock.promote(state,plan,[meta],run_id)
    receipt=stock.mutate(promote)
    receipt.update(old_slug=plan['old_slug'],new_slug=plan['new_slug'],run_id=run_id,
                   status='verified' if receipt.get('accepted') else 'held')
    batch_delivery.write_json(Path('stock-upgrade-receipt.json'),receipt)
    print(json.dumps(receipt,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--slug',required=True,choices=sorted(SLUGS))
    parser.add_argument('--admit',action='store_true');args=parser.parse_args()
    plan=next(p for p in json.loads(stock.PLAN.read_text())['batches'] if p['new_slug']==args.slug)
    directory=Path('linyuan/deliver')/args.slug
    (admit if args.admit else render)(plan,directory,int(os.environ['GITHUB_RUN_ID']))
