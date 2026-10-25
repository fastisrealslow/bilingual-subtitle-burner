"""Recognize a source-bound accepted checkpoint; never approve its media."""
import argparse
import json
import os
from pathlib import Path
import re


def accepted_checkpoint(directory, slug):
    directory=Path(directory)
    report_path=directory/'batch_report.json'
    metadata_path=directory/'meta.json'
    if not report_path.exists():
        return dict(accepted=False,parts=0)
    if report_path.is_symlink() or metadata_path.is_symlink():
        raise ValueError('Checkpoint cannot use symlink metadata')
    report=json.loads(report_path.read_text())
    count=report.get('accepted')
    if report.get('slug')!=slug or type(count) is not int or not 0<=count<=40:
        raise ValueError('Invalid checkpoint batch identity/count')
    if count==0:
        if metadata_path.exists():
            raise ValueError('Zero-output checkpoint has stale accepted metadata')
        return dict(accepted=False,parts=0)
    metadata=json.loads(metadata_path.read_text())
    rows=metadata if isinstance(metadata,list) else [metadata]
    context=report.get('source_context') or {}
    source=json.loads((directory/'_tmp/source_quality.json').read_text())
    digest=source.get('source_sha256')
    if (source.get('passed') is not True or context.get('passed') is not True
            or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest)
            or context.get('source_sha256')!=digest or len(rows)!=count):
        raise ValueError('Checkpoint is not bound to its checked source')
    finals=[]
    for row in rows:
        name=row.get('final')
        if (row.get('slug')!=slug or row.get('source_sha256')!=digest
                or not isinstance(name,str) or Path(name).name!=name
                or not name.endswith('.mp4')):
            raise ValueError('Invalid accepted checkpoint part')
        finals.append(name)
    if len(set(finals))!=count or report.get('accepted_finals')!=finals:
        raise ValueError('Checkpoint accepted files differ from its manifest')
    # This only opens the normal archive/compact steps. compact_delivery still
    # rechecks actual MP4/cover/subtitle/identity/motion/hash evidence twice.
    return dict(accepted=True,parts=count,source_sha256=digest,media_approved=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True)
    parser.add_argument('--slug',required=True)
    args=parser.parse_args()
    result=accepted_checkpoint(args.directory,args.slug)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as out:
            out.write('accepted='+str(result['accepted']).lower()+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
