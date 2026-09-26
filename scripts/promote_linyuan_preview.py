"""Validate and copy one exact automatic preview; never invoke FC or publish."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))


def request_fields(data):
    if not isinstance(data,dict) or set(data)!={'run_id','artifact_id','commit','sha256'}:
        raise ValueError('Promotion accepts only exact run/artifact/commit/file identities')
    if any(type(data[k]) is not int or data[k]<=0 for k in ('run_id','artifact_id')):
        raise ValueError('Invalid run or artifact ID')
    for key,length in [('commit',40),('sha256',64)]:
        if not re.fullmatch('[0-9a-f]{'+str(length)+'}',str(data[key])):
            raise ValueError('Invalid '+key)
    return data


def validate_origin(request,run,artifact,repo):
    slug='preview-'+str(request['run_id'])
    if (run.get('id')!=request['run_id'] or run.get('head_sha')!=request['commit']
            or run.get('head_branch')!='codex/automatic-preview'
            or (run.get('head_repository') or {}).get('full_name')!=repo
            or run.get('path')!='.github/workflows/linyuan-automatic-preview.yml'
            or run.get('conclusion')!='success'):
        raise ValueError('Preview origin is not the exact successful automatic run')
    if (artifact.get('id')!=request['artifact_id'] or artifact.get('expired')
            or artifact.get('name')!='preview-deliver-'+slug
            or (artifact.get('workflow_run') or {}).get('id')!=request['run_id']
            or not 0<artifact.get('size_in_bytes',0)<1024**3):
        raise ValueError('Preview artifact identity, expiry or size mismatch')
    return slug


def verify_directory(directory,request,slug,validator=None):
    import editorial_policy
    if validator is None:
        # Reuse local validators for final pixels, subtitles, cover and hashes.
        # This function does not call the remote FC service.
        from source_supply import validate_part
        validator=validate_part
    directory=Path(directory)
    metadata=json.loads((directory/'meta.json').read_text())
    rows=metadata if isinstance(metadata,list) else [metadata]
    if len(rows)!=1 or not isinstance(rows[0],dict):
        raise ValueError('Single-video promotion requires exactly one accepted output')
    row=rows[0];review=row.get('editorial_review') or {}
    if (row.get('slug')!=slug or row.get('automatic_only') is not True
            or row.get('reviewed_title_record') or row.get('title_handoff')
            or review.get('automatic_only') is not True
            or review.get('review_protocol')!=2
            or editorial_policy.model_review_skipped(review)
            or editorial_policy.review_error(review)
            or ((row.get('title_rewrite') or {}).get('review') or {}).get('method')!='cpu_text_review'):
        raise ValueError('Missing automatic selection and actual model review evidence')
    name=row.get('final','')
    if not name or Path(name).name!=name:
        raise ValueError('Invalid final filename')
    digest=hashlib.sha256((directory/name).read_bytes()).hexdigest()
    if digest!=request['sha256'] or digest!=(row.get('fingerprints') or {}).get('sha256'):
        raise ValueError('Actual accepted video bytes do not match request')
    issue=validator(row,directory)
    if issue:raise ValueError(issue)
    return row


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--request',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    request=request_fields(json.loads(args.request.read_text()))
    repo=os.environ['GITHUB_REPOSITORY']
    def api(path):
        return json.loads(subprocess.check_output(['gh','api',f'repos/{repo}/'+path],text=True,timeout=60))
    run=api(f"actions/runs/{request['run_id']}")
    artifact=api(f"actions/artifacts/{request['artifact_id']}")
    slug=validate_origin(request,run,artifact,repo)
    args.output.mkdir(parents=True,exist_ok=False)
    subprocess.run(['gh','run','download',str(request['run_id']),'--repo',repo,
                    '--name',artifact['name'],'--dir',str(args.output)],check=True,timeout=300)
    row=verify_directory(args.output,request,slug)
    original=api('contents/.github/linyuan-preview-request.json?ref='+request['commit'])
    source=json.loads(base64.b64decode(original['content']))['source']
    receipt=dict(producer=request,slug=slug,source_url=source,title=row['title'],
        cover_title=row['cover_title'],expected_sha256=request['sha256'],
        automatic_checks_passed=True,fc_invoked=False,published=False,
        note='Exact original video and copy; no re-rendering or per-video editorial patches')
    Path('preview-promotion-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    with open(os.environ['GITHUB_OUTPUT'],'a') as out:out.write('slug='+slug+'\n')
    print(json.dumps(receipt,ensure_ascii=False))


if __name__=='__main__':main()
