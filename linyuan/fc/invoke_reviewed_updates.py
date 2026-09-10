"""Run approved archive edits as tracked tasks, then verify their return receipt."""
import hashlib
import io
import json
import os
from pathlib import Path
import time


def main():
    from alibabacloud_fc20230330.client import Client
    from alibabacloud_fc20230330 import models as m
    from alibabacloud_tea_openapi import models as api
    from alibabacloud_tea_util import models as util
    client = Client(api.Config(access_key_id=os.environ['ALIYUN_AK'],
        access_key_secret=os.environ['ALIYUN_SK'],
        endpoint='fcv3.' + os.environ.get('FC_REGION','cn-hangzhou') + '.aliyuncs.com'))
    function = os.environ.get('FC_FUNCTION_NAME','fc-develop')
    runtime = util.RuntimeOptions(connect_timeout=20000, read_timeout=60000, autoretry=False)
    old = client.get_async_invoke_config_with_options(function,
        m.GetAsyncInvokeConfigRequest(qualifier='LATEST'), {}, runtime).body
    if not old.async_task or old.max_async_retry_attempts != 0:
        client.put_async_invoke_config_with_options(function,
            m.PutAsyncInvokeConfigRequest(qualifier='LATEST', body=m.PutAsyncInvokeConfigInput(
                async_task=True, max_async_retry_attempts=0,
                destination_config=old.destination_config,
                max_async_event_age_in_seconds=old.max_async_event_age_in_seconds)), {}, runtime)
    version = hashlib.sha256(Path('linyuan/fc/reviewed_updates.py').read_bytes()).hexdigest()[:16]
    deadline = time.monotonic() + 900
    for attempt in range(30):
        task_id = f'reviewed-0910-{version}-{attempt}'
        def query():
            try:
                return client.get_async_task_with_options(function,task_id,
                    m.GetAsyncTaskRequest(qualifier='LATEST'),{},runtime).body
            except Exception as exc:
                if any(x in str(getattr(exc,'code','')) for x in ('NotFound','NotExist')):
                    return None
                raise
        task = query()
        if task is None:
            try:
                response = client.invoke_function_with_options(function,
                    m.InvokeFunctionRequest(qualifier='LATEST',body=io.BytesIO(
                        b'{"triggerName":"apply-reviewed-updates-0910"}')),
                    m.InvokeFunctionHeaders(x_fc_invocation_type='Async',x_fc_async_task_id=task_id),runtime)
                if response.status_code != 202:
                    raise RuntimeError('Tracked update task was not accepted')
            except Exception:
                if query() is None:
                    raise
        print(json.dumps({'task_id':task_id,'status':'accepted-or-existing'}),flush=True)
        while time.monotonic() < deadline:
            task = query()
            if task and task.status in ('Succeeded','Failed','Stopped','Expired','Invalid'):
                break
            time.sleep(10)
        else:
            raise SystemExit('Tracked update still unresolved; retain task ID')
        try:
            result = json.loads(task.return_payload or '{}')
        except (ValueError, TypeError):
            result = {'error':'Invalid task return payload'}
        record = {'task_id':task_id,'task_status':task.status,'result':result}
        Path('reviewed-updates-verification.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
        print(json.dumps(record,ensure_ascii=False),flush=True)
        if task.status == 'Succeeded' and result.get('status') in ('verified','already_verified'):
            return
        if task.status == 'Succeeded' and result.get('publisher_busy') and time.monotonic() < deadline:
            time.sleep(20)
            continue
        raise SystemExit('Tracked update completed without all three verified receipts')
    raise SystemExit('Publisher remained busy; no untracked invocation was sent')


if __name__ == '__main__': main()
