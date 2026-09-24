"""Render a bounded display cleanup on the frozen source32 final, without publishing."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import caption_readability as captions
from editorial_policy import ass_dialogue_text


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    source_dir = ROOT / 'output/benchmark-20260921/source32-title-35884655321/simulation-media-sim-0916-032-b93410'
    out = ROOT / 'output/benchmark-20260921/source32-caption-cleanup-sep24'
    meta = json.loads((source_dir / 'meta.json').read_text())
    source = source_dir / meta['final']
    if digest(source) != meta['fingerprints']['sha256']:
        raise ValueError('Frozen final has changed')
    if meta['layout_proof']['template'] != 'landscape-live-v3-footer':
        raise ValueError('Only the isolated classic footer is supported')
    proof = json.loads((source_dir / meta['subtitle_edit_proofs'][0]).read_text())
    _, old_text = captions.replay_edit_proof(proof)
    _, new_proof = captions.clean_entries(proof['raw_entries'])
    replacements = [('垄垄断', '垄断'), ('虽虽然', '虽然')]
    expected = old_text
    ass = (source_dir / meta['subtitle_files'][0]).read_text(encoding='utf-8-sig')
    for old, new in replacements:
        if old_text.count(old) != 1 or ass.count(old) != 1:
            raise ValueError('Observed cleanup no longer matches this final')
        expected = expected.replace(old, new)
        ass = ass.replace(old, new)
    if expected != new_proof['display_text']:
        raise ValueError('New policy changed more than the two reviewed partial words')
    if captions.display_payload_text(ass_dialogue_text(ass)) != captions.display_payload_text(expected):
        raise ValueError('Rendered captions do not match reproducible display policy')
    out.mkdir(exist_ok=True)
    target = out / 'final.mp4'
    ass_path = out / 'captions.ass'
    ass_path.write_text(ass, encoding='utf-8-sig')
    (out / 'subtitle-edit-proof.json').write_text(json.dumps(new_proof, ensure_ascii=False, indent=2))
    ff = ROOT / 'output/local-tools/ffmpeg'
    fonts = ROOT / 'output/local-tools/fonts'
    if not (fonts / 'NotoSansCJKsc-Bold.otf').is_file():
        raise ValueError('The actual Chinese subtitle font is missing')
    region = meta['layout_proof']['subtitle_region']
    live = meta['layout_proof']['live_region']
    if region['y'] < live['y'] + live['height']:
        raise ValueError('Subtitle replacement would erase original source pixels')
    vf = (f"drawbox=x={region['x']}:y={region['y']}:w={region['width']}:h={region['height']}:color=0x75222f:t=fill,"
          f"ass=filename='{ass_path}':fontsdir='{fonts}'")
    subprocess.run([str(ff), '-y', '-v', 'error', '-i', str(source), '-vf', vf,
        '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'libx264', '-threads', '2',
        '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'copy',
        '-movflags', '+faststart', str(target)], check=True, timeout=600)
    def audio_hash(path):
        return subprocess.check_output([str(ff), '-v', 'error', '-i', str(path),
            '-map', '0:a:0', '-c', 'copy', '-f', 'hash', '-'], text=True).strip()
    if audio_hash(source) != audio_hash(target):
        raise ValueError('Audio stream changed')
    subprocess.run([str(ff), '-v', 'error', '-xerror', '-i', str(target),
        '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], check=True, timeout=300)
    result = dict(source_file=str(source.relative_to(ROOT)), source_sha256=digest(source),
        file=str(target.relative_to(ROOT)), sha256=digest(target), title=meta['title'],
        cover_title=meta['cover_title'], cover=str((source_dir / meta['cover']).relative_to(ROOT)),
        duration=meta['duration_sec'], policy_version=captions.VERSION,
        audio_stream_unchanged=True, full_av_decode=True, source_yield_credit=0,
        editorial_approved=False, production_gate_verified=False,
        scope='Full frozen final, same audio/title/cover/layout/timing; two reproducible display deletions only. Re-encoded diagnostic, not production approval.')
    (out / 'review.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
