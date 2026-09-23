"""Track a fixed successful-source regression without calling it a 100-source yield."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/benchmark-20260921/all-finals-regression-35921175272'
RUN='35921175272'
COMMIT='0fba8b2bf03c59cf67b3690128b0072080ddae52'
IDS={7,8,13,28,32,34,39,42,46,49,58,66,68,79,90,95,99}


def summarize():
    frozen=json.loads((ROOT/'output/final100-35902748400/results/media-verification.json').read_text())
    sources={r['id']:r['source_sha256'] for r in frozen}
    assert set(sources)==IDS
    rows={}
    for path in sorted(OUT.glob('simulation-report-*/report.json')):
        row=json.loads(path.read_text());ident=row['sample']['id']
        if (ident not in IDS or ident in rows or str(row['run_id'])!=RUN
                or row['tested_sha']!=COMMIT or row.get('diagnostic_subset') is not True):
            raise ValueError('Unexpected or mixed regression report')
        if row.get('source_sha256') and row['source_sha256']!=sources[ident]:
            raise ValueError('Regressed source has different mother bytes')
        rows[ident]=row
    summary=dict(run_id=RUN,tested_sha=COMMIT,scope='17 prior successful sources; not a random sample or full100 yield',
        expected_ids=sorted(IDS),reports=len(rows),pending=len(IDS)-len(rows),
        passed=sum(r['status']=='passed' for r in rows.values()),
        rejected=sum(r['status']=='rejected' for r in rows.values()),
        unresolved=sum(r['status']=='unresolved' for r in rows.values()),
        source_yield_credit=False,editorial_approved=False)
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'regression-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return summary


if __name__=='__main__':print(json.dumps(summarize(),ensure_ascii=False))
