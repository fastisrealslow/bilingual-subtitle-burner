"""Put fresh exact seed observations into the candidate catalogue without HTTP.

Run under the existing monitor writer lease after seed growth. This closes the
gap between a newly verified seed and the next hourly collection scan.
"""
import json
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
        media_approved=False,production_dispatch=False)


def main():
    result=materialize()
    if result['upserted_pages']:
        monitor.enforce_seed_restrictions(BASE)
        monitor.export_dashboard_data()
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
