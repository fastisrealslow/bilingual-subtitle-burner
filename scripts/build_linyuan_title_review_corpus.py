"""Freeze every selected verified final's actual, byte-bound title input.

Never rewrite ASR or substitute manually improved copy. Several versions of
one source stay separate when their actual final bytes differ. This is a text
replay corpus, not a source-yield denominator or an editorial pass list.
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def freeze(row):
    final=ROOT/row['file']
    if not row.get('video_audio_full_decode') or sha(final)!=row['sha256']:
        raise ValueError('Final lacks matching local decode evidence: '+str(final))
    meta_path=final.parent/'meta.json'
    meta=read(meta_path)
    if meta['final']!=final.name or meta['source_sha256']!=row['source_sha256']:
        raise ValueError('Final metadata/source mismatch')
    if meta['fingerprints']['sha256']!=row['sha256']:
        raise ValueError('Metadata is not bound to the actual MP4')
    report=final.parent.with_name(final.parent.name.replace('simulation-media-','simulation-report-'))
    work=report/'evidence/_tmp'
    raw=read(work/'cues_raw.json')
    selected=[c for c in raw if any(c['start']>=s['start']-.05 and c['end']<=s['end']+.05
                                   for s in meta['segments'])]
    transcript=''.join(c['text'] for c in selected)
    digest=hashlib.sha256(transcript.encode()).hexdigest()
    copies=[]
    for path in work.glob('copywrite*.json'):
        copy=read(path)
        if (copy.get('copy_identity',{}).get('transcript_sha256')==digest
                and copy.get('title')==meta['title'] and copy.get('cover_title')==meta['cover_title']):
            copies.append((path,copy))
    if len(copies)!=1:
        raise ValueError('Need exactly one matching actual title input: '+str(final))
    path,copy=copies[0]
    identity=copy['copy_identity']
    return dict(id=f"source{row['id']}-run{row['run_id']}",source_id=row['id'],
        source_run_id=row['run_id'],source_code=row['tested_sha'],
        source_sha256=row['source_sha256'],final_sha256=row['sha256'],
        final_file=row['file'],transcript_sha256=digest,
        source_cues_file=str((work/'cues_raw.json').relative_to(ROOT)),
        source_cues_sha256=sha(work/'cues_raw.json'),
        actual_copy_file=str(path.relative_to(ROOT)),actual_copy_sha256=sha(path),
        copy_suffix='_full' if path.stem=='copywrite_full' else '',
        reviewed_title=identity.get('reviewed_title'),occasion=identity['occasion'],
        old_title=meta['title'],old_cover=meta['cover_title'],duration=meta['duration_sec'],
        cues=selected,editorial_approved=False,
        scope='Exact transcript from an actual decoded final; no hand-written target copy. Text replay does not add a produced video.')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--verification',type=Path,action='append',required=True)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    seen=set();cases=[]
    for path in args.verification:
        for row in read(path):
            if not row.get('video_audio_full_decode') or row['sha256'] in seen:
                continue
            cases.append(freeze(row));seen.add(row['sha256'])
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(cases,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(cases=len(cases),source_ids=sorted({c['source_id'] for c in cases}),
                         final_sha256_count=len(seen),file=str(args.out))))


if __name__=='__main__':
    main()
