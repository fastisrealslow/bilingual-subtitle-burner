"""Put fresh exact seed observations into the candidate catalogue without HTTP.

Run under the existing monitor writer lease after seed growth. This closes the
gap between a newly verified seed and the next hourly collection scan.
"""
import json
import os
from pathlib import Path
import sqlite3

import monitor_v2 as monitor
from verified_seed_snapshot import items

BASE=Path(__file__).resolve().parent


def materialize(base=BASE, limit=24):
    base=Path(base)
    if type(limit) is not int or not 1<=limit<=24:
        raise ValueError('At most 24 exact parent observations per pass')
    database=base/'monitor_v2.db'
    if database.resolve()!=monitor.DB_PATH.resolve():
        raise ValueError('Catalogue writer must use the inspected database')
    # Never treat an unreadable catalogue as empty: it may contain denials for
    # other pages of this mother. The normal upsert also retains those holds.
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as connection:
        known={key:json.loads(extra or '{}') for key,extra in connection.execute('SELECT id,extra FROM items')}
    seeds=json.loads((base/'bilibili_seeds.json').read_text())['seeds']
    proposals=[]
    for seed in seeds:
        rows=items(seed)
        needed=[r for r in rows if r['id'] not in known
            or known[r['id']].get('metadata_checked_at')!=seed.get('checked_at')]
        if needed:
            proposals.append((not any(r['id'] not in known for r in needed),
                -max(float(json.loads(r['extra'])['duration']) for r in needed),seed['bvid'],needed))
    selected=sorted(proposals,key=lambda r:r[:3])[:limit]
    rows=[r for *_,batch in selected for r in batch]
    new=monitor.upsert_items(rows) if rows else []
    return dict(materialized_parents=len(selected),upserted_pages=len(rows),new_pages=len(new),
        refreshed_pages=len(rows)-len(new),seed_added_count=0,accepted_mp4_count=0,
        media_approved=False,production_dispatch=False,changed_candidate_ids=[r['id'] for r in rows])


def admissible_long_count(base,changed_ids):
    """Only changed, currently admissible, unattempted long pages wake admission."""
    if not changed_ids:return 0
    from fc import index as fc
    try:
        state=json.loads((Path(base)/'.automation/fc_state.json').read_text())
        data=json.loads((Path(base)/'dashboard/data.json').read_text())
        if (not isinstance(state.get('dispatched'),list) or not isinstance(state.get('published'),dict)
                or not isinstance(state.get('rejected'),list)):
            return 0
        rows=data if isinstance(data,list) else data.get('items',[])
        changed=set(changed_ids)
        # Assess new observations only. Existing retry clocks and their real
        # budgets stay untouched in the state file and normal dispatcher.
        candidates=fc.pick([r for r in rows if r.get('id') in changed],
                           {**state,'pending_retry':[]},fc.MAX_ATTEMPTS)
        return sum(fc.experiment.long_candidate(row) for row in candidates)
    except (OSError,ValueError,TypeError,KeyError,AttributeError):return 0


def main():
    result=materialize(BASE)
    changed=result.pop('changed_candidate_ids',[])
    if result['upserted_pages']:
        monitor.enforce_seed_restrictions(BASE)
        monitor.export_dashboard_data()
    result['admissible_long_candidates']=admissible_long_count(BASE,changed)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as output:
            output.write('admission_wakeup_required='+str(result['admissible_long_candidates']>0).lower()+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
