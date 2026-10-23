"""One attested analytics-route smoke test. Never invoke production handlers."""
import io
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from verify_production import read_config_health,verify_config_health


def probe(invoke,root=None):
    health=read_config_health(lambda:invoke({'triggerName':'diagnose-config'}))
    # An older function would default an unknown trigger into publication.
    # Require the exact already-reviewed route/module before invoking it.
    verify_config_health(health,root)
    result=invoke({'triggerName':'experiment-observations'})
    if not isinstance(result,dict) or type(result.get('scheduled')) is not bool:
        raise ValueError('Analytics route returned no explicit result')
    if result['scheduled']:
        if (result.get('workflow')!='linyuan-experiment-observations.yml'
                or result.get('ref')!='main' or type(result.get('source_jobs')) is not int
                or result['source_jobs']!=0 or type(result.get('publications')) is not int
                or result['publications']!=0):
            raise ValueError('Unexpected analytics dispatch identity or production side effect')
    elif result.get('reason') not in ('collector_already_active','recent_collector_success'):
        raise ValueError('Analytics route is unavailable: '+json.dumps(result,ensure_ascii=False))
    return dict(verified_runtime=True,actual_fc_route=True,
        deployed_code_sha256=health['code_sha256'],
        deployed_clock_sha256=health['experiment_observation_clock_sha256'],
        source_job_invocations=0,publication_invocations=0,result=result)


def main():
    from alibabacloud_fc20230330.client import Client
    from alibabacloud_fc20230330 import models as m
    from alibabacloud_tea_openapi import models as api
    from alibabacloud_tea_util import models as util
    client=Client(api.Config(access_key_id=os.environ['ALIYUN_AK'],
        access_key_secret=os.environ['ALIYUN_SK'],
        endpoint='fcv3.'+os.environ.get('FC_REGION','cn-hangzhou')+'.aliyuncs.com'))
    def invoke(payload):
        response=client.invoke_function_with_options(os.environ.get('FC_FUNCTION_NAME','fc-develop'),
            m.InvokeFunctionRequest(qualifier='LATEST',body=io.BytesIO(json.dumps(payload).encode())),
            m.InvokeFunctionHeaders(x_fc_invocation_type='Sync'),
            util.RuntimeOptions(connect_timeout=10000,read_timeout=120000,autoretry=False))
        body=response.body.read() if hasattr(response.body,'read') else response.body
        return json.loads(body.decode() if isinstance(body,bytes) else body)
    result=probe(invoke)
    Path('observation-clock-runtime-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
