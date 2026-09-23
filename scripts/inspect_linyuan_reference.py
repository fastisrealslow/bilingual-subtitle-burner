#!/usr/bin/env python3
"""Export view-only reference evidence, never production footage or approvals."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bvid', required=True)
    ap.add_argument('--source', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--records', type=Path, help='Frozen reference cohort; defaults to the original twenty')
    a = ap.parse_args()
    root = Path(__file__).resolve().parents[1]
    records = json.loads((a.records or root/'linyuan/simulations/benchmark-20260921/references-20.json').read_text())
    reference = next(r for r in records['rows'] if r['bvid'] == a.bvid)
    probe = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(a.source)]))
    stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    assert any(s['codec_type'] == 'audio' for s in probe['streams'])
    duration = float(probe['format']['duration'])
    assert abs(duration-reference['duration']) <= max(2, reference['duration']*.02), 'partial reference stream'
    a.out.mkdir(parents=True, exist_ok=True)
    times = sorted(set(round(min(duration-.2, t), 3) for t in
                       [0.5, 2, 5, 10, 15, duration*.25, duration*.4,
                        duration*.55, duration*.7, duration*.82, duration*.92, duration-1]))
    for i, t in enumerate(times):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(t), '-i', str(a.source),
                        '-frames:v', '1', '-q:v', '2', str(a.out/f'frame-{i:02d}.jpg')],
                       check=True, timeout=120)
    # Keep the complete original timeline/audio; do not enlarge a low-resolution stream.
    preview = a.out/'full-review.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(a.source),
                    '-map', '0:v:0', '-map', '0:a:0',
                    '-vf', "scale=w='min(iw,1280)':h='min(ih,1280)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '26', '-c:a', 'aac',
                    '-b:a', '96k', '-movflags', '+faststart', str(preview)], check=True, timeout=1200)
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(preview), '-map', '0:v', '-map', '0:a',
                    '-f', 'null', '-'], check=True, timeout=300)
    h = hashlib.sha256()
    with a.source.open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            h.update(chunk)
    proof = dict(bvid=a.bvid, reference=reference, source_sha256=h.hexdigest(),
                 actual_dimensions=[stream['width'], stream['height']], duration=duration,
                 frame_times=times, frame_count=len(times), complete_timeline=True,
                 preview_full_video_audio_decode=True, used_for_production=False,
                 audio_semantically_reviewed=False,
                 preview_sha256=hashlib.sha256(preview.read_bytes()).hexdigest())
    (a.out/'inspection.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    main()
