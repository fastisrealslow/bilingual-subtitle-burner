"""Reuse recent real CPU evidence only when title inputs, code and checks match."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

TITLE_FILES = (
    'linyuan/produce_cn.py', 'linyuan/title_rewrite.py',
    'linyuan/caption_readability.py', 'linyuan/headline_policy.py',
    'linyuan/editorial_policy.py', 'linyuan/presentation.py', 'requirements.txt',
    'tests/fixtures/linyuan_0913_title.json',
    'tests/fixtures/linyuan_0913_landscape_title.json',
)
WORKFLOW = '.github/workflows/linyuan-title-claim-check.yml'


def canonical_workflow(text):
    """Ignore the reuse wrapper and job wall clock, never CPU test assertions."""
    text=text.replace("concurrency:\n  group: title-claim-check-${{ inputs.local_text_model || 'qwen3:8b' }}\n  cancel-in-progress: true\n",'')
    lines=[]; inside=False
    for line in text.splitlines():
        if line.strip()=='# TITLE_REUSE_BEGIN':
            if inside:raise ValueError('Nested reuse block')
            inside=True
        elif line.strip()=='# TITLE_REUSE_END':
            if not inside:raise ValueError('Unmatched reuse block')
            inside=False
        elif not inside and '# TITLE_REUSE_GUARD' not in line:
            # An increased job wall clock preserves the same completed CPU
            # computation. Only the old 40 and new 90 minute ceilings qualify;
            # per-request budgets, prompts and assertions still match exactly.
            if line in ('    timeout-minutes: 40','    timeout-minutes: 90'):
                line='    timeout-minutes: <cpu-verification-wall-clock>'
            lines.append(line)
    if inside:raise ValueError('Unclosed reuse block')
    return '\n'.join(lines).strip()


def validate_results(rows, root=Path('.')):
    from title_rewrite import error, compact
    expected=['linyuan_0913_title.json','linyuan_0913_landscape_title.json']
    if not isinstance(rows,list) or [r.get('fixture') for r in rows]!=expected:
        raise ValueError('Both exact real subtitle cases are required')
    for row in rows:
        fixture=json.loads((root/'tests/fixtures'/row['fixture']).read_text())
        source=''.join(c['text'] for c in fixture['cues'])
        proof=row.get('title_rewrite') or {}
        if (row.get('passed') is not True or proof.get('review',{}).get('method')!='cpu_text_review'
                or len(row.get('title_candidates') or [])!=3
                or proof.get('source_sha256')!=hashlib.sha256(compact(source).encode()).hexdigest()
                or error(row.get('title'),proof,source)):
            raise ValueError('CPU result lacks source-bound, validated title evidence')
        if row['fixture']==expected[1] and any(re.search(
                r'新品.{0,12}(未达预期|不及预期|遇冷|反馈差)',row.get(k,''))
                for k in ('title','cover_title')):
            raise ValueError('Known host hypothesis was falsely approved')


def model_evidence(directory, expected):
    records=[]
    for path in Path(directory).rglob('*.json'):
        if '.llm_cache' not in path.parts:continue
        data=json.loads(path.read_text())
        if data.get('backend')!='local' or data.get('model')!=expected:
            raise ValueError('Actual CPU model differs from requested model')
        if not isinstance(data.get('content'),str) or not data['content']:
            raise ValueError('Missing actual model response')
        records.append(path)
    if not records:raise ValueError('No actual CPU response artifacts')
    return records


def gh(*args):
    return subprocess.check_output(['gh',*args],text=True,timeout=60)


def main():
    import base64
    repo=os.environ['GITHUB_REPOSITORY'];current=os.environ['GITHUB_RUN_ID']
    def api(path):return json.loads(gh('api',f'repos/{repo}/'+path))
    local={line.split('\t',1)[1]:line.split()[2] for line in
           subprocess.check_output(['git','ls-tree','-r','HEAD'],text=True).splitlines()}
    artifacts=api('actions/artifacts?name=title-claim-verification&per_page=12')['artifacts']
    checked=set()
    for artifact in sorted(artifacts,key=lambda a:a['id'],reverse=True):
        record=artifact.get('workflow_run') or {};run_id=record.get('id');head=record.get('head_sha')
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(artifact['created_at'].replace('Z','+00:00'))).total_seconds()
        if artifact.get('expired') or str(run_id)==current or not head or age>6*3600 or head in checked:continue
        checked.add(head)
        try:
            prior={r['path']:r['sha'] for r in api(f'git/trees/{head}?recursive=1')['tree'] if r['type']=='blob'}
            if any(not local.get(p) or local[p]!=prior.get(p) for p in TITLE_FILES):continue
            if local[WORKFLOW]!=prior.get(WORKFLOW):
                blob=api('git/blobs/'+prior[WORKFLOW])
                old=base64.b64decode(blob['content']).decode()
                if canonical_workflow(old)!=canonical_workflow(Path(WORKFLOW).read_text()):continue
            run=api(f'actions/runs/{run_id}')
            if (run.get('head_branch')!='main' or run.get('head_sha')!=head
                    or (run.get('head_repository') or {}).get('full_name')!=repo
                    or run.get('path')!='.github/workflows/fc-production-deploy.yml'):continue
            jobs=api(f'actions/runs/{run_id}/jobs?per_page=100')['jobs']
            passed=any(j['name']=='editorial-check / check' and j['conclusion']=='success' for j in jobs)
            resume=(str(run_id)==os.environ.get('TITLE_RESUME_RUN_ID') and
                    any(j['name']=='editorial-check / check' and j['conclusion'] in ('cancelled','failure') for j in jobs))
            if not passed and not resume:continue
            with tempfile.TemporaryDirectory() as directory:
                gh('run','download',str(run_id),'--repo',repo,'--name','title-claim-verification','--dir',directory)
                paths=list(Path(directory).rglob('title-claim-verification.json'))
                if len(paths)!=1:continue
                rows=json.loads(paths[0].read_text())
                if passed:validate_results(rows)
                evidence=model_evidence(directory,os.environ.get('RUN_TEXT_MODEL','qwen3:8b'))
                destination=Path('linyuan/.llm_cache');destination.mkdir(parents=True,exist_ok=True)
                for path in evidence:shutil.copy2(path,destination/path.name)
            if resume:
                # Raw responses are exact-input computation checkpoints, not
                # approvals. Both cases still execute all current validations;
                # any unfinished request must run on the actual CPU model.
                provenance=dict(resumed_from_run=run_id,tested_head=head,approved=False,
                    model=os.environ.get('RUN_TEXT_MODEL','qwen3:8b'),responses=len(evidence))
                Path('title-proof-reuse.json').write_text(json.dumps(provenance,indent=2))
                with open(os.environ['GITHUB_OUTPUT'],'a') as output:output.write('resumed=true\n')
                with open(os.environ['GITHUB_ENV'],'a') as output:output.write('TITLE_VERIFICATION_RESUMED=true\n')
                print(json.dumps(provenance),flush=True)
                return
            Path('title-claim-verification.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
            provenance=dict(reused_from_run=run_id,tested_head=head,real_cpu_cases=2,
                            model=os.environ.get('RUN_TEXT_MODEL','qwen3:8b'),
                            title_files={p:local[p] for p in TITLE_FILES})
            Path('title-proof-reuse.json').write_text(json.dumps(provenance,indent=2))
            with open(os.environ['GITHUB_OUTPUT'],'a') as output:output.write('reused=true\n')
            print(json.dumps(provenance),flush=True)
            return
        except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError) as exc:
            print('Prior CPU evidence not reusable: '+type(exc).__name__,flush=True)
    print('No identical verified CPU evidence; execute the full real check',flush=True)


if __name__=='__main__':main()
