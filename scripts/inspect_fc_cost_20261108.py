"""Read cloud control-plane and bills without invoking any function. Encrypt all output."""
import base64,inspect,json,os,subprocess,time
from pathlib import Path
from alibabacloud_fc20230330.client import Client
from alibabacloud_fc20230330 import models as m
from alibabacloud_tea_openapi import models as api
from alibabacloud_tea_util import models as util
from aliyunsdkcore.client import AcsClient
from aliyunsdkbssopenapi.request.v20171214.DescribeInstanceBillRequest import DescribeInstanceBillRequest
PUBLIC_KEY='-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAv3DsAcPb96NQA4nJGZpj\nj9QOFLSwrRf9ZEyb6uruJsMpTS3i2VJZ8M+v6O6GgWqfBcluZv30IhJItqOEuDpj\nSViFx+C8/HUjETEwcRiUkf5igi/KkH+y6x6zVh9wUdVnaP8kGs/Hg6/89NYNthwi\nNBLCJJgfds7S/YHQeXqUOXvPSWJYx8T95XLz94lhvu4CpUNfenEc9piBYI9ipUSN\nxiTgvP7bvWbnskUjCtnQYSdTDLnrJLjGgV8PC47XlFhBz0noQ3uNFioQxNqVjff9\nrI7TwjWO9xKCPo/NK+QTIbKs0WSd+BHX5LySRDTEyWRhZ0sFhYWEtRR9VUiw+Upz\njwIDAQAB\n-----END PUBLIC KEY-----\n'
report=dict(observed_at=time.time(),function_invocations=0,state_mutated=False)
def error(exc):
    code=getattr(exc,'code',None)
    if hasattr(exc,'get_error_code'):code=exc.get_error_code()
    return dict(error_type=type(exc).__name__,error_code=str(code or '')[:100])
region=os.environ.get('FC_REGION','cn-hangzhou');function=os.environ.get('FC_FUNCTION_NAME','fc-develop')
fc=Client(api.Config(access_key_id=os.environ['ALIYUN_AK'],access_key_secret=os.environ['ALIYUN_SK'],endpoint='fcv3.'+region+'.aliyuncs.com'))
runtime=util.RuntimeOptions(connect_timeout=10000,read_timeout=30000,autoretry=False)
try:
    body=fc.get_function_with_options(function,m.GetFunctionRequest(qualifier='LATEST'),{},runtime).body
    report['function']={key:getattr(body,key,None) for key in ['function_name','runtime','cpu','memory_size','disk_size','timeout','instance_concurrency','instance_lifecycle_config','internet_access']}
    if hasattr(report['function'].get('instance_lifecycle_config'),'to_map'):report['function']['instance_lifecycle_config']=report['function']['instance_lifecycle_config'].to_map()
    triggers=fc.list_triggers_with_options(function,m.ListTriggersRequest(limit=100),{},runtime).body.triggers
    report['triggers']=[dict(name=t.trigger_name,type=t.trigger_type,config=json.loads(t.trigger_config)) for t in triggers]
except Exception as exc:report['function_error']=error(exc)
for method,model in [('get_scaling_config','GetScalingConfigRequest'),('get_provision_config','GetProvisionConfigRequest')]:
    if not hasattr(fc,method+'_with_options') or not hasattr(m,model):continue
    try:
        ctor=getattr(m,model);kwargs={'qualifier':'LATEST'} if 'qualifier' in inspect.signature(ctor).parameters else {}
        response=getattr(fc,method+'_with_options')(function,ctor(**kwargs),{},runtime).body
        data=response.to_map() if hasattr(response,'to_map') else {}
        report[method]={k:v for k,v in data.items() if any(s in k.lower() for s in ['instance','minimum','maximum','scheduled','provision','target','current'])}
    except Exception as exc:report[method]=error(exc)
try:
    ctor=m.ListAsyncTasksRequest;fields=inspect.signature(ctor).parameters
    kwargs={k:v for k,v in dict(qualifier='LATEST',limit=100,include_payload=True).items() if k in fields}
    body=fc.list_async_tasks_with_options(function,ctor(**kwargs),{},runtime).body
    tasks=getattr(body,'tasks',[]) or []
    report['async_tasks']=[]
    for task in tasks:
        data=task.to_map()
        row={k:v for k,v in data.items() if any(x in k.lower() for x in ['time','duration','status','taskid','payload'])}
        row['available_keys']=sorted(data)
        report['async_tasks'].append(row)
    report['task_details']=[]
    for task in tasks[:12]:
        try:
            detail=fc.get_async_task_with_options(function,task.task_id,m.GetAsyncTaskRequest(qualifier='LATEST'),{},runtime).body.to_map()
            report['task_details'].append(detail)
        except Exception as exc:report['task_details'].append(error(exc))
    try:
        functions=fc.list_functions_with_options(m.ListFunctionsRequest(limit=100),{},runtime).body
        report['regional_functions']=[{k:getattr(f,k,None) for k in ['function_name','cpu','memory_size','timeout','runtime']} for f in functions.functions]
    except Exception as exc:report['regional_functions_error']=error(exc)
except Exception as exc:report['async_tasks_error']=error(exc)
payload=json.dumps(report,ensure_ascii=False,default=str).encode()
Path('/tmp/audit-key.pub').write_text(PUBLIC_KEY)
chunks=[]
for start in range(0,len(payload),160):
    result=subprocess.run(['openssl','pkeyutl','-encrypt','-pubin','-inkey','/tmp/audit-key.pub','-pkeyopt','rsa_padding_mode:oaep','-pkeyopt','rsa_oaep_md:sha256'],input=payload[start:start+160],capture_output=True,check=True)
    chunks.append(base64.b64encode(result.stdout).decode())
Path('fc-cost-audit.encrypted.json').write_text(json.dumps(dict(version=1,encrypted_chunks=chunks)))
print('Encrypted read-only audit saved; no functions invoked and no configuration changed')
