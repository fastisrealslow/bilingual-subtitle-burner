"""Compare complete existing stock decisions against the original exhaustive matcher, offline."""
import json,sys,time
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan'),str(ROOT/'linyuan/fc')]
import index as fc
import fingerprint_match as matcher
state=json.loads(Path(ROOT/'linyuan/.automation/fc_state.json').read_text())
inventory=json.loads(Path(ROOT/'linyuan/.automation/source_inventory.json').read_text())
fixed=inventory['updated_at']+60
from experiment_runtime import health
assert health()["passed"]
original=matcher.match_ratio

def exhaustive(current,previous,limit):
    if not current or not previous:return 0.0
    def distance(a,b):
        try:return (int(a,16)^int(b,16)).bit_count()
        except (TypeError,ValueError):return 10**9
    return sum(any(distance(a,b)<=limit for b in previous) for a in current)/len(current)

results={};stocks={}
try:
    with patch.object(fc.time,'time',lambda:fixed),patch.object(fc,'gh',side_effect=AssertionError('Offline benchmark must not request anything')):
        for name,method in [('exhaustive',exhaustive),('indexed',original)]:
            matcher.match_ratio=method
            start=time.perf_counter();stocks[name]=fc.source_inventory(state,inventory)
            results[name]=round(time.perf_counter()-start,6)
finally:
    matcher.match_ratio=original
assert stocks['exhaustive']==stocks['indexed'],(stocks['exhaustive'],stocks['indexed'])
report=dict(snapshot_updated_at=inventory['updated_at'],frozen_now=fixed,outputs_equal=True,
    actual_artifacts=len(inventory['artifacts']),stock=stocks['indexed'],seconds=results,
    speedup=results['exhaustive']/results['indexed'],state_mutated=False,production_dispatch=False)
Path('fingerprint-runtime-benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))
