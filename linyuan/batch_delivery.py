"""Durable per-part acceptance and manifest-only delivery packaging."""
import json
import hashlib
import math
import shutil
from pathlib import Path


def organize_interview_series(rows, *, source_duration, complete=False,
                              selective_retry=False):
    """Group accepted long-interview arguments, not arbitrary time chunks.

    Never number a partial checkpoint or a selected-parts repair. A plan ID
    includes the accepted ranges, so a later different selection cannot silently
    reuse the same episode numbers. This is navigation, not a platform season.
    """
    if not complete or selective_retry or source_duration < 900:
        return rows, None
    episodes = [r for r in rows if r.get('content_type') != 'full_interview']
    if not 2 <= len(episodes) <= 20:
        return rows, None
    mother = episodes[0].get('source_sha256', '')
    if (not isinstance(mother, str) or len(mother) != 64
            or any(c not in '0123456789abcdef' for c in mother)):
        return rows, None
    spans = []
    for row in episodes:
        segments = row.get('segments') or []
        # A serial episode is one complete continuous answer. Multi-answer
        # compilations and incomplete metadata remain ordinary standalone clips.
        if row.get('source_sha256') != mother or len(segments) != 1:
            return rows, None
        start, end = segments[0].get('start'), segments[0].get('end')
        if (isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start < end <= source_duration + 1):
            return rows, None
        title = row.get('title')
        if not isinstance(title, str) or not title.strip() or len(title) > 160:
            return rows, None
        if '\n' in title or '\r' in title:
            return rows, None
        spans.append((start, end, row))
    spans.sort(key=lambda s: (s[0], s[1]))
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        return rows, None
    if len({r['title'].strip() for _, _, r in spans}) != len(spans):
        return rows, None
    keys = [hashlib.sha256(json.dumps([mother, start, end],
            separators=(',', ':')).encode()).hexdigest()[:16]
            for start, end, _ in spans]
    plan_id = hashlib.sha256(('\n'.join([mother, *keys])).encode()).hexdigest()[:20]
    manifest = dict(version=1, kind='accepted_interview_series',
        series_id='interview-' + mother[:20], plan_id=plan_id,
        source_sha256=mother, episode_count=len(spans),
        platform_collection_created=False, publication_status='not_verified',
        episodes=[dict(episode_number=i, episode_key=key, title=r['title'],
                       final=r.get('final'), source_start=start, source_end=end)
                  for i, ((start, end, r), key) in enumerate(zip(spans, keys), 1)])
    directory = '\n'.join(f"{e['episode_number']}. {e['title']}"
                          for e in manifest['episodes'])
    enriched = []
    lookup = {id(r): i for i, (_, _, r) in enumerate(spans, 1)}
    for row in rows:
        number = lookup.get(id(row))
        if number is None:
            enriched.append(row)
            continue
        nav = (f'同场访谈分集：第 {number}/{len(spans)} 集'
               '（各集独立成篇，发布以主页为准）\n分集目录：\n' + directory)
        desc = row.get('desc') or ''
        # Do not truncate approved copy or exceed the upload description limit.
        if len(desc) + len(nav) + 2 > 2000:
            return rows, None
        enriched.append({**row, 'desc': desc + '\n\n' + nav,
            'interview_series': {**manifest, 'episode_number': number,
                'episode_key': keys[number - 1],
                'previous_title': spans[number - 2][2]['title'] if number > 1 else None,
                'next_title': spans[number][2]['title'] if number < len(spans) else None}})
    return enriched, manifest


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
    if any(row.get('interview_series') for row in rows):
        manifest = out / 'interview_series.json'
        if manifest.is_symlink() or not manifest.is_file():
            raise ValueError('Missing accepted interview series manifest')
        names.add(manifest.name)
    for row in rows:
        for key in ('final', 'cover', 'preview_30s', 'contact_sheet_6'):
            name = row[key]
            if Path(name).name != name or not (out / name).is_file():
                raise ValueError(f'Invalid/missing accepted artifact: {name}')
            names.add(name)
        for name in row.get('subtitle_files') or []:
            if Path(name).name != name or not (out / name).is_file():
                raise ValueError(f'Invalid/missing accepted subtitles: {name}')
            names.add(name)
        if row.get('subtitle_edit_proof_version')==1:
            for name in row.get('subtitle_edit_proofs') or []:
                if (Path(name).name!=name or not name.startswith('subtitle_edit_proof')
                        or not name.endswith('.json') or not (out/name).is_file()):
                    raise ValueError(f'Invalid/missing subtitle edit proof: {name}')
                names.add(name)
        cover_proof = row.get('cover_proof') or {}
        if cover_proof.get('feed_safe_crop') is not None:
            feed = cover_proof.get('feed_preview') or cover_proof.get('feed_square')
            if (not isinstance(feed, str) or Path(feed).name != feed
                    or not feed.endswith('.jpg') or (out / feed).is_symlink()
                    or not (out / feed).is_file()):
                raise ValueError(f'Invalid/missing accepted feed square: {feed}')
            names.add(feed)
        thumb = cover_proof.get('thumbnail')
        if thumb and Path(thumb).name == thumb:
            names.add(thumb)
    delivery = out / '_accepted'
    if delivery.exists():
        shutil.rmtree(delivery)
    delivery.mkdir()
    for name in names:
        if (out / name).is_file():
            shutil.copy2(out / name, delivery / name)
            shutil.copy2(out / name, delivery / f'{slug}.{name}')
    return len(rows)
