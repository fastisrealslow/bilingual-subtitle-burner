#!/usr/bin/env python3
"""Independently decode downloaded A/B finals; never infer editorial approval."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'output/baseline-comparison-20260921/results'
sys.path.insert(0, str(ROOT / 'linyuan'))
import title_rewrite


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    import cv2
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    catalog = []
    prior_path = RESULTS / 'media-verification.json'
    prior = json.loads(prior_path.read_text()) if prior_path.exists() else []
    previous = {r.get('sha256'): r for r in prior if r.get('video_audio_full_decode')}
    for report_path in sorted(RESULTS.glob('ab-report-*/report.json')):
        report = json.loads(report_path.read_text())
        variant = report_path.parent.name.split('-')[2]
        folder = RESULTS / report_path.parent.name.replace('ab-report-', 'ab-media-', 1)
        for final in report.get('finals', []):
            row = dict(id=report['sample']['id'], slug=report['sample']['slug'], variant=variant,
                       tested_sha=report['tested_sha'], run_id=report['run_id'],
                       source_sha256=report['source_sha256'],
                       video_audio_full_decode=False, editorial_approved=False,
                       editorial_scope='Automatic hash, duration and full audio/video decode only; manual observations are recorded separately')
            try:
                name = final['file']
                if Path(name).name != name:
                    raise ValueError('unexpected final path')
                video = folder / name
                row['sha256'] = digest(video)
                if row['sha256'] != final['sha256']:
                    raise ValueError('final differs from acceptance report')
                meta_path = folder / name.replace('final', 'meta').replace('.mp4', '.json')
                if not meta_path.exists():
                    meta_path = folder / 'meta.json'
                meta = json.loads(meta_path.read_text())
                if meta['fingerprints']['sha256'] != row['sha256'] or meta['title'] != final['title']:
                    raise ValueError('metadata is not bound to this final')
                # Reuse only a completed local decode of these exact bytes.
                if row['sha256'] not in previous:
                    subprocess.run([ffmpeg, '-v', 'error', '-xerror', '-i', str(video),
                                    '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'],
                                   check=True, timeout=300, capture_output=True)
                cap = cv2.VideoCapture(str(video))
                try:
                    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / cap.get(cv2.CAP_PROP_FPS)
                finally:
                    cap.release()
                if abs(duration - final['duration']) > 1:
                    raise ValueError('decoded duration differs from report')
                row.update(video_audio_full_decode=True, dimensions=[width, height], duration=duration,
                           file=str(video.relative_to(ROOT)), title=meta['title'], cover_title=meta['cover_title'],
                           cover=str((folder / meta['cover']).relative_to(ROOT)),
                           contact_sheet=str((folder / meta['contact_sheet_6']).relative_to(ROOT)),
                           current_title_gate_issue=title_rewrite.error(meta['title'], meta['title_rewrite']))
            except (OSError, ValueError, KeyError, ZeroDivisionError, subprocess.SubprocessError) as exc:
                row['verification_error'] = str(exc)
            catalog.append(row)
    prior_path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(finals=len(catalog), fully_decoded=sum(r['video_audio_full_decode'] for r in catalog)), ensure_ascii=False))


if __name__ == '__main__':
    main()
