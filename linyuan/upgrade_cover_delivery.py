"""Upgrade legacy verified portrait covers, without re-encoding accepted media."""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def upgrade(directory, slug, run_id, artifact_id, validate=None, font=None):
    from PIL import Image
    import editorial_cover as C
    import presentation as V
    if validate is None:
        from source_supply import validate_part
        validate = validate_part
    if not re.fullmatch(r'ly-[a-zA-Z0-9-]+', slug) or min(run_id, artifact_id) <= 0:
        raise ValueError('Invalid upgrade origin')
    directory = Path(directory)
    metadata = directory / 'meta.json'
    if metadata.is_symlink():
        raise ValueError('Metadata symlink is not allowed')
    raw = metadata.read_bytes()
    data = json.loads(raw)
    rows = data if isinstance(data, list) else [data]
    if not 1 <= len(rows) <= 40:
        raise ValueError('Invalid accepted part count')
    immutable, pending = {}, []
    # Validate the entire original accepted bundle before modifying any cover.
    # Fail closed on unrelated problems; this is not a quality-gate workaround.
    for row in rows:
        if row.get('slug') != slug:
            raise ValueError('Delivery identity mismatch')
        names = [row.get('final'), row.get('cover')]
        names += list(row.get('subtitle_files') or []) + list(row.get('subtitle_edit_proofs') or [])
        for name in names:
            if not isinstance(name, str) or not name or Path(name).name != name or '\\' in name:
                raise ValueError('Unsafe original filename')
            path = directory / name
            if path.is_symlink() or not path.is_file():
                raise ValueError('Missing or unsafe original file')
        error = validate(copy.deepcopy(row), directory)
        if error:
            raise ValueError('Original delivery fails full validation: ' + str(error))
        for name in [row['final']] + names[2:]:
            immutable[name] = digest(directory / name)
        if immutable[row['final']] != (row.get('fingerprints') or {}).get('sha256'):
            raise ValueError('Original media fingerprint mismatch')
        proof = row.get('cover_proof') or {}
        if proof.get('cover_layout_version') == 3:
            continue
        if (proof.get('style') != 'dark' or row.get('cover_person_image_verified') is not True
                or row.get('cover_person_image_source') != 'authority_reference'):
            continue  # A scene face cannot be relabelled as an authority portrait.
        box = proof.get('face_box')
        if (not isinstance(box, list) or len(box) != 4
                or any(type(n) not in (int, float) or not math.isfinite(n) for n in box)
                or not 0 <= box[0] < box[2] <= 1280
                or not 0 <= box[1] < box[3] <= 720):
            raise ValueError('Archived portrait bounds are invalid')
        if not isinstance(row.get('cover_title'), str) or not row['cover_title']:
            raise ValueError('Missing original reviewed cover copy')
        cover = directory / row['cover']
        with Image.open(cover) as image:
            if image.size != (1280, 720):
                raise ValueError('Archived cover canvas mismatch')
            portrait = image.crop(tuple(map(int, box))).copy()
        if min(portrait.size) < 160:
            continue  # Do not magnify a tiny or unverified placeholder.
        pending.append((row, portrait, digest(cover)))
    changes = []
    font = font or (C.font_path() if pending else None)
    for row, portrait, old_hash in pending:
        cover = directory / row['cover']
        portrait_path = directory / (cover.stem + '_archived_portrait.png')
        if portrait_path.exists() or portrait_path.is_symlink():
            raise ValueError('Portrait upgrade scratch path already exists')
        portrait.save(portrait_path)
        try:
            style = V.select_cover_style(False, row['cover_title'])
            image, lines, size, boxes, face = V.dark_cover(portrait_path,
                row['cover_title'], row['speaker'], font, C.font_face_index(font), palette=style)
            image.save(cover, quality=95)
            proof = V.cover_proof(image, cover, lines, size, boxes, style=style,
                face_box=face, feed_crop=V.FEED_WIDE_CROP)
        finally:
            portrait_path.unlink(missing_ok=True)
        provenance = dict(version=1, original_artifact_id=artifact_id, original_run_id=run_id,
            original_metadata_sha256=hashlib.sha256(raw).hexdigest(),
            original_cover_sha256=old_hash, portrait_source='archived_verified_cover_crop',
            portrait_source_resolution=list(portrait.size),
            original_copy_preserved=True, media_and_subtitles_preserved=True)
        row['cover_proof'] = {**proof, 'portrait_provenance': provenance}
        row['cover_upgrade'] = provenance
        changes.append(dict(cover=row['cover'], old_sha256=old_hash,
                            new_sha256=digest(cover), style=style))
    if changes:
        metadata.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    for row in rows:
        error = validate(copy.deepcopy(row), directory)
        if error:
            raise ValueError('Upgraded delivery fails full validation: ' + str(error))
    if any(digest(directory / name) != sha for name, sha in immutable.items()):
        raise ValueError('Accepted video or subtitles changed during cover upgrade')
    report = dict(slug=slug, changed=bool(changes), changes=changes,
        original_artifact_id=artifact_id, original_run_id=run_id,
        original_file_sha256=immutable, full_validation_passed=True,
        media_and_subtitles_unchanged=True, titles_unchanged=True)
    (directory / 'cover-upgrade-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--slug', required=True)
    parser.add_argument('--run-id', required=True, type=int)
    parser.add_argument('--artifact-id', required=True, type=int)
    args = parser.parse_args()
    print(json.dumps(upgrade(args.directory, args.slug, args.run_id, args.artifact_id), ensure_ascii=False))


if __name__ == '__main__':
    main()
