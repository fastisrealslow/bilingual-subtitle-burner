"""Coordinate existing publication gates on CI, keeping domestic upload on FC."""
import json
import os
from pathlib import Path

import index as fc
from reviewed_upload_client import capability


def run(*,preview=False):
    initial=fc.load_state()
    due=fc.catchup_deficit(initial)
    if preview:
        return dict(preview=True,due=due,inventory=fc.source_inventory(initial),published=0)
    if not due:
        return dict(published=0,reason='no_due_slot')
    if not capability():
        # Do not send a new transport event to an obsolete runtime. The legacy
        # publisher/timer remains available until the verified rollout switch.
        raise RuntimeError('FC reviewed-upload capability is unavailable; no upload intent written')
    os.environ['LINYUAN_PUBLISH_BACKEND']='reviewed-upload'
    def publish_due():
        results=[]
        for _ in range(4):
            state=fc.load_state()
            if not fc.catchup_deficit(state):break
            result=fc.publish_catchup(dict(triggerName='publish-catchup'))
            results.append(result)
            if not result.get('published'):break
        return dict(published=sum(r.get('published',0) for r in results),attempts=results)
    try:
        return fc.run_with_lease('publish',publish_due)
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
