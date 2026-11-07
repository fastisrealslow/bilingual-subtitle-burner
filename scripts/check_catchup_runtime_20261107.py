import io,json,os,sys
from pathlib import Path
sys.path[:0]=['linyuan','linyuan/fc']
from alibabacloud_fc20230330.client import Client
from alibabacloud_fc20230330 import models as m
from alibabacloud_tea_openapi import models as api
from alibabacloud_tea_util import models as util
from verify_production import read_config_health,verify_config_health
from bili_archive_status import archive_status
client=Client(api.Config(access_key_id=os.environ['ALIYUN_AK'],access_key_secret=os.environ['ALIYUN_SK'],endpoint='fcv3.'+os.environ['FC_REGION']+'.aliyuncs.com'))
function=os.environ['FC_FUNCTION_NAME']
runtime=util.RuntimeOptions(connect_timeout=10000,read_timeout=120000,autoretry=False)
def read_health():
    reply=client.invoke_function_with_options(function,m.InvokeFunctionRequest(qualifier='LATEST',body=io.BytesIO(b'{"triggerName":"diagnose-config"}')),m.InvokeFunctionHeaders(x_fc_invocation_type='Sync'),runtime)
    data=reply.body.read() if hasattr(reply.body,'read') else reply.body
    return json.loads(data)
verify_config_health(read_config_health(read_health))
config=client.get_function_with_options(function,m.GetFunctionRequest(qualifier='LATEST'),{},runtime).body
actual=(config.environment_variables or {}).get('BILIBILI_COOKIES')
report=dict(code_verified=True,production_dispatch=False,state_mutated=False,fc_cookie_configured=bool(actual))
def credentials(raw):
    data=json.loads(raw.lstrip('\ufeff'));rows=(data.get('cookie_info') or {}).get('cookies') or data.get('cookies')
    if rows is None:return {k:data.get(k) for k in ('SESSDATA','bili_jct','DedeUserID')}
    return {r['name']:r['value'] for r in rows if r.get('name') in {'SESSDATA','bili_jct','DedeUserID'}}
if actual:
    report['same_login_credentials']=credentials(actual)==credentials(os.environ['BILIBILI_COOKIES'])
    check=archive_status([],275211725,actual,expected_titles=['林园：现在港股不跟美股，跟着 A 股走，两地上市除非特好不买'],max_pages=1)
    report['fc_login_read']=dict(api_code=check.get('api_code'),http_status=check.get('http_status'),owner_verified=check.get('owner_verified'),response_error=check.get('response_error'))
Path('catchup-runtime-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))
if not actual or report['fc_login_read'].get('api_code')!=0 or report['fc_login_read'].get('owner_verified') is not True:
    raise SystemExit('FC login identity is not verified; do not enter uploader')
