"""Source-limited real-ledger A/B planning replay; no dispatch or approval."""
import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan/fc'),str(ROOT/'linyuan')]
import index as F
import publishing_experiment as E


def main():
    paths=[ROOT/'site/fc_state.json',ROOT/F.SOURCE_INVENTORY_KEY,ROOT/F.DATA_JSON]
    state,inventory,data=[json.loads(p.read_text()) for p in paths]
    items=data if isinstance(data,list) else data['items']
    local=os.environ.get('BASELINE_FC_PATH')
    if local:
        before=Path(local).read_bytes()
    else:
        repo=os.environ.get('GITHUB_REPOSITORY','fastisrealslow/bilingual-subtitle-burner')
        url=f'https://raw.githubusercontent.com/{repo}/b343785cf7f405d438f7d439ec4ccfdb0e79b023/linyuan/fc/index.py'
        with urlopen(url,timeout=30) as response:before=response.read(1_000_001)
    assert hashlib.sha256(before).hexdigest()=='ff91a1c3775d574a71a7efe9ea13be5332037a0a9afc4b713eb99fd27f09de76'
    old=next(n for n in ast.parse(before).body if isinstance(n,ast.FunctionDef)
             and n.name=='experiment_reuse_candidates')
    namespace=dict(F.__dict__)
    exec(compile(ast.Module(body=[old],type_ignores=[]),'pinned-before-reuse','exec'),namespace)
    latest={e['slug']:e for e in F._latest_dispatches(state)}
    records={r['slug']:r for r in inventory['artifacts']}
    original=deepcopy((state,inventory,items))
    cases=[]
    # Use the real snapshot's own clock; never renew an inspection timestamp.
    with patch.object(F.time,'time',return_value=float(inventory['updated_at'])):
        for slug,entry in latest.items():
            record=records.get(slug)
            if (not record or entry.get('experiment_selection_revision')==E.SELECTION_REVISION
                    or not any(p.get('status')=='verified' and E.actual_profile(p)==E.TREATMENT
                               for p in record.get('parts',[]))
                    or F.old_treatment_blocks_reselection(entry,record,state)):continue
            mother=entry['experiment_mother_sha256'];parent=entry.get('experiment_parent_slug')
            related={slug,parent}
            for key,row in latest.items():
                if (row.get('source_url')==entry.get('source_url')
                        or row.get('experiment_mother_sha256')==mother or row.get('source_sha256')==mother
                        or row.get('experiment_parent_slug')==parent
                        or any(p.get('source_sha256')==mother for p in
                               (state.get('published',{}).get(key) or {}).get('parts',[]))):related.add(key)
            related.update(r['slug'] for r in inventory['artifacts']
                           if any(p.get('source_sha256')==mother for p in r.get('parts',[])))
            # Keep ALL publication history for real range/audio/text dedup.
            # Project only unrelated dispatch/stock records out of this one-
            # mother planning test. These are not global inventory metrics.
            projected={**state,'dispatched':[r for r in state['dispatched'] if r['slug'] in related]}
            stock={**inventory,'artifacts':[r for r in inventory['artifacts'] if r['slug'] in related]}
            a=list(namespace['experiment_reuse_candidates'](projected,stock,items))
            b=list(F.experiment_reuse_candidates(projected,stock,items))
            prior={p.get('source_sha256') for p,r in a}
            added=[(p,r) for p,r in b if p.get('source_sha256') not in prior]
            if not added:continue
            assert all(p.get('source_sha256')==mother for p,r in added)
            plan=F.experiment_reuse_plan(added,projected,dict(portrait=1,landscape=0))
            assert plan and plan[0][2]=='portrait'
            cases.append(dict(old_treatment_slug=slug,mother_sha256=mother,
                still_unpublishable=[dict(index=p['index'],sha256=p.get('sha256'),
                    error=F.inventory_publication_error(p,entry,state)) for p in record['parts']
                    if p.get('status')=='verified' and E.actual_profile(p)==E.TREATMENT],
                source_limited_before_candidates=len(a),source_limited_after_candidates=len(b),
                proposed=[dict(parent=p['slug'],run_id=r.get('run_id'),requested_layout=layout,
                    geometry_hints=r.get('source_geometry_hints'),
                    next_slug='ly-story-'+E.digest([E.EXPERIMENT_ID,mother,E.SELECTION_REVISION])[:10])
                    for p,r,layout in plan]))
    assert cases,'No actual newly eligible mother found; do not claim stock increased'
    assert (state,inventory,items)==original
    report=dict(passed=True,kind='read_only_source_limited_planning_comparison',
        snapshot_clock=inventory['updated_at'],baseline_fc_sha256=hashlib.sha256(before).hexdigest(),
        input_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        cases=cases,source_rows_and_receipts_unchanged=True,inspection_timestamps_renewed=False,
        dispatched=False,media_approved=False,stock_registered=False,sample_registered=False,published=False)
    (ROOT/'blocked-treatment-mother-reuse-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
