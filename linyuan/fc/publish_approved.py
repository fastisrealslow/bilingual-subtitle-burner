"""Publish one exact approved file through the existing FC lock and tracked task."""
import hashlib
import json
import os
from pathlib import Path
import re
from catchup_from_inventory import tracked_request
from publish_fresh_six import prepare_async_tasks, state


def exact_receipt(st, request):
    for part in (st.get('published', {}).get(request['slug']) or {}).get('parts', []):
        if (part.get('status') == 'published'
                and re.fullmatch(r'BV\w{10}', str(part.get('bvid') or ''))
                and (part.get('fingerprints') or {}).get('sha256') == request['expected_sha256']
                and part.get('makeup_slot') == request.get('makeup_slot')):
            return part
    return None


def main():
    from alibabacloud_fc20230330.client import Client
    from alibabacloud_fc20230330 import models as m
    from alibabacloud_tea_openapi import models as api
    from alibabacloud_tea_util import models as util
    request=json.loads(Path('linyuan/.automation/publish_request.json').read_text())
    required={'slug','artifact_id','source_url','title','expected_sha256','request_id'}
    if required-request.keys():
        raise SystemExit('Approved request missing required fields')
    receipt=exact_receipt(state(),request)
    task_id='ly-approved-'+hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()[:32]
    report=dict(task_id=task_id,request=request,receipt=receipt,status='reconciling')
    path=Path('approved-publish-receipt.json')
    def save():
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False),flush=True)
    save()
    if not receipt:
        client=Client(api.Config(access_key_id=os.environ['ALIYUN_AK'],access_key_secret=os.environ['ALIYUN_SK'],
            endpoint='fcv3.'+os.environ.get('FC_REGION','cn-hangzhou')+'.aliyuncs.com'))
        function=os.environ.get('FC_FUNCTION_NAME','fc-develop')
        runtime=util.RuntimeOptions(connect_timeout=10000,read_timeout=60000,autoretry=False)
        prepare_async_tasks(client,function,m,runtime)
        payload=dict(triggerName='publish-batch',batch_slug=request['slug'],batch_remaining=1,
            artifact_id=int(request['artifact_id']),source_url=request['source_url'],
            title=request['title'],expected_sha256=request['expected_sha256'])
        if request.get('makeup_slot'):
            payload['makeup_slot']=request['makeup_slot']
        task=tracked_request(client,function,m,runtime,'publish-batch',task_id,
                             payload=payload,wait_seconds=85*60)
        report.update(status=task.status if task else 'Unknown',return_payload=task.return_payload if task else None)
        save()
        receipt=exact_receipt(state(),request)
        report['receipt']=receipt
        save()
        if not receipt:
            raise SystemExit('No exact BVID/hash/slot receipt; retain task ID and do not blindly replay')
    report['status']='verified_receipt'
    save()
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as summary:
        summary.write('已核验投稿回执：'+request.get('makeup_slot','')+'\n\n'
                      'https://www.bilibili.com/video/'+receipt['bvid']+'\n')


if __name__=='__main__':main()
