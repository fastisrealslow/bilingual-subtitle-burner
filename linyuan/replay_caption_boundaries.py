"""Verify the incident's actual captions without model inference or publication."""
import argparse
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch

import produce_cn as production
from presentation import layout_for, wrap_words


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('--source-sha', default='a8c31e64cb10672706c38ce9f9efc4cacf77312eafeda6ff68a467b437c9e27b')
    parser.add_argument('--start-cue', type=int, default=56)
    parser.add_argument('--end-cue', type=int, default=106, help='Exclusive cue index')
    parser.add_argument('--duration', type=float, default=140.64)
    args=parser.parse_args()
    source=Path(args.source)/'_tmp'
    report=json.loads((source/'source_quality.json').read_text())
    assert report['source_sha256']==args.source_sha
    cues=json.loads((source/'cues_raw.json').read_text())[args.start_cue:args.end_cue]
    assert abs(cues[-1]['end']-cues[0]['start']-args.duration)<.01
    start=cues[0]['start']
    entries=[dict(start_sec=c['start']-start,end_sec=c['end']-start,zh=c['text'],en='') for c in cues]
    layout=layout_for(720,1280,True)
    cache=source/'replayed_caption_groups.json'
    cache.unlink(missing_ok=True)
    with patch.object(production,'llm',side_effect=AssertionError('Caption replay called the model')):
        groups=production.semantic_caption_entries(entries,'',layout,cache)
    normalize=lambda text:re.sub(r'[\s，。！？；：、]','',text)
    assert ''.join(normalize(g['zh']) for g in groups)==normalize(''.join(c['text'] for c in cues))
    assert all(.25<=g['end_sec']-g['start_sec']<=8 for g in groups)
    assert all(len(wrap_words(g['zh'],g.get('line_capacity',layout['line_capacity'])))<=2 for g in groups)
    print(json.dumps(dict(caption_screens=len(groups),source_seconds=args.duration,
        character_conservation=True,word_and_layout_checks=True,model_calls=0),ensure_ascii=False))


if __name__=='__main__':
    main()
