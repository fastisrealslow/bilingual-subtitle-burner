"""Replay the saved 648-second incident transcript without ASR or publication."""
import argparse
import json
from pathlib import Path
import time

import produce_cn as production


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('artifact_dir', type=Path)
    parser.add_argument('--output', type=Path, default=Path('/tmp/local-text-replay'))
    args = parser.parse_args()
    source = args.artifact_dir / '_tmp'
    report = json.loads((source / 'source_quality.json').read_text())
    expected = 'a8c31e64cb10672706c38ce9f9efc4cacf77312eafeda6ff68a467b437c9e27b'
    if report.get('source_sha256') != expected:
        raise ValueError('Unexpected source artifact')
    cues = json.loads((source / 'cues_raw.json').read_text())
    if len(cues) != 232:
        raise ValueError('Unexpected transcript')
    args.output.mkdir(parents=True, exist_ok=True)
    # A fresh cache makes this a measured inference, not a cached answer.
    production.BASE = args.output
    results = []
    for number, (a, b) in enumerate(production._chunk_by_time(cues), 1):
        left, right = a, b
        while left > 0 and cues[a]['start'] - cues[left-1]['start'] <= 60:
            left -= 1
        while right+1 < len(cues) and cues[right+1]['end'] - cues[b]['end'] <= 60:
            right += 1
        block = cues[left:right+1]
        started = time.monotonic()
        picks = production.pick_highlights(block, '林园', '', args.output,
                                           suffix=f'_replay_{number}', allow_empty=True)
        row = dict(block=number, seconds=round(time.monotonic()-started, 2),
                   cues=len(block), picks=picks, reviews=[])
        for index, pick in enumerate(picks):
            try:
                proof = production.review_complete_argument(block, [pick], '林园', '', args.output,
                                                            f'_replay_{number}_{index}')
                row['reviews'].append(dict(accepted=True, proof=proof))
            except production.EditorialReviewUnavailable:
                raise
            except production.VisualQualityError as exc:
                row['reviews'].append(dict(accepted=False, reason=str(exc)))
        results.append(row)
        (args.output / 'report.json').write_text(json.dumps(dict(source_sha256=expected,
            original_run=34224561237, blocks=results), ensure_ascii=False, indent=2))
        print(json.dumps(row, ensure_ascii=False), flush=True)
    print('REPLAY_COMPLETE: actual local inference completed; this is not a video publication receipt')


if __name__ == '__main__':
    main()
