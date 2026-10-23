"""Independent analytics clock. Never admit sources, edit receipts or publish."""
from datetime import date, datetime, timedelta
import time

import publishing_experiment as experiment

TRIGGER = 'experiment-observations'
CRON = '0 47 * * * *'
WORKFLOW = 'linyuan-experiment-observations.yml'
REPO = 'fastisrealslow/bilingual-subtitle-burner'


def policy():
    # Keep the last clip's 168–170h measurement, not a new experiment period.
    return dict(name=TRIGGER,cron=CRON,workflow=WORKFLOW,ref='main',
        start=experiment.START,
        end_exclusive=(date.fromisoformat(experiment.END_EXCLUSIVE)+timedelta(days=7)).isoformat(),
        source_jobs=False,publication=False)


def request(gh,now=None):
    now=time.time() if now is None else now
    local=datetime.fromtimestamp(now,experiment.CST).date().isoformat()
    config=policy()
    if not config['start']<=local<config['end_exclusive']:
        return dict(scheduled=False,reason='outside_fixed_observation_period')
    try:
        payload=gh('GET',f'/actions/workflows/{WORKFLOW}/runs?per_page=5',timeout=15)
        rows=payload.get('workflow_runs') if isinstance(payload,dict) else None
        if not isinstance(rows,list) or len(rows)>5:
            raise ValueError('Unverified collector run list')
        for row in rows:
            if (not isinstance(row,dict) or row.get('path')!='.github/workflows/'+WORKFLOW
                    or row.get('head_branch')!='main'
                    or (row.get('head_repository') or {}).get('full_name')!=REPO):
                raise ValueError('Collector run identity unavailable')
            status=row.get('status')
            if status in {'queued','pending','requested','waiting','in_progress'}:
                return dict(scheduled=False,reason='collector_already_active')
            if status!='completed':raise ValueError('Unknown collector status')
            updated=datetime.fromisoformat(row['updated_at'].replace('Z','+00:00'))
            if updated.tzinfo is None or updated.timestamp()>now:
                raise ValueError('Unknown or future collector clock')
            if row.get('conclusion')=='success' and now-updated.timestamp()<25*60:
                return dict(scheduled=False,reason='recent_collector_success')
        # Fixed trusted code only. No source/title/slot/input from the event.
        gh('POST',f'/actions/workflows/{WORKFLOW}/dispatches',{'ref':'main'},timeout=20)
        return dict(scheduled=True,workflow=WORKFLOW,ref='main',source_jobs=0,publications=0)
    except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
        # Unknown is not an unused budget. Do not create a second job after a
        # failed/ambiguous status read, nor retry an ambiguous dispatch POST.
        return dict(scheduled=False,reason='collector_request_unavailable',error_type=type(exc).__name__)
