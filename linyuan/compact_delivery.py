"""A minimal fully validated publishing bundle, with unchanged original bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

from artifact_range import delivery_files


def compact(directory, slug, validate=None):
    if validate is None:
        from source_supply import validate_part
        validate = validate_part
    if not re.fullmatch(r'ly-[a-zA-Z0-9-]+', slug):
        raise ValueError('Invalid delivery slug')
    directory = Path(directory)
    metadata = directory / 'meta.json'
    if metadata.is_symlink():
        raise ValueError('Metadata symlink is not allowed')
    raw = metadata.read_bytes()
    data = json.loads(raw)
    rows = data if isinstance(data, list) else [data]
    if not 1 <= len(rows) <= 40:
        raise ValueError('Invalid accepted part count')
    names = {'meta.json'}
    for row in rows:
        if row.get('slug') != slug:
            raise ValueError('Wrong delivery identity')
        names.update(delivery_files(row))
        error = validate(row, directory)
        if error:
            raise ValueError('Original delivery fails full validation: ' + str(error))
    hashes = {}
    for name in sorted(names):
        path = directory / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Unsafe or missing publishing file: ' + name)
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    for row in rows:
        if hashes[row['final']] != (row.get('fingerprints') or {}).get('sha256'):
            raise ValueError('Original media fingerprint mismatch')
    destination = directory / '_publish_ready'
    # No replacement of accepted or previous delivery directories.
    destination.mkdir()
    for name in sorted(names):
        shutil.copyfile(directory / name, destination / name)
        if hashlib.sha256((destination / name).read_bytes()).hexdigest() != hashes[name]:
            raise ValueError('Publishing copy changed')
    for row in rows:
        error = validate(row, destination)
        if error:
            raise ValueError('Compact delivery fails full validation: ' + str(error))
    report = dict(slug=slug, parts=len(rows), files=hashes,
        metadata_unchanged=(destination / 'meta.json').read_bytes() == raw,
        media_unchanged=True, full_validation_passed=True,
        publishing_bytes=sum((destination / name).stat().st_size for name in names),
        original_delivery_bytes=sum(path.stat().st_size for path in (directory / '_accepted').glob('*') if path.is_file()))
    (directory / 'compact-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--slug', required=True)
    args = parser.parse_args()
    print(json.dumps(compact(args.directory, args.slug), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
