"""Rebuild only the reported moving window; never regenerate speech or copy."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import produce_cn as p
from live_tracking import render_tracked
from audience_copy import dated_description
from presentation import verify_render
from source_supply import validate_part

BVID = 'BV1UEpC6bEQB'
SLUG = 'ly-1013-5a1382'
OLD_SHA = '8cdaf95155cefe57d0aad3858368a0eb44420d17d831ae1a9bcd0ba4ee88d342'
SOURCE_SHA = 'ced49dbeb34439f98149da57e9d361a18f8d62c852dea4a49b6b54729d2b73ea'
TITLE = '林园：牛市大概率延续，可转债进可攻退可守'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audio_digest(path):
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path),
        '-map', '0:a:0', '-c:a', 'copy', '-f', 'adts', 'pipe:1'], timeout=60)
    return hashlib.sha256(raw).hexdigest()


def original_part(directory):
    rows = json.loads((Path(directory) / 'meta.json').read_text())
    rows = rows if isinstance(rows, list) else [rows]
    matches = [m for m in rows if m.get('title') == TITLE and m.get('final') == 'final_5.mp4']
    if len(matches) != 1: raise ValueError('Reported original part unavailable')
    meta = matches[0]
    if (meta.get('source_sha256') != SOURCE_SHA
            or sha(Path(directory)/meta['final']) != OLD_SHA
            or meta.get('segments') != [dict(start=70.52, end=172.44,
                reason='保留原始提问和其后连续回答；未声称模型语义审核通过')]):
        raise ValueError('Original speech/file binding changed')
    return copy.deepcopy(meta)


def build(original, source, editorial, output):
    original, source, output = Path(original), Path(source), Path(output)
    meta = original_part(original)
    if sha(source) != SOURCE_SHA: raise ValueError('Mother media hash changed')
    output.mkdir(parents=True, exist_ok=True)
    work = output/'_tmp'; work.mkdir(exist_ok=True)
    reference = p._download_speaker_reference('林园', work)
    source_report = json.loads((Path(editorial)/'_tmp/source_quality.json').read_text())
    start, duration = 70.52, 101.92
    marks = p.selected_segment_exclusions(source, start, duration,
        work/'source-corners', source_report.get('detected_corner_logos') or ())
    tracked = work/'tracked.mp4'
    tracking = render_tracked(source, start, duration, tracked, reference, p._local_face_models(),
        exclusions=marks, overlay_probe=lambda frame,index:p.selected_frame_logos(
            frame, work/'source-corners', index, shot_local=True))
    if tracking['framing']['geometry_reframes']:
        raise ValueError('Repair still changes shot scale')
    framing = tracking['framing']
    if framing['pan_frames'] > .1*(framing['pan_frames']+framing['held_frames']):
        raise ValueError('Repair still has sustained tracking movement')
    p.verify_live_region_after_render(tracked, reference=reference,
        live_region=dict(x=0, y=0, width=632, height=470))
    final = output/'final.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-i',str(original/meta['final']),
        '-i',str(tracked),'-filter_complex',
        '[1:v]fade=t=in:st=0:d=0.4,fade=t=out:st=101.52:d=0.4[window];'
        '[0:v][window]overlay=44:360:shortest=1[v]',
        '-map','[v]','-map','0:a:0','-c:v','libx264','-crf','18','-preset','veryfast',
        '-c:a','copy','-movflags','+faststart',str(final)],check=True,timeout=240)
    if audio_digest(final) != audio_digest(original/meta['final']):
        raise ValueError('Repair changed original encoded speech audio')
    for name in [meta['cover'], meta['cover_proof'].get('thumbnail'),
                 meta['cover_proof'].get('feed_preview'), meta['cover_proof'].get('feed_square'),
                 *meta.get('subtitle_files', []), *meta.get('subtitle_edit_proofs', [])]:
        if not name: continue
        if Path(name).name != name: raise ValueError('Unsafe original asset filename')
        shutil.copy2(original/name, output/name)
    meta.update(final='final.mp4', framing_proofs=[framing],
        **verify_render(final, meta['layout_proof']))
    meta.update(p.verify_live_region_after_render(final, reference=reference))
    meta['final_live_identity'] = p.verify_final_live_identity(final, work, '林园', '')
    meta['fingerprints'] = p.build_content_fingerprints(final,
        p.editorial.subtitle_files_text(output, meta['subtitle_files']))
    meta['duration_sec'] = float(p.probe(final, 'format=duration'))
    meta['desc'] = dated_description(meta['desc'], meta, dict(
        title='林园炒股秘籍《新浪财经采访》2020.12.14', publish_time='2026-03-29T11:37:57+00:00'))
    for _ in range(2):
        error = validate_part(meta, output)
        if error: raise ValueError(error)
    meta['tracking_revision'] = dict(bvid=BVID, old_sha256=OLD_SHA,
        source_sha256=SOURCE_SHA, audio_bitstream_sha256=audio_digest(final),
        same_speech_interval=True, original_cover_sha256=sha(output/meta['cover']),
        independent_media_validation_passes=2, new_posts=0)
    (output/'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(dict(bvid=BVID,sha256=sha(final),matched_ratio=tracking['matched_ratio'],
        pan_frames=framing['pan_frames'],held_frames=framing['held_frames'],
        complete_checks=True,new_posts=0),ensure_ascii=False),flush=True)


if __name__ == '__main__':
    ap=argparse.ArgumentParser()
    for name in ['original','source','editorial','output']:ap.add_argument('--'+name,required=True)
    args=ap.parse_args();build(**vars(args))
