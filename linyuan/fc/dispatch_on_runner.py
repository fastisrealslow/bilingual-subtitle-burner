"""Run admission/recovery on GitHub; use FC only for domestic media transfers."""
import json
import os
from pathlib import Path
import time

import index as fc


def retire_hourly_timer(client, function, models, runtime):
    """Disable only dispatch, after proving the publication timer is intact."""
    def read():
        result = client.list_triggers_with_options(
            function, models.ListTriggersRequest(limit=100), {}, runtime)
        return {t.trigger_name: t for t in result.body.triggers}

    triggers = read()
    publish = triggers.get('publish')
    dispatch = triggers.get('dispatch')
    if not publish or publish.trigger_type != 'timer':
        raise RuntimeError('Publication timer is missing; keep dispatch unchanged')
    publish_config = json.loads(publish.trigger_config)
    if (publish_config.get('enable') is not True
            or publish_config.get('cronExpression') != '0 0 2,6,8,13 * * *'
            or publish.qualifier != 'LATEST'):
        raise RuntimeError('Publication timer differs from daily four; keep dispatch unchanged')
    if not dispatch or dispatch.trigger_type != 'timer':
        raise RuntimeError('Dispatch timer is missing; inspect migration before changing anything')
    config = json.loads(dispatch.trigger_config)
    if config.get('enable') is not False:
        config['enable'] = False
        client.update_trigger_with_options(function, 'dispatch', models.UpdateTriggerRequest(
            body=models.UpdateTriggerInput(trigger_config=json.dumps(config))), {}, runtime)
    verified = read()
    if json.loads(verified['dispatch'].trigger_config).get('enable') is not False:
        raise RuntimeError('Hourly dispatch timer disable did not persist')
    actual_publish = verified['publish']
    if (json.loads(actual_publish.trigger_config) != publish_config
            or actual_publish.qualifier != publish.qualifier):
        raise RuntimeError('Publication timer changed during migration; inspect concurrent update')
    return {'dispatch_enabled': False, 'publish_enabled': True,
            'publish_cron': publish_config['cronExpression']}


def retire_timer():
    from alibabacloud_fc20230330.client import Client
    from alibabacloud_fc20230330 import models
    from alibabacloud_tea_openapi import models as api
    from alibabacloud_tea_util import models as util
    client = Client(api.Config(access_key_id=os.environ['ALIYUN_AK'],
        access_key_secret=os.environ['ALIYUN_SK'],
        endpoint='fcv3.' + os.environ.get('FC_REGION', 'cn-hangzhou') + '.aliyuncs.com'))
    return retire_hourly_timer(client, os.environ.get('FC_FUNCTION_NAME', 'fc-develop'),
        models, util.RuntimeOptions(connect_timeout=10000, read_timeout=60000, autoretry=False))


def main():
    if not fc.TOKEN:
        raise RuntimeError('GITHUB_TOKEN is required for runner admission')
    receipt = {'backend': 'github', 'run_id': os.environ.get('GITHUB_RUN_ID'),
               'started_at': int(time.time()), 'completed': False}
    try:
        fc.log_event('github_dispatch', 'GitHub 调度开始：补库存及失败恢复')
        result = fc.dispatch_handler({'execution_backend': 'github'})
        receipt['dispatch_result'] = result
        if result.get('admission_busy') or result.get('inventory_unavailable'):
            receipt['migration_deferred'] = True
            return receipt
        if result.get('requires_mainland_transfer'):
            from catchup_from_inventory import run_inventory_task, task_result
            task_id, task = run_inventory_task('dispatch-source-inventory')
            receipt['domestic_transfer'] = {'task_id': task_id,
                'status': task.status if task else 'Unknown', 'result': task_result(task)}
            if not task or task.status != 'Succeeded':
                raise RuntimeError('Domestic transfer unresolved; retain task ID and hourly timer')
        # No FC invocation is used to disable/read back these control-plane timers.
        # Run admission successfully first, so a broken runner cannot strand supply.
        receipt['timers'] = retire_timer()
        receipt['completed'] = True
        return receipt
    except Exception as exc:
        message = str(exc).lower()
        if 'current user is in debt' in message or 'account in debt' in message:
            receipt['failure'] = {
                'category': 'cloud_account_billing', 'provider': 'aliyun_fc',
                'retryable_without_external_change': False,
                'material_rejected': False,
                'reason': '阿里云账户欠费，中转/控制面不可用；恢复账户后再试'}
        else:
            receipt['failure'] = {'category': 'dispatch_runtime',
                                  'error_type': type(exc).__name__,
                                  'material_rejected': False}
        raise
    finally:
        fc.flush_logs()
        Path('github-dispatch-receipt.json').write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2))
        print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
