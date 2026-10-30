"""One source-bound normal retry after the actual title repair is deployed.

CPU title evidence earns one retry, never media approval or a published T.
The existing lease, worker ceiling, producer and publisher gates remain.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from fc import index as F
from publishing_experiment import TREATMENT, digest
from reuse_title_verification import validate_candidate_pool
from source_admission_guard import restriction

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = 'ly-story-77352bfc47'
CHILD = 'ly-titlefix-20261030-4b387838'
SOURCE = 'https://www.bilibili.com/video/BV1iC4y1H7JN'
MOTHER = '4b3878382b576fd0c8272ae4eeec9e0b1960edb460e8e8019953be7b8c23378e'
CPU_RUN = 40907811869
DEPLOY_RUN = 40911312468
DELIVERY_ARTIFACT = 12875084203
ASR_CHILD = 'ly-asrseam-20261030-4b387838'
ASR_RUN = 40920169914
ASR_DELIVERY_ARTIFACT = 12919627941


def proof_error(cpu, deploy, root=ROOT):
    fixture=json.loads((root/'tests/fixtures/linyuan_story_title_20261029.json').read_text())
    if (cpu.get('passed') is not True or cpu.get('model')!='qwen3.5:9b'
            or cpu.get('source_run_id')!=40817982883 or cpu.get('source_sha256')!=MOTHER
            or cpu.get('fixture_sha256')!=digest(fixture)):
        return 'actual_cpu_case_not_verified'
    try: validate_candidate_pool(cpu['result'])
    except (ValueError,KeyError,TypeError): return 'cpu_candidate_binding_invalid'
    health=deploy.get('health') or {}
    if (deploy.get('verification_complete') is not True
            or (health.get('experiment_runtime') or {}).get('passed') is not True
            or health.get('daily_limit')!=4
            or (health.get('publishing_experiment') or {}).get('id')!=F.experiment.EXPERIMENT_ID
            or (health.get('publishing_experiment') or {}).get('treatment_slots')!=[2,4]):
        return 'deployment_not_verified'
    for path,key in [('linyuan/publishing_experiment.py','publishing_experiment_sha256'),
                     ('linyuan/fc/index.py','code_sha256')]:
        if hashlib.sha256((root/path).read_bytes()).hexdigest()!=health.get(key):
            return 'deployed_code_differs'
    return None


def plan(state, stock, active, seeds, *, seam_proof=None):
    child_slug=CHILD
    if seam_proof is not None:
        from asr_overlap_replay import proof_error as seam_error
        if seam_error(seam_proof):return None,'actual_asr_seam_not_verified'
        if not any(e.get('slug')==CHILD for e in state.get('dispatched',[])):
            return None,'prior_title_retry_missing'
        child_slug=ASR_CHILD
    if not F.experiment.preparing():return None,'experiment_closed'
    if any(e.get('slug')==child_slug for e in state.get('dispatched',[])):
        return None,'already_attempted_or_unresolved'
    if not stock.get('inventory_fresh'):
        return None,'inventory_unknown'
    ready=stock.get('experiment_ready') or {}
    if sum(ready.values())>=4 and ready.get('landscape',0)>=2:
        return None,'trial_reserve_covered'
    running=[r for r in active if r.get('status') in {'queued','in_progress'}]
    if len(running)>=F.MAX_ACTIVE_SOURCES:
        return None,'worker_limit'
    entries=[e for e in state.get('dispatched',[]) if e.get('slug')==ORIGINAL]
    if not entries:return None,'original_intent_missing'
    original=max(entries,key=lambda e:float(e.get('ts') or 0))
    if (original.get('source_url')!=SOURCE or original.get('experiment_mother_sha256')!=MOTHER
            or original.get('editorial_profile')!=TREATMENT or original.get('failed')
            or original.get('source_quality_rejected') or ORIGINAL in state.get('published',{})):
        return None,'source_or_state_changed'
    if restriction(SOURCE,seeds):return None,'source_restricted'
    related={e['slug'] for e in state['dispatched']
             if e.get('source_url')==SOURCE or e.get('experiment_mother_sha256')==MOTHER}
    if any(str(r.get('display_title','')).split(' · ')[-1] in related for r in running):
        return None,'related_source_running'
    # Copy identity/context, never old counters, media approvals or selected
    # ranges. The parent and its accepted C backups remain untouched.
    keep=('video_id','source_url','source','author','title','speech_date_context','publish_time',
          'required_presentation_version','production_rules_version','experiment_selection_revision',
          'experiment_mother_sha256','experiment_parent_slug')
    child={k:deepcopy(original[k]) for k in keep if k in original}
    child.update(slug=child_slug,key=child_slug,asset_url=SOURCE,editorial_profile=TREATMENT,
        output_layout='landscape',ts=int(time.time()),delay_hours=0,
        title_recovery_evidence=dict(rule='canonical-company-story-title-20261030-v1',
            original_slug=ORIGINAL,original_run_id=40817982883,cpu_run_id=CPU_RUN,
            deployment_run_id=DEPLOY_RUN,source_sha256=MOTHER,media_approved=False))
    if seam_proof is not None:child['asr_seam_recovery_evidence']=deepcopy(seam_proof)
    return child,None


def request(cpu, deploy, *, seam_proof=None):
    issue=proof_error(cpu,deploy)
    if issue:return dict(dispatched=0,reason=issue)
    state=F.load_state()
    inventory=json.loads(F.gh('GET','/contents/'+F.SOURCE_INVENTORY_KEY+'?ref=main',raw=True))
    stock=F.source_inventory(state,inventory)
    active=[]
    for status in ('queued','in_progress'):
        active+=F.gh('GET',f'/actions/workflows/{F.WF_PRODUCE}/runs?status={status}&per_page=100').get('workflow_runs',[])
    seeds=json.loads(F.gh('GET','/contents/linyuan/bilibili_seeds.json?ref=main',raw=True))['seeds']
    child,issue=plan(state,stock,active,seeds,seam_proof=seam_proof)
    if issue:return dict(dispatched=0,reason=issue)
    # Persist the deterministic intent before the existing dispatcher posts.
    # An unknown response must not produce a duplicate attempt on a callback.
    state['dispatched'].append(child)
    F.save_state(state)
    artifact=ASR_DELIVERY_ARTIFACT if seam_proof is not None else DELIVERY_ARTIFACT
    reason=('完整原始识别重放验证的分块交界修复' if seam_proof is not None else '真实CPU和部署读回已验证的标题修复')
    sent=F._request_quality_reprocess(state,child,child['slug'],
        reason+'：重新自动选段，全部成片门禁照常验收',artifact)
    child['title_recovery_evidence']['dispatch_confirmed']=bool(sent)
    F.save_state(state)
    return dict(dispatched=int(bool(sent)),slug=child['slug'],actual_treatment_stock_added=0,
                reason='normal_production_requested' if sent else 'request_unresolved_no_automatic_replay')


def main():
    import subprocess,tempfile
    report=dict(dispatched=0,actual_treatment_stock_added=0)
    with tempfile.TemporaryDirectory() as directory:
        directory=Path(directory)
        for run,name,dest in [(CPU_RUN,'story-title-recovery','cpu'),
                              (DEPLOY_RUN,'production-verification','deploy')]:
            info=F.gh('GET',f'/actions/runs/{run}')
            if info.get('status')!='completed' or info.get('conclusion')!='success':
                report['reason']='verification_still_pending'
                break
            subprocess.run(['gh','run','download',str(run),'--repo',F.REPO,'--name',name,
                            '--dir',str(directory/dest)],check=True,capture_output=True)
        else:
            cpu_paths=list((directory/'cpu').rglob('story-title-recovery.json'))
            deploy_paths=list((directory/'deploy').rglob('production-verification.json'))
            if len(cpu_paths)!=1 or len(deploy_paths)!=1:
                raise ValueError('Unambiguous verification reports required')
            cpu=json.loads(cpu_paths[0].read_text())
            deploy=json.loads(deploy_paths[0].read_text())
            report=F.run_with_lease('dispatch',lambda:request(cpu,deploy))
            if report.get('reason')=='already_attempted_or_unresolved':
                # A failed title retry exposed a distinct raw-ASR seam defect.
                # Replay its complete immutable alignment before granting one
                # separate, deterministic normal retry. Old attempts survive.
                run=F.gh('GET',f'/actions/runs/{ASR_RUN}')
                if run.get('status')=='completed' and run.get('conclusion')=='failure':
                    subprocess.run(['gh','run','download',str(ASR_RUN),'--repo',F.REPO,
                        '--name','editorial-'+CHILD,'--dir',str(directory/'asr')],check=True,capture_output=True)
                    paths=list((directory/'asr').rglob('qwen_cpu/aligned.json'))
                    if len(paths)!=1:raise ValueError('Unambiguous actual alignment required')
                    from asr_overlap_replay import replay
                    seam=replay(paths[0])
                    report=F.run_with_lease('dispatch',lambda:request(cpu,deploy,seam_proof=seam))
    Path('verified-title-recovery-request.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
