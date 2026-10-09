"""Restore derived feed crops only; never edit an accepted video's evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def repair(directory, slug, run_id, artifact_id, validate=None):
    from PIL import Image
    from presentation import FEED_SAFE_CROP, write_feed_square
    from batch_delivery import archive_accepted
    if validate is None:
        from source_supply import validate_part
        validate=validate_part
    if not re.fullmatch(r'ly-[a-zA-Z0-9-]+',slug) or min(run_id,artifact_id)<=0:
        raise ValueError('Invalid recovery origin')
    directory=Path(directory)
    raw=(directory/'meta.json').read_bytes()
    data=json.loads(raw);rows=data if isinstance(data,list) else [data]
    if not 1<=len(rows)<=40:raise ValueError('Invalid accepted delivery size')
    originals={};pending=[]
    for meta in rows:
        if meta.get('slug')!=slug:raise ValueError('Delivery slug does not match trusted origin')
        proof=meta.get('cover_proof') or {}
        names=[meta.get('final'),meta.get('cover')]
        names+=list(meta.get('subtitle_files') or [])+list(meta.get('subtitle_edit_proofs') or [])
        for name in names:
            if not isinstance(name,str) or not name or Path(name).name!=name or '\\' in name:
                raise ValueError('Unsafe original delivery filename')
            path=directory/name
            if path.is_symlink():raise ValueError('Original delivery symlink is not allowed')
            originals[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        if originals[meta['final']]!=(meta.get('fingerprints') or {}).get('sha256'):
            raise ValueError('Actual original media fingerprint mismatch')
        error=validate(meta,directory)
        if error is None:continue
        if error!='信息流方形封面验收件缺失':
            raise ValueError('Not a missing-feed-only incident: '+str(error))
        cover=Path(meta['cover'])
        expected=cover.stem+'_feed_square.jpg'
        if (proof.get('version')!=4 or proof.get('feed_safe_crop')!=list(FEED_SAFE_CROP)
                or cover.suffix!='.jpg' or proof.get('feed_square')!=expected
                or (directory/expected).exists() or (directory/expected).is_symlink()):
            raise ValueError('Feed crop contract mismatch')
        pending.append(meta)
    restored=[]
    for meta in pending:
        with Image.open(directory/meta['cover']) as image:
            if image.size!=(1280,720):raise ValueError('Original cover canvas mismatch')
            feed=write_feed_square(image,directory/meta['cover'])
        restored.append(dict(file=feed.name,sha256=hashlib.sha256(feed.read_bytes()).hexdigest()))
    # Recheck every accepted part, including unaffected parts of a mixed bundle.
    for meta in rows:
        error=validate(meta,directory)
        if error:raise ValueError('Recovered delivery failed full validation: '+str(error))
    if (directory/'meta.json').read_bytes()!=raw or any(
            hashlib.sha256((directory/n).read_bytes()).hexdigest()!=h for n,h in originals.items()):
        raise ValueError('Original media, captions or metadata changed during recovery')
    if restored:archive_accepted(directory,slug)
    report=dict(slug=slug,original_run_id=run_id,original_artifact_id=artifact_id,
        changed=bool(restored),restored=restored,original_file_sha256=originals,
        metadata_unchanged=True,full_validation_passed=True)
    (directory/'recovery-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',required=True)
    parser.add_argument('--slug',required=True)
    parser.add_argument('--run-id',required=True,type=int)
    parser.add_argument('--artifact-id',required=True,type=int)
    args=parser.parse_args()
    print(json.dumps(repair(args.directory,args.slug,args.run_id,args.artifact_id),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
