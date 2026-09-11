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


def main():
    initial=state()
    stock=fc.source_inventory(initial)
    action=inventory_action(initial,stock)
    if not action:
        Path('catchup-receipt.json').write_text(json.dumps(dict(skipped=True,inventory=stock),ensure_ascii=False))
        return
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
    def query():
        try:
            return client.get_async_task_with_options(function,task_id,m.GetAsyncTaskRequest(qualifier='LATEST'),{},runtime).body
        except Exception as exc:
            if any(x in str(getattr(exc,'code','')) for x in ('NotFound','NotExist')):return None
            raise
    task=query()
    if task is None:
        client.invoke_function_with_options(function,m.InvokeFunctionRequest(qualifier='LATEST',
            body=io.BytesIO(json.dumps(dict(triggerName=action)).encode())),
            m.InvokeFunctionHeaders(x_fc_invocation_type='Async',x_fc_async_task_id=task_id),runtime)
    deadline=time.monotonic()+300
    while time.monotonic()<deadline:
        task=query()
        if task and task.status in {'Succeeded','Failed','Stopped','Expired','Invalid'}:break
        time.sleep(15)
    current=state()
    result=dict(task_id=task_id,action=action,status=task.status if task else 'Unknown',
                daily_publish=current.get('daily_publish'),return_payload=task.return_payload if task else None)
    new=[]
    for info in current.get('published',{}).values():
        for part in info.get('parts',[]):
            if part.get('status')=='published' and part.get('bvid'):
                new.append(part)
    if task and task.status=='Succeeded' and new:
        old_bvs={p.get('bvid') for info in initial.get('published',{}).values() for p in info.get('parts',[])}
        ids=[p['bvid'] for p in new if p['bvid'] not in old_bvs]
        if ids:
            from bili_archive_status import archive_status
            result['creator_verification']=archive_status(ids,fc.OWNER_MID,os.environ['BILIBILI_COOKIES'])
    Path('catchup-receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
