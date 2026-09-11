"""Verify deployed code and timers; refill only after an explicit source refresh."""
import hashlib
import io
import json
import os
from pathlib import Path


def refill_requested(env=None):
    """Keep code-deploy pushes from accidentally starting another production batch."""
    value = (env or os.environ).get('FC_REFILL', '')
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


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
    if (health.get('code_sha256') != expected or health.get('daily_limit') != 4
            or health.get('publish_hours_beijing') != [10, 14, 16, 21]
            or health.get('live_min_per_day') != 4
            or health.get('cover_styles') != ['scene','photo','light','dark']
            or health.get('landscape_hour_beijing') != 14 or health.get('audio_max_per_day') != 0
            or health.get('weekly_full_slot_beijing') != {'weekday': 6, 'hour': 21}
            or health.get('editorial_policy_version') != 2026090604
            or health.get('minimum_final_seconds') != 120
            or health.get('editorial_code_sha256') != hashlib.sha256(Path('linyuan/editorial_policy.py').read_bytes()).hexdigest()
            or health.get('dispatch_workflow_ref') != 'main'):
        raise SystemExit('Deployed FC code/limit does not match verified checkout: '+json.dumps(health))
    # Keep the inexpensive coordinator hourly; rendering remains GitHub CPU-only.
    # Publish at 10/14/16/21 Beijing time, with the first item at 10:00.
    desired = {'dispatch':'0 37 * * * *', 'publish':'0 0 2,6,8,13 * * *'}
    read_runtime = util.RuntimeOptions(connect_timeout=10000,read_timeout=60000,
                                       autoretry=True,max_attempts=3)
    response = client.list_triggers_with_options(function,m.ListTriggersRequest(limit=100),{},read_runtime)
    triggers = {t.trigger_name:t for t in response.body.triggers}
    for name, cron in desired.items():
        if name not in triggers or triggers[name].trigger_type != 'timer':
            raise SystemExit('Expected production timer is missing: '+name)
        config = json.loads(triggers[name].trigger_config)
        config.update(cronExpression=cron,enable=True,payload=json.dumps({'triggerName':name}))
        client.update_trigger_with_options(function,name,m.UpdateTriggerRequest(body=m.UpdateTriggerInput(
            qualifier='LATEST',trigger_config=json.dumps(config))),{},read_runtime)
    updated = client.list_triggers_with_options(function,m.ListTriggersRequest(limit=100),{},read_runtime)
    timer_proof=[]
    for trigger in updated.body.triggers:
        if trigger.trigger_name in desired:
            config=json.loads(trigger.trigger_config)
            if not config.get('enable') or config.get('cronExpression') != desired[trigger.trigger_name]:
                raise SystemExit('Timer read-back mismatch: '+trigger.trigger_name)
            if trigger.qualifier != 'LATEST':
                raise SystemExit('Timer is pinned to an obsolete function version')
            timer_proof.append({'name':trigger.trigger_name,'qualifier':trigger.qualifier,'config':config})
    dispatch = invoke({'triggerName':'dispatch'}, asynchronous=True) if refill_requested() else {
        'skipped': True, 'reason': 'deploy_without_refill'}
    result={'health':health,'timers':timer_proof,'dispatch':dispatch}
    Path('production-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
