"""Stop only explicitly listed live normal runs with exact known source bans.

Never delete jobs, artifacts, dispatch samples or failure history. This does not
cancel research previews and never restarts a stopped run.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
from source_admission_guard import official_page_key,restriction

REPO='fastisrealslow/bilingual-subtitle-burner'
REQUESTS=(
    (38448038043,'ly-1013-ca5a26','BV1tTi5BrEES'),
    (38448052503,'ly-1013-85151b','BV1eXtZzgE7C'),
    (38448622858,'ly-1013-b6bc66','BV1HXejzDEhn'),
    (38449213557,'ly-1013-776f97','BV1qos3zgEu1'),
    (38449570731,'ly-1013-cf8b8e','BV1VimhYiEXr'),
)


def eligible(run,state,request,policies):
    run_id,slug,bvid=request
    if (run.get('id')!=run_id or run.get('head_branch')!='main'
            or (run.get('head_repository') or {}).get('full_name')!=REPO
            or run.get('path')!='.github/workflows/linyuan-produce-cn.yml'
            or run.get('display_title')!='中文源出片 · '+slug
            or run.get('status') not in ('queued','in_progress','waiting','pending','requested')):
        return False
    entries=[e for e in state.get('dispatched') or [] if e.get('slug')==slug]
    if not entries:return False
    entry=max(entries,key=lambda e:float(e.get('ts') or 0))
    return (official_page_key(entry.get('source_url'))==('bilibili',bvid)
            and any(official_page_key(row.get('source_url'))==('bilibili',bvid)
                    and row.get('no_reprint')==1 for row in policies)
            and bool(restriction(entry['source_url'],[],policies)))


def api(method,path,raw=False):
    command=['gh','api','--method',method]
    if raw:
        if method!='GET':raise ValueError('Raw observation must be read-only')
        command+=['-H','Accept: application/vnd.github.raw+json']
    command+=['repos/'+REPO+'/'+path]
    output=subprocess.check_output(command,text=True,timeout=30)
    return json.loads(output) if output.strip() else {}


def main():
    if os.environ.get('GITHUB_REPOSITORY')!=REPO:raise SystemExit('Wrong repository')
    policies=json.loads((ROOT/'linyuan/source_restrictions.json').read_text())['pages']
    # The real state file exceeds the contents API's inline-base64 ceiling.
    # Read actual JSON bytes with the raw media type, not empty metadata.content.
    state=api('GET','contents/site/fc_state.json?ref=main',raw=True)
    results=[]
    for request in REQUESTS:
        run_id,slug,bvid=request
        try:
            run=api('GET','actions/runs/'+str(run_id))
            if not eligible(run,state,request,policies):
                results.append(dict(run_id=run_id,slug=slug,status='terminal_or_not_exact_restricted_normal_run'))
                continue
            api('POST','actions/runs/'+str(run_id)+'/cancel')
            results.append(dict(run_id=run_id,slug=slug,bvid=bvid,status='cancellation_requested',
                reason='Exact known no_reprint=1; normal production only; preserve all artifacts and failure samples'))
        except Exception as exc:
            results.append(dict(run_id=run_id,slug=slug,status='observation_or_cancellation_unavailable',error_type=type(exc).__name__))
    Path('restricted-run-stop-receipt.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(results,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
