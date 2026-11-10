"""Coordinate existing publication gates on CI with a domestic fallback."""
import json
import os
from pathlib import Path

import index as fc
from reviewed_upload_client import capability


def run(*,preview=False,max_slots=4,backend=None,not_before=None):
    if type(max_slots) is not int or not 1<=max_slots<=4:
        raise ValueError("Invalid bounded publication count")
    import time
    if not_before is not None:
        from datetime import datetime
        if type(not_before) not in (int,float):raise ValueError('Invalid slot clock')
        local=datetime.fromtimestamp(not_before,fc.experiment.CST)
        now=datetime.fromtimestamp(time.time(),fc.experiment.CST)
        if (not 0<=not_before-time.time()<=120 or local.date()!=now.date()
                or local.hour not in fc.PUBLISH_HOURS or (local.minute,local.second,local.microsecond)!=(0,0,0)):
            raise ValueError('Canary may reserve only the next ordinary slot for two minutes')
    initial=fc.load_state()
    due=fc.catchup_deficit(initial)
    if preview:
        return dict(preview=True,due=due,inventory=fc.source_inventory(initial),published=0)
    if not due and not_before is None:
        return dict(published=0,reason='no_due_slot')
    import fc_cost_policy
    backend=backend or getattr(fc_cost_policy,"UPLOAD_BACKEND","reviewed-upload")
    if backend not in {"reviewed-upload","ci-preferred"}:raise ValueError("Unknown publication backend")
    if backend=="reviewed-upload" and not capability():
        # Do not send a new transport event to an obsolete runtime. The legacy
        # publisher/timer remains available until the verified rollout switch.
        raise RuntimeError('FC reviewed-upload capability is unavailable; no upload intent written')
    os.environ['LINYUAN_PUBLISH_BACKEND']=backend
    def publish_due():
        if not_before is not None:
            while time.time()<not_before:time.sleep(min(5,not_before-time.time()))
        results=[]
        for _ in range(max_slots):
            state=fc.load_state()
            if not fc.catchup_deficit(state):break
            result=fc.publish_catchup(dict(triggerName='publish-catchup'))
            results.append(result)
            if not result.get('published'):break
        return dict(published=sum(r.get('published',0) for r in results),attempts=results)
    try:
        result=fc.run_with_lease('publish',publish_due)
        before={p.get('bvid') for v in initial.get('published',{}).values() for p in v.get('parts',[])}
        current=fc.load_state()
        ids=sorted({p['bvid'] for v in current.get('published',{}).values() for p in v.get('parts',[])
            if p.get('status')=='published' and p.get('bvid') and p['bvid'] not in before})
        result['new_bvids']=ids
        if ids:
            from bili_archive_status import archive_status
            try:
                result['creator_verification']=archive_status(ids,fc.OWNER_MID,fc.COOKIES_JSON)
                result['visibility_pending']=result['creator_verification']['public_count']!=len(ids)
            except Exception as exc:
                result['visibility_pending']=True
                result['creator_verification_error']=type(exc).__name__
        return result
    finally:
        fc.flush_logs()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview',action='store_true')
    args=parser.parse_args()
    result=run(preview=args.preview)
    Path('runner-publish-receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
