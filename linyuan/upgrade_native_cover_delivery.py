"""Backfill native scene covers without changing accepted media or cohorts."""
import copy
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile


def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda:handle.read(4*1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def upgrade(directory, output, mother, slug, run_id, artifact_id, *, validate=None):
    """Produce a NEW validated bundle. Original delivery bytes are read-only.

    Only original native pixels in the approved continuous interval are used.
    Failed scene gates keep that part's accepted cover, honestly unchanged.
    A better cover NEVER changes baseline/story experiment membership.
    """
    import produce_cn as P
    from artifact_range import delivery_files
    if validate is None:
        from source_supply import validate_part
        validate=validate_part
    if (not re.fullmatch(r'ly-[a-zA-Z0-9-]+',slug)
            or type(run_id) is not int or type(artifact_id) is not int
            or min(run_id,artifact_id)<=0):
        raise ValueError('Invalid original delivery provenance')
    directory,output,mother=map(Path,(directory,output,mother))
    if output.exists() or output.is_symlink():raise ValueError('Output must be new')
    metadata=directory/'meta.json'
    if metadata.is_symlink():raise ValueError('Unsafe original metadata')
    raw=metadata.read_bytes();data=json.loads(raw)
    rows=data if isinstance(data,list) else [data]
    if not 1<=len(rows)<=40:raise ValueError('Invalid accepted part count')
    source_sha=sha(mother)
    names={'meta.json'}
    for row in rows:
        if row.get('slug')!=slug or row.get('source_sha256')!=source_sha:
            raise ValueError('Original mother/delivery binding differs')
        names.update(delivery_files(row))
    for name in names:
        if (not isinstance(name,str) or Path(name).name!=name or '\\' in name
                or (directory/name).is_symlink() or not (directory/name).is_file()):
            raise ValueError('Missing or unsafe accepted file')
    immutable={name:sha(directory/name) for name in names}
    for row in rows:
        error=validate(copy.deepcopy(row),directory)
        if error:raise ValueError('Original delivery fails full validation: '+str(error))
        if immutable[row['final']]!=(row.get('fingerprints') or {}).get('sha256'):
            raise ValueError('Original video fingerprint differs')
    report=dict(slug=slug,changed=False,changes=[],skipped=[],
        original_run_id=run_id,original_artifact_id=artifact_id,
        original_metadata_sha256=hashlib.sha256(raw).hexdigest(),source_sha256=source_sha,
        experiment_membership_unchanged=True,published=False)
    with tempfile.TemporaryDirectory(prefix='native-cover-upgrade-') as temporary:
        stage=Path(temporary)
        for name in names:shutil.copyfile(directory/name,stage/name)
        previews=stage/'_native-cover-previews';previews.mkdir()
        for index,row in enumerate(rows):
            if (row.get('cover_proof') or {}).get('style')=='scene':continue
            spans=row.get('segments') or []
            if len(spans)!=1:
                report['skipped'].append(dict(index=index,reason='requires_one_approved_continuous_interval'))
                continue
            start,end=spans[0]['start'],spans[0]['end']
            preview=previews/f'native-preview-{index}.jpg'
            try:
                reference=P._download_speaker_reference(row['speaker'],previews)
                style,reason=P.make_live_clip_cover(mother,None,start,end,row['cover_title'],
                    row['speaker'],preview,reference_path=reference,style='scene',
                    allow_editorial_fallback=False)
                proof=json.loads(Path(str(preview)+'.proof.json').read_text())
                if (style!='scene' or reason is not None or proof.get('style')!='scene'
                        or proof.get('sha256')!=sha(preview) or any(proof.get(k) is not True for k in
                    ('no_added_text','no_black_bars','no_qr','face_fully_visible','feed_safe_face'))):
                    raise P.VisualQualityError('Native cover proof is incomplete')
            except P.VisualQualityError as exc:
                report['skipped'].append(dict(index=index,reason=str(exc)))
                continue
            cover=stage/row['cover'];old_sha=sha(cover)
            shutil.copyfile(preview,cover)
            # Preview filenames are part of the proof; retain them unchanged
            # and let the normal compact delivery whitelist include them.
            for key in ('thumbnail','feed_preview'):
                name=proof.get(key)
                if name:
                    if (not isinstance(name,str) or Path(name).name!=name or '\\' in name
                            or (previews/name).is_symlink() or not (previews/name).is_file()):
                        raise ValueError('Unsafe native cover companion file')
                    shutil.copyfile(previews/name,stage/name)
            provenance=dict(version=1,original_run_id=run_id,original_artifact_id=artifact_id,
                original_metadata_sha256=report['original_metadata_sha256'],
                original_cover_sha256=old_sha,source_sha256=source_sha,
                source_interval=[start,end],video_sha256=immutable[row['final']],
                media_and_subtitles_preserved=True,experiment_membership_unchanged=True)
            row.update(cover_proof=proof,cover_person_image_source='verified_source_frame',
                cover_person_image_verified=True,native_cover_upgrade=provenance)
            error=validate(copy.deepcopy(row),stage)
            if error:raise ValueError('Native upgraded part fails full validation: '+str(error))
            report['changes'].append(dict(index=index,cover=row['cover'],old_sha256=old_sha,
                new_sha256=proof['sha256'],source_interval=[start,end]))
        report['changed']=bool(report['changes'])
        (stage/'meta.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
        changed_covers={change['cover'] for change in report['changes']}
        for name,before in immutable.items():
            if name not in changed_covers|{'meta.json'} and sha(stage/name)!=before:
                raise ValueError('Accepted media or subtitle bytes changed')
        for row in rows:
            error=validate(copy.deepcopy(row),stage)
            if error:raise ValueError('New delivery fails full validation: '+str(error))
        # Detect an input mutation too, not just changes in the staging copy.
        if any(sha(directory/name)!=before for name,before in immutable.items()):
            raise ValueError('Original accepted delivery changed')
        report.update(full_validation_passed=True,original_delivery_unchanged=True,
            media_and_subtitles_unchanged=True,titles_unchanged=True)
        output.mkdir(parents=True)
        keep={'meta.json'}
        for row in rows:keep.update(delivery_files(row))
        for name in keep:shutil.copyfile(stage/name,output/name)
        (output/'native-cover-upgrade-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--mother',required=True)
    parser.add_argument('--slug',required=True)
    parser.add_argument('--run-id',required=True,type=int)
    parser.add_argument('--artifact-id',required=True,type=int)
    args=parser.parse_args(argv)
    print(json.dumps(upgrade(args.directory,args.output,args.mother,args.slug,
        args.run_id,args.artifact_id),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
