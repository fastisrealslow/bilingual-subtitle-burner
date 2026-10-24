"""Frozen real-delivery caption comparison; never registers or publishes media."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import caption_readability as R
from editorial_policy import ass_dialogue_text


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delivery', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root, out = args.delivery, args.out
    raw_meta = (root / 'meta.json').read_bytes()
    data = json.loads(raw_meta)
    rows = data if isinstance(data, list) else [data]
    assert len(rows) == 1
    meta = rows[0]
    source = root / meta['final']
    assert digest(source) == meta['fingerprints']['sha256'] == '0e57dd3415c7aaa5d66fbb91442b2c7cc15cef7dfc85e993ada71088f3374919'
    old = json.loads((root / meta['subtitle_edit_proofs'][0]).read_text())
    R.replay_edit_proof(old)
    _, new = R.clean_entries(old['raw_entries'])
    assert old['display_text'].endswith('平常心了，？')
    assert new['display_text'] == old['display_text'].replace('平常心了，？', '平常心了，是吧？')
    assert new['raw_text'] == old['raw_text']
    R.replay_edit_proof(new)
    ass = (root / meta['subtitle_files'][0]).read_text(encoding='utf-8-sig')
    assert ass.count('平常心了？') == 1
    lines = ass.splitlines()
    changed = 0
    for i, line in enumerate(lines):
        if not line.startswith('Dialogue:') or '平常心了？' not in line:
            continue
        fields = line.split(',', 9)
        assert fields[2] == '0:00:25.36'
        fields[2] = '0:00:25.76'  # Existing source anchors, not inferred timing.
        fields[9] = fields[9].replace('平常心了？', '平常心了是吧？')
        lines[i] = ','.join(fields)
        changed += 1
    assert changed == 1
    ass = '\n'.join(lines) + '\n'
    assert R.display_payload_text(ass_dialogue_text(ass)) == R.display_payload_text(new['display_text'])
    layout = meta['layout_proof']
    assert layout['subtitle_font_px'] == 75 and layout['subtitle_max_lines'] == 1
    region = layout['source_region']
    width, height = region['width'], region['height']
    assert region['x'] == region['y'] == 0
    assert height < layout['subtitle_region']['y']
    out.mkdir(parents=True, exist_ok=True)
    ass_path = out / 'captions.ass'
    ass_path.write_text(ass, encoding='utf-8-sig')
    target = out / 'confirmation-tag-preview.mp4'
    # Lossless encode makes decoded source pixels above the footer comparable
    # byte for byte. The original compressed audio packets are copied intact.
    vf = f'drawbox=x=0:y={height}:w={width}:h=ih-{height}:color=0x101c2b:t=fill,ass={ass_path}'
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-copyts', '-i', str(source), '-vf', vf,
        '-map', '0:v:0', '-map', '0:a:0', '-c:v', 'libx264', '-threads', '2',
        '-preset', 'veryfast', '-crf', '0', '-pix_fmt', 'yuv420p', '-c:a', 'copy',
        '-fps_mode:v', 'passthrough', '-enc_time_base:v', '-1', '-avoid_negative_ts', 'disabled',
        str(target)], check=True, timeout=600)
    def stream_hash(path, audio=False):
        command = ['ffmpeg', '-v', 'error', '-copyts', '-i', str(path)]
        command += ['-map', '0:a:0', '-c', 'copy'] if audio else [
            '-map', '0:v:0', '-vf', f'crop={width}:{height}:0:0', '-pix_fmt', 'yuv420p',
            '-fps_mode:v', 'passthrough', '-enc_time_base:v', '-1']
        return subprocess.check_output(command + ['-f', 'hash', '-'], text=True, timeout=300).strip()
    for audio in (False, True):
        assert stream_hash(source, audio) == stream_hash(target, audio), 'Original picture/audio changed'
    def clocks(path):
        raw = subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
            'stream=codec_type,start_time,duration,nb_frames', '-of', 'json', str(path)], text=True, timeout=30)
        return {s['codec_type']: s for s in json.loads(raw)['streams'] if s['codec_type'] in {'video', 'audio'}}
    before, after = clocks(source), clocks(target)
    assert set(before) == set(after) == {'video', 'audio'}
    for kind in before:
        for field in ('start_time', 'duration'):
            assert abs(float(before[kind][field]) - float(after[kind][field])) < .001, 'Audio/video clock changed'
        assert before[kind].get('nb_frames') == after[kind].get('nb_frames'), 'Frame/packet count changed'
    subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(target),
        '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], check=True, timeout=300)
    assert (root / 'meta.json').read_bytes() == raw_meta
    (out / 'subtitle-edit-proof.json').write_text(json.dumps(new, ensure_ascii=False, indent=2))
    report = dict(passed=True, diagnostic_only=True, registered_stock=False, published=False,
        source_video_sha256=digest(source), preview_sha256=digest(target),
        original_picture_pixels_unchanged=True, audio_packets_unchanged=True, full_av_decode=True,
        audio_video_clocks_unchanged=True, original_streams=before, preview_streams=after,
        font_px=75, max_lines=1, changed_screens=1, old_policy=old['version'], new_policy=new['version'],
        old_tail='平常心了，？', new_tail='平常心了，是吧？',
        source_anchor_end=25.76, metadata_unchanged=True, production_gate_approved=False)
    (out / 'review.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
