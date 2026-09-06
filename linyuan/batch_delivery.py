"""Durable per-part acceptance and manifest-only delivery packaging."""
import json
import shutil
from pathlib import Path


def write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def quarantine_part(out, suffix):
    """A failed render must never match the deliverable root's MP4 globs."""
    out = Path(out)
    dest = out / '_tmp' / 'rejected' / (suffix.strip('_') or '1')
    dest.mkdir(parents=True, exist_ok=True)
    names = [f'final{suffix}.mp4', f'preview_30s{suffix}.mp4',
             f'contact_sheet_6{suffix}.jpg',
             f'cover{suffix}.jpg' if suffix else 'cover_16x9.jpg']
    for name in names:
        for path in (out / name, out / (name + '.proof.json')):
            if path.is_file():
                path.replace(dest / path.name)


def archive_accepted(out, slug):
    """Copy only manifest-listed accepted outputs, never rejected/debug media."""
    out = Path(out)
    meta = json.loads((out / 'meta.json').read_text(encoding='utf-8'))
    rows = meta if isinstance(meta, list) else [meta]
    if not rows:
        raise ValueError('No accepted parts')
    names = {'meta.json', 'batch_report.json'}
    for row in rows:
        for key in ('final', 'cover', 'preview_30s', 'contact_sheet_6'):
            name = row[key]
            if Path(name).name != name or not (out / name).is_file():
                raise ValueError(f'Invalid/missing accepted artifact: {name}')
            names.add(name)
        thumb = (row.get('cover_proof') or {}).get('thumbnail')
        if thumb and Path(thumb).name == thumb:
            names.add(thumb)
    for name in names:
        if (out / name).is_file():
            shutil.copy2(out / name, out / f'{slug}.{name}')
    return len(rows)
