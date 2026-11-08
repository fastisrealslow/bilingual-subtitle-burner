"""Run only the already-reviewed domestic upload on FC; coordinate on CI."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.error

from upload_transport import (VERSION,TRIGGER,TAG,LEGACY_SENTINEL,RemoteUploadPending,
                              canonical,digest,pack)


def connection():
    from alibabacloud_fc20230330.client import Client
    from alibabacloud_fc20230330 import models as m
    from alibabacloud_tea_openapi import models as api
    from alibabacloud_tea_util import models as util
    client=Client(api.Config(access_key_id=os.environ['ALIYUN_AK'],access_key_secret=os.environ['ALIYUN_SK'],
        endpoint='fcv3.'+os.environ.get('FC_REGION','cn-hangzhou')+'.aliyuncs.com'))
    return client,os.environ.get('FC_FUNCTION_NAME','fc-develop'),m,util.RuntimeOptions(
        connect_timeout=10000,read_timeout=60000,autoretry=False)


def capability():
    """Use the existing read-only health route, including on old runtimes."""
    import io
    client,function,m,runtime=connection()
    response=client.invoke_function_with_options(function,m.InvokeFunctionRequest(qualifier='LATEST',
        body=io.BytesIO(canonical(dict(triggerName='diagnose-config')))),
        m.InvokeFunctionHeaders(x_fc_invocation_type='Sync'),runtime)
    raw=response.body.read() if hasattr(response.body,'read') else response.body
    result=json.loads(raw)
    if response.status_code!=200 or (result.get('fc_cost_policy') or {}).get('upload_protocol')!=VERSION:
        return False
    from verify_production import verify_config_health
    try:
        verify_config_health(result)
    except SystemExit:
        return False
    return True


def prepare(fc,directory,part,entry,cmd):
    """No uploader is called until this binding is saved with the existing intent."""
    values={cmd[i]:cmd[i+1] for i in range(len(cmd)-1) if cmd[i].startswith('--')}
    identity='-'.join([os.environ['GITHUB_RUN_ID'],os.environ.get('GITHUB_RUN_ATTEMPT','1'),
        hashlib.sha256(canonical([entry['slug'],entry['upload_part_index'],
                                 (part.get('fingerprints') or {}).get('sha256')])).hexdigest()[:16]])
    task_id='ly-upload-'+identity
    submission=dict(task_id=task_id,slug=entry['slug'],part_index=entry['upload_part_index'],
        artifact_id=entry['upload_artifact_id'],created_at=int(time.time()),title=values['--title'],
        source=values['--source'],desc=values['--desc'],tags=values['--tag'],
        tid=fc.TID,copyright=fc.COPYRIGHT)
    bundle=Path(directory).parent/(task_id+'.zip')
    manifest=pack(directory,part,submission,bundle)
    try:
        release=fc.gh('GET','/releases/tags/'+TAG,timeout=20)
    except urllib.error.HTTPError as exc:
        if exc.code!=404:raise
        release=fc.gh('POST','/releases',dict(tag_name=TAG,target_commitish='main',
            name='Reviewed upload transport',prerelease=True),timeout=30)
    import requests
    # Stream the compact single-part ZIP. Do not load full interview media into
    # Python memory or delete/re-upload an existing possibly active asset.
    with bundle.open('rb') as stream:
        response=requests.post(f'https://uploads.github.com/repos/{fc.REPO}/releases/{release["id"]}/assets',
            params={'name':bundle.name},data=stream,
            headers={'Authorization':'Bearer '+fc.TOKEN,'Content-Type':'application/zip',
                     'Content-Length':str(bundle.stat().st_size)},timeout=(10,600))
    if response.status_code!=201:
        raise RuntimeError('Reviewed transport upload was not confirmed')
    asset=response.json()
    url=f'https://github.com/{fc.REPO}/releases/download/{TAG}/{bundle.name}'
    if asset.get('state')!='uploaded' or asset.get('browser_download_url')!=url:
        raise RuntimeError('Reviewed transport asset identity mismatch')
    binding=dict(task_id=task_id,bundle_url=url,bundle_sha256=digest(bundle),
                 manifest_sha256=hashlib.sha256(canonical(manifest)).hexdigest(),
                 asset_id=asset['id'],protocol=VERSION,created_at=submission['created_at'])
    entry['upload_transport']=binding
    return binding


def execute(binding):
    from publish_fresh_six import prepare_async_tasks
    from catchup_from_inventory import tracked_request,task_result
    client,function,m,runtime=connection()
    prepare_async_tasks(client,function,m,runtime)
    payload=dict(binding,triggerName=TRIGGER,batch_slug=LEGACY_SENTINEL)
    try:
        task=tracked_request(client,function,m,runtime,TRIGGER,binding['task_id'],
                             payload=payload,wait_seconds=2700)
        result=task_result(task)
        # If the async gateway omits/truncates its return, reconcile the exact
        # durable worker receipt. This read cannot start another uploader.
        if result is None and task and task.status in {'Succeeded','Failed','Stopped','Expired','Invalid'}:
            import index as fc
            receipt=fc.gh('GET','/contents/linyuan/.automation/upload_tasks/'+binding['task_id']+'.json?ref=main',timeout=20)
            result=json.loads(base64.b64decode(receipt['content'])).get('result')
        if (not task or task.status not in {'Succeeded','Failed','Stopped','Expired','Invalid'} or not isinstance(result,dict)
                or result.get('protocol')!=VERSION or result.get('task_id')!=binding['task_id']):
            raise RemoteUploadPending('Reviewed transfer outcome is unknown')
        if (result.get('upload_called') is True and result.get('returncode')==0
                and re.fullmatch(r'BV[a-zA-Z0-9]{10}',str(result.get('bvid') or ''))
                and result.get('video_sha256')==binding.get('video_sha256')):
            process=subprocess.CompletedProcess([],0,result['bvid'],'')
            process.upload_metrics=result
            return process
        # Even a terminal CLI error can follow a successful platform submission.
        # Preserve the ordinary authenticated archive reconciliation path.
        if result.get('upload_called') is True:
            raise RemoteUploadPending('Reviewed uploader needs archive reconciliation')
        return subprocess.CompletedProcess([],1,'','Reviewed transfer did not enter uploader: '+str(result.get('error_type')))
    except RemoteUploadPending:
        raise
    except Exception as exc:
        raise RemoteUploadPending('Reviewed transfer must be reconciled: '+type(exc).__name__) from exc
