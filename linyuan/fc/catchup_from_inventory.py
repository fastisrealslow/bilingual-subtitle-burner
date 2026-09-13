"""One bounded catch-up request; a stable task ID prevents network replays."""
import io
import json
import os
from pathlib import Path
import time

import index as fc
from publish_fresh_six import prepare_async_tasks, state


def inventory_action(initial, stock):
    if not stock['inventory_fresh']:
        return None
    if fc.catchup_deficit(initial) and stock.get('publishable_now',stock['daily_mix_usable'])>0:
        return 'publish-catchup'
    if (stock['daily_mix_usable']<fc.TARGET_READY_RESERVE
            or stock.get('verified_landscape',0)<fc.TARGET_LANDSCAPE_RESERVE):
        return 'dispatch-source-inventory'
    return None


def run_inventory_task(action):
    from alibabacloud_fc20230330.client import Client
    from alibabacloud_fc20230330 import models as m
    from alibabacloud_tea_openapi import models as api
    from alibabacloud_tea_util import models as util
    client=Client(api.Config(access_key_id=os.environ['ALIYUN_AK'],access_key_secret=os.environ['ALIYUN_SK'],
        endpoint='fcv3.'+os.environ.get('FC_REGION','cn-hangzhou')+'.aliyuncs.com'))
    function=os.environ.get('FC_FUNCTION_NAME','fc-develop')
    runtime=util.RuntimeOptions(connect_timeout=10000,read_timeout=60000,autoretry=False)
    prepare_async_tasks(client,function,m,runtime)
    task_id='ly-stock-'+action+'-'+os.environ['GITHUB_RUN_ID']
    save_receipt(dict(task_id=task_id,action=action,status='Reconciling',outcome='pending'))
    return task_id, tracked_request(client,function,m,runtime,action,task_id)


def tracked_request(client,function,m,runtime,action,task_id,payload=None,wait_seconds=300):
    def query():
        try:
            return client.get_async_task_with_options(function,task_id,m.GetAsyncTaskRequest(qualifier='LATEST'),{},runtime).body
        except Exception as exc:
            if any(x in str(getattr(exc,'code','')) for x in ('NotFound','NotExist')):return None
            raise
    task=query()
    if task is None:
        try:
            response=client.invoke_function_with_options(function,m.InvokeFunctionRequest(qualifier='LATEST',
                body=io.BytesIO(json.dumps(payload or dict(triggerName=action)).encode())),
                m.InvokeFunctionHeaders(x_fc_invocation_type='Async',x_fc_async_task_id=task_id),runtime)
            if response.status_code != 202:
                raise RuntimeError('Tracked inventory task was not accepted')
        except Exception:
            # An interrupted response is ambiguous. Reconcile the SAME task ID;
            # never send another upload/dispatch while its outcome is unknown.
            if query() is None:
                raise
    deadline=time.monotonic()+wait_seconds
    while time.monotonic()<deadline:
        task=query()
        if task and task.status in {'Succeeded','Failed','Stopped','Expired','Invalid'}:break
        time.sleep(15)
    return task


def task_result(task):
    """FC may put the actual handler result in the terminal event's logTail."""
    if task is None:return None
    candidates=[getattr(task,'return_payload',None)]
    data=task.to_map() if hasattr(task,'to_map') else {}
    for event in reversed(data.get('events') or []):
        if event.get('status')!='Succeeded':continue
        try:
            detail=json.loads(event.get('eventDetail') or '{}')
            candidates.append(detail.get('logTail'))
        except (ValueError,TypeError,AttributeError):continue
    for raw in candidates:
        try:
            parsed=json.loads(raw or '')
            if isinstance(parsed,dict):return parsed
        except (ValueError,TypeError):pass
    return None


def save_receipt(result):
    Path('catchup-receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)


def main():
    initial=state()
    stock=fc.source_inventory(initial)
    action=inventory_action(initial,stock)
    if not action:
        save_receipt(dict(skipped=True,outcome='idle',inventory=stock))
        return
    task_id,task=run_inventory_task(action)
    result=dict(task_id=task_id,action=action,status=task.status if task else 'Unknown',
                return_payload=task.return_payload if task else None,
                function_result=task_result(task))
    result['outcome']='failed' if result['status'] in {'Failed','Stopped','Expired','Invalid'} else 'pending'
    save_receipt(result)
    if not task or task.status!='Succeeded':
        raise SystemExit('FC task '+result['status']+'; retain task ID and reconcile before retry')
    # Persist the task before reading remote state: a receipt-read interruption
    # must not erase the evidence needed to avoid a duplicate invocation.
    current=state()
    result['daily_publish']=current.get('daily_publish')
    new=[]
    for info in current.get('published',{}).values():
        for part in info.get('parts',[]):
            if part.get('status')=='published' and part.get('bvid'):
                new.append(part)
    result['outcome']='completed_without_new_publication'
    result['new_bvids']=[]
    if new:
        old_bvs={p.get('bvid') for info in initial.get('published',{}).values() for p in info.get('parts',[])}
        ids=[p['bvid'] for p in new if p['bvid'] not in old_bvs]
        if ids:
            result.update(new_bvids=sorted(set(ids)),outcome='publication_receipt_found')
            save_receipt(result)
            from bili_archive_status import archive_status
            result['creator_verification']=archive_status(ids,fc.OWNER_MID,os.environ['BILIBILI_COOKIES'])
    save_receipt(result)
    if action=='publish-catchup' and not result['new_bvids']:
        raise SystemExit('Publication task produced no new receipt; inspect function_result and retain task ID')
    if result.get('creator_verification') and result['creator_verification']['public_count']!=len(result['new_bvids']):
        raise SystemExit('Publication receipt exists but public visibility is pending; do not reupload')


if __name__=='__main__':main()
