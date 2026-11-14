"""One actual, code-bound model repair may request one normal source job."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from fc import index as F
import publishing_experiment as E
import documented_company_story as D
import verified_story_title_recovery as R

ROOT=Path(__file__).resolve().parents[1]
# Real qwen3.5:9b replay of the current selector, positive case and controls.
CPU_RUN=43605098370
CPU_HEAD='fc701af8bda408e5db0719df2ec18796617b2646'
DELIVERY_ARTIFACT=12953295426
TIMELINE_ARTIFACT=13528993846


def cpu_error(report):
    fixture=json.loads((ROOT/'tests/fixtures/linyuan_documented_company_source_20261030.json').read_text())
    selected=(report.get('selection') or {}).get('selected') or []
    controls=report.get('controls') or []
    if (report.get('passed') is not True or report.get('model')!='qwen3.5:9b'
            or report.get('source_run_id')!=40993786441 or report.get('source_sha256')!=R.MOTHER
            or report.get('fixture_sha256')!=E.digest(fixture) or report.get('baseline_selected')!=0
            or report.get('primary_replayed_calls')!=4 or len(selected)!=1
            or {c.get('name') for c in controls}!={'quoted_other_author','different_company_result'}
            or len(controls)!=2 or any(c.get('kind')!='skip' for c in controls)):
        return 'actual_model_case_or_controls_not_verified'
    item=selected[0];pick=fixture['candidates'][1]
    if ((item.get('start'),item.get('end'))!=(pick['start'],pick['end'])
            or D.selection_error(item.get('story_selection') or {})):
        return 'actual_source_selection_not_verified'
    return None


def deployment_error(report):
    health=report.get('health') or {};runtime=health.get('experiment_runtime') or {}
    if (report.get('verification_complete') is not True or runtime.get('passed') is not True
            or health.get('daily_limit')!=4
            or (health.get('publishing_experiment') or {}).get('id')!=E.EXPERIMENT_ID
            or (health.get('publishing_experiment') or {}).get('treatment_slots')!=[2,4]):
        return 'deployment_not_verified'
    for path,actual in [('linyuan/fc/index.py',health.get('code_sha256')),
            ('linyuan/publishing_experiment.py',health.get('publishing_experiment_sha256')),
            ('linyuan/documented_company_story.py',runtime.get('documented_company_story_sha256')),
            ('linyuan/editorial_policy.py',health.get('editorial_code_sha256')),
            ('linyuan/source_timeline.py',runtime.get('source_timeline_module_sha256')),
            ('linyuan/source_timeline_evidence.json',runtime.get('source_timeline_evidence_sha256'))]:
        if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=actual:
            return 'deployed_code_differs'
    return None


def download_report(run,name,filename,directory):
    info=F.gh('GET',f'/actions/runs/{run}')
    if info.get('status')!='completed' or info.get('conclusion')!='success':return None
    target=directory/str(run)
    subprocess.run(['gh','run','download',str(run),'--repo',F.REPO,'--name',name,'--dir',str(target)],
        check=True,capture_output=True,timeout=90)
    paths=list(target.rglob(filename))
    if len(paths)!=1:raise ValueError('Unambiguous verification report required')
    return json.loads(paths[0].read_text())


def request(cpu):
    issue=cpu_error(cpu)
    if issue:return dict(dispatched=0,reason=issue)
    state=F.load_state()
    inventory=json.loads(F.gh('GET','/contents/'+F.SOURCE_INVENTORY_KEY+'?ref=main',raw=True))
    stock=F.source_inventory(state,inventory)
    active=[]
    for status in ('queued','in_progress'):
        active+=F.gh('GET',f'/actions/workflows/{F.WF_PRODUCE}/runs?status={status}&per_page=100').get('workflow_runs',[])
    seeds=json.loads(F.gh('GET','/contents/linyuan/bilibili_seeds.json?ref=main',raw=True))['seeds']
    child,issue=R.plan(state,stock,active,seeds,documentary_proof=cpu)
    if issue=='already_attempted_or_unresolved':
        from source_timeline import lookup
        from editorial_policy import source_key
        prior=next((e for e in state.get('dispatched',[]) if e.get('slug')==R.DOCUMENTARY_CHILD),{})
        other=((prior.get('production_failure_summary') or {}).get('source_context') or {}).get('source_sha256')
        proof=lookup(source_key(R.SOURCE),R.MOTHER,other)
        if proof:child,issue=R.plan(state,stock,active,seeds,documentary_proof=cpu,timeline_proof=proof)
    if issue:return dict(dispatched=0,reason=issue)
    state['dispatched'].append(child);F.save_state(state)
    artifact=TIMELINE_ARTIFACT if child.get('audio_timeline_recovery_evidence') else DELIVERY_ARTIFACT
    sent=F._request_quality_reprocess(state,child,child['slug'],
        '已验证原话及完整音频时间轴：重新自动选段，全部成片门禁照常验收',artifact)
    child['documented_company_recovery_evidence']['dispatch_confirmed']=bool(sent)
    F.save_state(state)
    return dict(dispatched=int(bool(sent)),slug=child['slug'],actual_treatment_stock_added=0,
        reason='normal_production_requested' if sent else 'request_unresolved_no_automatic_replay')


def main():
    result=dict(dispatched=0,actual_treatment_stock_added=0,reason='cpu_or_deployment_pending')
    with tempfile.TemporaryDirectory() as directory:
        directory=Path(directory)
        info=F.gh('GET',f'/actions/runs/{CPU_RUN}')
        if info.get('head_sha')!=CPU_HEAD:raise ValueError('Actual CPU commit identity changed')
        cpu=download_report(CPU_RUN,'documented-company-cpu','documented-company-cpu.json',directory)
        if cpu and not cpu_error(cpu):
            for path in ('linyuan/publishing_experiment.py','linyuan/documented_company_story.py'):
                actual=F.gh('GET',f'/contents/{path}?ref={CPU_HEAD}',raw=True)
                if actual!=(ROOT/path).read_bytes():raise ValueError('CPU-tested selector code differs')
            runs=F.gh('GET','/actions/workflows/fc-production-deploy.yml/runs?per_page=10').get('workflow_runs',[])
            for run in [r for r in runs if r.get('conclusion')=='success'][:3]:
                deploy=download_report(run['id'],'production-verification','production-verification.json',directory)
                if deploy and not deployment_error(deploy):
                    result=F.run_with_lease('dispatch',lambda:request(cpu));break
    Path('documented-company-recovery-request.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
