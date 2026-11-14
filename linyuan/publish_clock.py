"""Wake the trusted CI coordinator; no video work on the paid timer invocation."""
WORKFLOW='linyuan-publish-runner.yml'
REPO='fastisrealslow/bilingual-subtitle-burner'


def event_route(event, policy):
    """FC timer names identify the timer; its custom message lives in payload."""
    import json
    name=str(event.get('triggerName',''))
    if name!='publish' or not event.get('triggerTime'):return name
    timer=policy.publish_timer()
    if not timer['recovery']:return name
    raw=event.get('payload')
    if not isinstance(raw,str) or len(raw)>1024:return name
    try:custom=json.loads(raw)
    except (ValueError,TypeError):return name
    # Recognize only this owned recovery message. Never merge arbitrary timer
    # payload fields into publisher controls or infer an upload command.
    return policy.RECOVERY_TRIGGER if custom==timer['payload'] else name


def request(gh):
    rows=gh('GET',f'/actions/workflows/{WORKFLOW}/runs?per_page=5',timeout=15).get('workflow_runs')
    if not isinstance(rows,list):raise ValueError('Publisher clock run list unavailable')
    for row in rows:
        if (row.get('path')!='.github/workflows/'+WORKFLOW or row.get('head_branch')!='main'
                or (row.get('head_repository') or {}).get('full_name')!=REPO):
            raise ValueError('Publisher clock run identity unavailable')
        if row.get('status') in {'queued','pending','requested','waiting','in_progress'}:
            return dict(scheduled=False,reason='publisher_already_active',published=0)
    # Never repeat an ambiguous POST, and never fall through to a second uploader.
    gh('POST',f'/actions/workflows/{WORKFLOW}/dispatches',{'ref':'main','inputs':{'preview':'false'}},timeout=20)
    return dict(scheduled=True,workflow=WORKFLOW,ref='main',published=0)


def recover(fc):
    """One late domestic safety net; no paid lease wait or empty log writes."""
    if not fc.catchup_deficit(fc.load_state()):
        return dict(published=0,reason='due_slots_already_complete')
    import os
    previous=os.environ.get('LINYUAN_PUBLISH_BACKEND')
    os.environ['LINYUAN_PUBLISH_BACKEND']='legacy'
    def due():
        results=[]
        for _ in range(4):
            result=fc.publish_catchup(dict(triggerName='publish-catchup'))
            results.append(result)
            if not result.get('published'):break
        return dict(published=sum(r.get('published',0) for r in results),
                    recovery=True,attempts=results)
    try:
        return fc.run_with_lease('publish',due,wait_seconds=0)
    finally:
        if previous is None:os.environ.pop('LINYUAN_PUBLISH_BACKEND',None)
        else:os.environ['LINYUAN_PUBLISH_BACKEND']=previous
        fc.flush_logs()
