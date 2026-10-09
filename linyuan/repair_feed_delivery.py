"""Recover only the known accepted batch whose feed crop was not archived.

Regenerate a deterministic thumbnail from the exact archived cover. Never
rewrite media, subtitles, metadata, title evidence or an approval flag.
"""
import argparse
import hashlib
import json
from pathlib import Path

SLUG = 'ly-1006-32e054'
RUN_ID = 37865193167
ARTIFACT_ID = 11589227435
MEDIA_SHA256 = '9d1bd249ccc2816744ab3938c766507382cf425d285f9f92dcac9a203867c226'
COVER_SHA256 = 'ac5531d85fcfc15f431cb018a52c971a7916a55e2b2222d9e072435ba94f7c7b'


def repair(directory, validate=None):
    from PIL import Image
    from presentation import FEED_SAFE_CROP, write_feed_square
    from batch_delivery import archive_accepted
    if validate is None:
        from source_supply import validate_part
        validate = validate_part
    directory = Path(directory)
    raw_meta = (directory / 'meta.json').read_bytes()
    data = json.loads(raw_meta)
    rows = data if isinstance(data, list) else [data]
    if len(rows) != 1:
        raise ValueError('Recovery requires the exact single accepted part')
    meta = rows[0]
    proof = meta.get('cover_proof') or {}
    if (meta.get('slug') != SLUG or meta.get('final') != 'final_1.mp4'
            or meta.get('cover') != 'cover_1.jpg'
            or proof.get('version') != 4
            or proof.get('feed_safe_crop') != list(FEED_SAFE_CROP)
            or proof.get('feed_square') != 'cover_1_feed_square.jpg'):
        raise ValueError('Not the source-bound October 9 delivery incident')
    before = {}
    for name, expected in (('final_1.mp4', MEDIA_SHA256), ('cover_1.jpg', COVER_SHA256)):
        path = directory / name
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_symlink() or actual != expected:
            raise ValueError('Incident media/cover fingerprint mismatch: ' + name)
        before[name] = actual
    error = validate(meta, directory)
    if error != '信息流方形封面验收件缺失':
        raise ValueError('Recovery only accepts the missing-feed verdict: ' + str(error))
    with Image.open(directory / meta['cover']) as image:
        feed = write_feed_square(image, directory / meta['cover'])
    error = validate(meta, directory)
    if error:
        raise ValueError('Recovered delivery failed full current validation: ' + error)
    if (directory / 'meta.json').read_bytes() != raw_meta:
        raise ValueError('Recovery must not change metadata')
    if any(hashlib.sha256((directory / n).read_bytes()).hexdigest() != h for n,h in before.items()):
        raise ValueError('Recovery must not change media or original cover')
    archive_accepted(directory, SLUG)
    report = dict(slug=SLUG, original_run_id=RUN_ID, original_artifact_id=ARTIFACT_ID,
        media_sha256=MEDIA_SHA256, cover_sha256=COVER_SHA256, restored_file=feed.name,
        restored_sha256=hashlib.sha256(feed.read_bytes()).hexdigest(),
        metadata_unchanged=True, media_unchanged=True, full_validation_passed=True)
    (directory / 'recovery-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    args = parser.parse_args()
    print(json.dumps(repair(args.directory),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
