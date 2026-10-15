"""CI entry point; the shared edit transaction is also usable from mainland FC."""
import argparse
import json
from pathlib import Path
import sys
from source_supply import validate_part
from replace_reported_tracking import apply,client_from_cookies
sys.path.insert(0,str(Path(__file__).parent/'fc'))
import index as fc

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--directory',required=True);a=ap.parse_args()
    client,csrf=client_from_cookies(fc.OWNER_MID)
    result=fc.run_with_lease('publish',lambda:apply(fc,a.directory,client,csrf,validate_part),wait_seconds=120)
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if result.get('status') not in {'verified','already_verified'}:raise SystemExit(1)
