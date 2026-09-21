"""Build and verify a local, commit-bound candidate package; never deploy."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def git(*args, cwd=ROOT):
    return subprocess.check_output(['git',*args],cwd=cwd)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--ref',default='HEAD')
    ap.add_argument('--base',required=True)
    ap.add_argument('--output',type=Path,default=ROOT/'output/replacement-20260921')
    args=ap.parse_args()
    ref=git('rev-parse',args.ref+'^{commit}').decode().strip()
    base=git('rev-parse',args.base+'^{commit}').decode().strip()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    label='linyuan-candidate-'+ref[:7]
    archive=out/(label+'.zip');patch=out/(label+'.patch')
    git('archive','--format=zip','--prefix='+label+'/',str(ref),'-o',str(archive))
    patch.write_bytes(git('diff','--binary',base,ref))
    files={}
    for entry in git('ls-tree','-r','-z',ref).split(b'\0'):
        if not entry:continue
        meta,path=entry.split(b'\t',1);mode,kind,blob=meta.decode().split()
        if kind!='blob':raise ValueError('Unsupported non-file tree entry')
        files[path.decode()]=dict(mode=mode,git_blob=blob)
    with zipfile.ZipFile(archive) as z:
        if z.testzip():raise ValueError('Archive CRC failure')
        paths={name[len(label)+1:]:name for name in z.namelist() if not name.endswith('/')}
        if paths.keys()!=files.keys():raise ValueError('Archive file set differs from commit')
        for path,name in paths.items():
            content=z.read(name)
            blob=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()
            if blob!=files[path]['git_blob']:raise ValueError('Archive content differs: '+path)
    # Check patch application on a fresh worktree at the stated base, without
    # touching the user's checkout, applying the patch, or changing main.
    with tempfile.TemporaryDirectory(prefix='linyuan-release-check-') as temporary:
        checkout=Path(temporary)/'base'
        git('worktree','add','--detach',str(checkout),base)
        try:
            git('apply','--check',str(patch),cwd=checkout)
        finally:
            git('worktree','remove',str(checkout))
    changed=git('diff','--name-only',base,ref).decode().splitlines()
    runtime=[p for p in changed if '/.automation/' in '/'+p or
             p.endswith(('/dashboard/data.json','/up_videos.json','/monitor_v2.db'))]
    if runtime:raise ValueError('Runtime state differs from base: '+', '.join(runtime))
    manifest=dict(commit=ref,base=base,tree=git('rev-parse',ref+'^{tree}').decode().strip(),
        archive=archive.name,patch=patch.name,files=files,changed_files=changed,
        archive_matches_commit=True,patch_applies_to_base=True,runtime_state_changed=runtime,
        production_accepted=False,publication_authorized=False,
        note='Candidate code only. 30% production yield and editorial quality are not certified. '+
             'ZIP includes historical runtime state: do not overwrite live state with it. '+
             'Local media evidence remains under output/benchmark-20260921.')
    manifest_path=out/(label+'-manifest.json')
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    sums=[]
    for path in (archive,patch,manifest_path):
        sums.append(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.name)
    (out/'SHA256SUMS').write_text('\n'.join(sums)+'\n')
    print(json.dumps(dict(commit=ref,archive=str(archive),patch=str(patch),files=len(files),
        archive_matches_commit=True,patch_applies_to_base=True),ensure_ascii=False))


if __name__=='__main__':main()
