"""Verify deployed code, daily timers and candidate supply; then kick dispatch."""
import hashlib
import io
import json
import os
from pathlib import Path


def main():
    from alibabacloud_fc20230330.client import Client
    from alibabacloud_fc20230330 import models as m
    from alibabacloud_tea_openapi import models as api
    from alibabacloud_tea_util import models as util
    client = Client(api.Config(access_key_id=os.environ['ALIYUN_AK'],
                              access_key_secret=os.environ['ALIYUN_SK'],
                              endpoint='fcv3.'+os.environ.get('FC_REGION','cn-hangzhou')+'.aliyuncs.com'))
    function = os.environ.get('FC_FUNCTION_NAME','fc-develop')

    def invoke(payload, asynchronous=False):
        response = client.invoke_function_with_options(
            function, m.InvokeFunctionRequest(qualifier='LATEST',body=io.BytesIO(json.dumps(payload).encode())),
            m.InvokeFunctionHeaders(x_fc_invocation_type='Async' if asynchronous else 'Sync'),
            util.RuntimeOptions(connect_timeout=10000,read_timeout=120000,autoretry=False))
        if asynchronous:
            return {'status_code':response.status_code}
        body = response.body.read() if hasattr(response.body,'read') else response.body
        if isinstance(body,bytes): body=body.decode()
        return json.loads(body)

    health = invoke({'triggerName':'diagnose-production'})
    expected = hashlib.sha256(Path('linyuan/fc/index.py').read_bytes()).hexdigest()
    if health.get('code_sha256') != expected or health.get('daily_limit') != 6:
        raise SystemExit('Deployed FC code/limit does not match verified checkout: '+json.dumps(health))
    # FC cron expressions use UTC. Preserve the original two timer identities;
    # dispatch at 06:00 Beijing, with three hours before the first release slot.
    desired = {'dispatch':'0 0 22 * * *', 'publish':'0 0 1,3,5,7,10,13 * * *'}
    response = client.list_triggers(function,m.ListTriggersRequest(limit=100))
    triggers = {t.trigger_name:t for t in response.body.triggers}
    for name, cron in desired.items():
        if name not in triggers or triggers[name].trigger_type != 'timer':
            raise SystemExit('Expected production timer is missing: '+name)
        config = json.loads(triggers[name].trigger_config)
        config.update(cronExpression=cron,enable=True,payload=json.dumps({'triggerName':name}))
        client.update_trigger(function,name,m.UpdateTriggerRequest(body=m.UpdateTriggerInput(
            qualifier='LATEST',trigger_config=json.dumps(config))))
    updated = client.list_triggers(function,m.ListTriggersRequest(limit=100))
    timer_proof=[]
    for trigger in updated.body.triggers:
        if trigger.trigger_name in desired:
            config=json.loads(trigger.trigger_config)
            if not config.get('enable') or config.get('cronExpression') != desired[trigger.trigger_name]:
                raise SystemExit('Timer read-back mismatch: '+trigger.trigger_name)
            timer_proof.append({'name':trigger.trigger_name,'config':config})
    result={'health':health,'timers':timer_proof,
            'dispatch':invoke({'triggerName':'dispatch','_refill_count':6},asynchronous=True)}
    Path('production-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
