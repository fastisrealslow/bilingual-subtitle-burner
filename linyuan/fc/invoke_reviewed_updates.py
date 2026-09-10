"""Invoke the accepted in-place edits; read back the persisted creator receipts."""
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
    deadline = time.monotonic() + 900
    while True:
        try:
            response = client.invoke_function_with_options(os.environ.get('FC_FUNCTION_NAME','fc-develop'),
                m.InvokeFunctionRequest(qualifier='LATEST', body=io.BytesIO(json.dumps(
                    {'triggerName':'apply-reviewed-updates-0910'}).encode())),
                m.InvokeFunctionHeaders(x_fc_invocation_type='Sync'),
                util.RuntimeOptions(connect_timeout=10000, read_timeout=600000, autoretry=False))
        except Exception as exc:
            # A gateway 503 can arrive while FC is still running. The FC lease
            # and persisted edit receipt reconcile the next call without replay.
            if getattr(exc, 'code', '') in ('ServiceUnavailable', 'RequestTimeout') and time.monotonic() < deadline:
                print(json.dumps({'status':'awaiting_receipt','gateway_code':exc.code}),flush=True)
                time.sleep(30)
                continue
            raise
        body = response.body.read() if hasattr(response.body,'read') else response.body
        if isinstance(body, bytes): body = body.decode()
        result = json.loads(body)
        Path('reviewed-updates-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if result.get('status') in ('verified','already_verified'):
            return
        if result.get('publisher_busy') or result.get('status') == 'pending':
            if time.monotonic() < deadline:
                time.sleep(30); continue
        raise SystemExit('Reviewed archive updates require inspection; see verification receipt')


if __name__ == '__main__': main()
