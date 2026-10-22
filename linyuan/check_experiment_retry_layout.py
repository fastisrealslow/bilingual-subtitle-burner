"""Read-only real-stock retry-planning regression, not a new stock claim."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent/'fc'))
import index as fc


def original(path):
    snapshot='2ba1a8866411ba6cbf4a2503b93ab951d9d1fab2'
    return json.loads(subprocess.check_output(['gh','api',
        f'repos/{os.environ["GITHUB_REPOSITORY"]}/contents/{path}?ref={snapshot}',
        '-H','Accept: application/vnd.github.raw+json'],timeout=120))


def main():
    state=original('linyuan/.automation/fc_state.json')
    inventory=original('linyuan/.automation/source_inventory.json')
    before=copy.deepcopy(state)
    entry={e['slug']:e for e in fc._latest_dispatches(state)}['ly-story-d83bdfcc6f']
    assert entry['editorial_profile']==fc.experiment.TREATMENT and entry['output_layout']=='landscape'
    with patch.object(fc.time,'time',return_value=1792653000):
        stock=fc.source_inventory(state,inventory)
        assert stock['inventory_fresh'] and stock['verified_landscape']>=8
        assert stock['experiment_ready']['landscape']==0
        # Explicit planning scenario: when current producers finish without
        # qualifying treatment, cached retry must be admissible. This does NOT
        # assert that today's real workers have completed or yielded any stock.
        active=[]
        normal=fc.landscape_admission_deficit(stock,state,active)
        needs=fc.experiment.production_deficits(stock,state,active)
        assert normal==0 and needs['landscape']==2
        assert fc.retry_supply_needed(entry,normal,needs)
        assert not fc.retry_supply_needed({**entry,'editorial_profile':'baseline'},normal,needs)
    assert state==before
    print(json.dumps(dict(snapshot='2ba1a8866411ba6cbf4a2503b93ab951d9d1fab2',slug=entry['slug'],baseline_landscape=stock['verified_landscape'],
        treatment_landscape=0,ordinary_admissions=normal,treatment_needed=needs['landscape'],
        scenario='after_active_jobs_finish_without_treatment; not observed completed jobs',
        old_blocks_treatment=True,new_permits_bounded_retry=True,passed=True,read_only=True,
        dispatches=0,media_downloads=0,registered_stock=False,published=False),ensure_ascii=False))


if __name__=='__main__':main()
