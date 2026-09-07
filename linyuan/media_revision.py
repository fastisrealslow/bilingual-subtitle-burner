"""Permit rendering one existing video's repair, never an additional upload."""
import argparse
import copy
import json
from pathlib import Path

BVID='BV1HNbw6UEuv'
SLUG='ly-mother-edited-0907-01'
OLD_SHA='3c8d79144c856487a8f1656c407ca856945765683ea8dd3ee7fb5f01dd48ebcf'
SOURCE_SHA='9dc2b7c6f82570984a52ccdff5c4a41a7595c0a129b1919df81d7539a266a345'


def render_history(state, bvid, slug):
    if (bvid,slug)!=(BVID,SLUG):
        raise ValueError('No recorded in-place repair for this video')
    result=copy.deepcopy(state)
    receipt=result.get('published',{}).get(slug,{})
    parts=receipt.get('parts') or []
    if (len(parts)!=1 or parts[0].get('bvid')!=bvid
            or parts[0].get('source_sha256')!=SOURCE_SHA
            or parts[0].get('fingerprints',{}).get('sha256')!=OLD_SHA):
        raise ValueError('The existing media receipt changed; do not re-render a stale revision')
    # This is only a render-local copy. The live receipt remains published and
    # processed, so the ordinary publisher cannot treat its revision as stock.
    del result['published'][slug]
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bvid',required=True)
    p.add_argument('--slug',required=True);p.add_argument('--state',required=True)
    a=p.parse_args();path=Path(a.state)
    result=render_history(json.loads(path.read_text()),a.bvid,a.slug)
    path.write_text(json.dumps(result,ensure_ascii=False))
    print('Rendering an in-place media revision for',a.bvid,'; live publication history unchanged')
