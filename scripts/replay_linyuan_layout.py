"""Replay only layout on a frozen accepted media artifact; never count new yield."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))


def main():
    import produce_cn as producer
    import landscape
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sample', type=int, required=True)
    parser.add_argument('--input-run-id',default='35565180877')
    args = parser.parse_args()
    files = list(args.input.glob('meta*.json'))
    if len(files) != 1:
        raise ValueError('expected one frozen final metadata file')
    meta = json.loads(files[0].read_text())
    video = args.input / meta['final']
    original_hash = producer._file_sha256(video)
    if original_hash != meta['fingerprints']['sha256']:
        raise ValueError('input final changed')
    declared_region = meta['layout_proof'].get('live_region')
    if (declared_region is None
            and meta.get('audio_card_template') == 'live_editorial_v4_readable'
            and meta['layout_proof']['canvas'] == {'width': 720, 'height': 1280}):
        # This versioned legacy card records its canvas/template, while its
        # live rectangle is the producer's fixed, independently checked region.
        declared_region = producer.LIVE_REGION
    if declared_region != landscape.source_window(meta):
        raise ValueError('source window differs from actual input layout')
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / 'media'
    shutil.copytree(args.input, output)
    row = dict(id=args.sample, commit=os.environ.get('GITHUB_SHA'),
        run_id=os.environ.get('GITHUB_RUN_ID'), input_run_id=args.input_run_id,
        source_sha256=meta['source_sha256'], input_final_sha256=original_hash,
        status='unresolved', editorial_approved=False, source_yield_credit=False,
        scope='Same video/audio/copy; layout-only diagnostic. Not a new source pass.')
    started = time.monotonic()
    try:
        required=('live_region_verified','no_qr_verified','no_black_bars_verified',
                  'review_assets_verified','subtitle_word_boundaries_verified',
                  'subtitle_semantic_groups_verified','title_quality_verified')
        if (any(meta.get(k) is not True for k in required)
                or meta.get('corner_review',{}).get('media_sha256')!=original_hash
                or meta.get('corner_review',{}).get('passed') is not True):
            raise ValueError('layout replay needs a byte-bound accepted input')
        revised = landscape.reframe(meta, output, args.output/'work')
        for key in ('title','cover_title','source_sha256','segments','subtitle_text_sha256'):
            if revised[key]!=meta[key]:raise ValueError('Layout-only replay changed '+key)
        if producer._file_sha256(output/revised['cover'])!=producer._file_sha256(args.input/meta['cover']):
            raise ValueError('Layout-only replay changed the actual cover')
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(output/revised['final']),
            '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], check=True, timeout=300)
        (output / files[0].name).write_text(json.dumps(revised, ensure_ascii=False, indent=2))
        row.update(status='layout_verified', output_final_sha256=revised['fingerprints']['sha256'],
            layout_proof=revised['layout_proof'], reframe=revised['landscape_reframe'],
            video_audio_full_decode=True, title=revised['title'],cover_title=revised['cover_title'],
            copy_and_cover_unchanged=True)
    except Exception as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'
    row['seconds'] = round(time.monotonic()-started, 2)
    row['original_unchanged'] = producer._file_sha256(video) == original_hash
    (args.output/'result.json').write_text(json.dumps(row, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(row, ensure_ascii=False))
    if row['status'] != 'layout_verified' or not row['original_unchanged']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
