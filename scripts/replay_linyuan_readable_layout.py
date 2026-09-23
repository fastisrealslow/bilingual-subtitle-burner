"""Isolated larger-caption layout trial on the exact same accepted input files."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))


def main():
    import landscape
    import replay_linyuan_layout as replay
    original = landscape.layout

    def readable(style=None):
        spec = original(style)
        if landscape.style_name(style) == 'quiet':
            spec.update(live_region=dict(x=248, y=0, width=784, height=584),
                        subtitle_region=dict(x=180, y=584, width=920, height=136),
                        subtitle_font_px=44, line_capacity=20,
                        template='landscape-readable-footer-diagnostic')
        return spec

    landscape.layout = readable
    try:
        replay.main()
    finally:
        landscape.layout = original
        # Preserve the intervention separately from production layout versions.
        # This template is not an accepted production default or yield credit.
        index = sys.argv.index('--output')
        out = Path(sys.argv[index+1])
        if out.is_dir():
            (out / 'layout-intervention.json').write_text(json.dumps(dict(
                subtitle_font_px=44, line_capacity=20, picture_height=584,
                production_default_changed=False, source_yield_credit=False,
                implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                scope='Layout diagnostic only; preserve source pixels, all displayed words, title, cover, timing and audio'), indent=2)+'\n')


if __name__ == '__main__':
    main()
