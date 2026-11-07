import json,os,re
from pathlib import Path
from alibabacloud_fc20230330.client import Client
from alibabacloud_fc20230330 import models as m
from alibabacloud_tea_openapi import models as api
from alibabacloud_tea_util import models as util
from aliyun.log import LogClient,GetLogsRequest
report=dict(state_mutated=False,production_dispatch=False)
redactions=[v for k,v in os.environ.items() if k in {'ALIYUN_AK','ALIYUN_SK','BILIBILI_COOKIES'} and v]
cookies=json.loads(os.environ['BILIBILI_COOKIES'].lstrip('\ufeff'))
for row in (cookies.get('cookie_info') or {}).get('cookies') or cookies.get('cookies') or []:
    if isinstance(row,dict) and len(str(row.get('value') or ''))>3:redactions.append(str(row['value']))
for k in ('SESSDATA','bili_jct','access_token','refresh_token'):
    if isinstance(cookies.get(k),str):redactions.append(cookies[k])
def safe(value):
    text=str(value)
    for secret in redactions:text=text.replace(secret,'[redacted]')
    text=re.sub(r'https?://[^\s\"<>]+','[url]',text)
    text=re.sub(r'(?i)(SESSDATA|bili_jct|access_token|refresh_token|Authorization)([\s\"=:]+)[^\s,;]+',r'\1=[redacted]',text)
    return text[:1800]
try:
    client=Client(api.Config(access_key_id=os.environ['ALIYUN_AK'],access_key_secret=os.environ['ALIYUN_SK'],endpoint='fcv3.'+os.environ['FC_REGION']+'.aliyuncs.com'))
    config=client.get_function_with_options(os.environ['FC_FUNCTION_NAME'],m.GetFunctionRequest(qualifier='LATEST'),{},util.RuntimeOptions(connect_timeout=10000,read_timeout=30000)).body.log_config
    report['logs_configured']=bool(config and config.project and config.logstore)
    report['rows']=[]
    if report['logs_configured']:
        logs=LogClient(os.environ['FC_REGION']+'.log.aliyuncs.com',os.environ['ALIYUN_AK'],os.environ['ALIYUN_SK'])
        for query in ['"ly-1104-992bb2"','"投稿失败" or "投稿超时" or "Task timed out"']:
            response=logs.get_logs(GetLogsRequest(config.project,config.logstore,1794016800,1794024600,query=query,line=100))
            for row in response.get_logs():
                content=row.get_contents()
                text=' '.join(str(v) for k,v in content.items() if k in {'message','content','msg','__raw__','level'})
                if text:report['rows'].append(dict(time=row.get_time(),text=safe(text)))
except Exception as exc:
    code=getattr(exc,'code',None)
    if not code and hasattr(exc,'get_error_code'):code=exc.get_error_code()
    report.update(error_type=type(exc).__name__,error_code=re.sub('[^A-Za-z0-9_.-]','',str(code or ''))[:80])
Path('upload-root-readonly.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))
