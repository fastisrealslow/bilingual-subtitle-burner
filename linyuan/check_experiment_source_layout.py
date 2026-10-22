"""Read-only observed-receipt replay; proposed plan is not production output."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parent/'fc'))
import index as fc

SNAPSHOT='c60660ad8fbaa4dd576a4f616828cc3dcf98c55b'


def main():
    if len(sys.argv)>1:
        state=json.loads(Path(sys.argv[1]).read_text());snapshot='local-read-only-state'
    else:
        state=json.loads(subprocess.check_output(['gh','api',
            f'repos/{os.environ["GITHUB_REPOSITORY"]}/contents/linyuan/.automation/fc_state.json?ref={SNAPSHOT}',
            '-H','Accept: application/vnd.github.raw+json'],timeout=120));snapshot=SNAPSHOT
    before=copy.deepcopy(state);latest={e['slug']:e for e in state['dispatched']}
    children=[latest[s] for s in ('ly-story-608bf63cec','ly-story-d97101ba6e')]
    candidates=[];observed=[]
    for child in children:
        parent=latest[child['experiment_parent_slug']]
        parent={**parent,'source_sha256':child['experiment_mother_sha256']}
        record=dict(historical_mother=True)
        formats=fc.experiment_reuse_layouts(parent,record,state)
        assert formats==({'portrait'} if child is children[0] else {'landscape'})
        candidates.append((parent,record))
        observed.append(dict(parent=parent['slug'],child=child['slug'],
            old_requested_layout=child['output_layout'],observed_parent_formats=sorted(formats),
            source_sha256=parent['source_sha256']))
    assert [e['output_layout'] for e in children]==['landscape','portrait']
    # Counterfactual before these two already-dispatched jobs. This proves
    # planning only, not present eligibility, new rendering or qualified stock.
    plan=fc.experiment_reuse_plan(candidates,state,dict(portrait=2,landscape=2))
    assert [layout for p,r,layout in plan]==['portrait','landscape']
    assert state==before
    payload=(Path(sys.argv[2]).read_bytes() if len(sys.argv)>2 else
        subprocess.check_output(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/12444748806/zip'],timeout=120))
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        report=json.loads(archive.read(next(n for n in archive.namelist() if n.endswith('batch_report.json'))))
    assert report['slug']=='ly-story-608bf63cec' and type(report['accepted']) is int and report['accepted']==0
    assert any('标题文案待重试' in f.get('reason','') for f in report.get('rejected',[]))
    # Counterfactual AFTER collecting this actual completed empty-batch report
    # and AFTER active portrait producers finish without filling the gap.
    # This changes only a private copy, never production state or a live job.
    replay=copy.deepcopy(state);entry=next(e for e in reversed(replay['dispatched']) if e['slug']==report['slug'])
    entry['source_check_run_id']=39808255729
    entry['production_failure_summary']=dict(slug=report['slug'],run_id=39808255729,artifact_id=12444748806,accepted=report['accepted'])
    proposed_retry=fc.experiment_retry_layout(entry,replay,dict(portrait=2,landscape=0),[])
    assert proposed_retry=='portrait' and entry['output_layout']=='landscape' and state==before
    result=dict(passed=True,read_only=True,snapshot=snapshot,observed=observed,
        scenario='same admitted mothers, before dispatch, two gaps per format; not new output',
        proposed=[dict(parent=p['slug'],layout=l) for p,r,l in plan],
        retry_scenario='after actual zero-output report collection and an unmet portrait gap; not observed dispatch',
        empty_retry=dict(run_id=39808255729,artifact_id=12444748806,accepted=0,
            original_layout=entry['output_layout'],proposed_layout=proposed_retry),
        dispatches=0,registered_stock=False,published=False)
    out=Path('experiment-source-layout-verification');out.mkdir(exist_ok=True)
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
