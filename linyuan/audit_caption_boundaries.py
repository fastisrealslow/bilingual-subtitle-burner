"""Read-only A/B audit on replayable real subtitle edit proofs; no publishing."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import types

import caption_lines as candidate
import caption_readability as readability
import presentation
import produce_cn


from caption_boundary_quality import compact, metrics


def validate(rows,groups,layout):
    assert ''.join(r['zh'] for r in rows)==''.join(e['zh'] for e in groups)
    assert [a for r in rows for a in r['caption_chars']]==[a for e in groups for a in candidate.character_anchors(e)]
    assert all(len(r['lines'])==1 and len(r['zh'])<=layout['line_capacity']
               and r['font_px']==layout['subtitle_font_px']
               and .25<=r['end_sec']-r['start_sec']<=6 for r in rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    baseline=types.ModuleType('baseline_caption_lines')
    code=subprocess.check_output(['git','show',args.baseline+':linyuan/caption_lines.py'],text=True)
    exec(compile(code,'baseline_caption_lines.py','exec'),baseline.__dict__)
    paths=list(Path('/tmp').glob('*/subtitle_edit_proof*.json'))
    paths+=list(Path('/tmp').glob('*/deliver/subtitle_edit_proof*.json'))
    paths+=list(Path('/Users/liwenbo/Library/Application Support/LinyuanAudit/runs').glob('*/evidence/*/*proof*.json'))
    seen=set();cases=[];errors=[]
    for path in sorted(paths):
        try:proof=json.loads(path.read_text())
        except (OSError,ValueError):continue
        if not proof.get('raw_entries') or not proof.get('raw_sha256'):continue
        if proof['raw_sha256'] in seen:continue
        seen.add(proof['raw_sha256'])
        try:
            readability.replay_edit_proof(proof)
            entries,_=readability.clean_entries(proof['raw_entries'],policy_version=proof['version'])
            layout=presentation.live_card_layout()
            for meta_path in [path.parent/'matched-meta.json',path.parent/'meta.json',*path.parent.glob('*.meta.json')]:
                if meta_path.exists():
                    meta=json.loads(meta_path.read_text())
                    if not isinstance(meta,dict):continue
                    if meta.get('layout_proof',{}).get('subtitle_max_lines')==1:
                        layout=meta['layout_proof'];break
            try:
                texts=produce_cn.source_caption_groups(entries,layout)
                groups=produce_cn.apply_semantic_groups(entries,texts,layout['line_capacity'],layout['subtitle_font_px'])
                input_kind='source_semantic_groups'
            except ValueError:
                groups=entries;input_kind='same_raw_entries_both_versions'
            row=dict(proof=str(path),raw_sha256=proof['raw_sha256'],input_kind=input_kind)
            for label,module in [('before',baseline),('after',candidate)]:
                try:
                    screens=module.one_line_screens(groups,layout);validate(screens,groups,layout)
                    row[label]=metrics(screens,entries,layout)
                    assert row[label]==metrics(screens,groups,layout),'Source punctuation boundary hints changed'
                    row[label+'_screens']=[dict(text=s['zh'],start=s['start_sec'],end=s['end_sec']) for s in screens]
                except (ValueError,AssertionError) as exc:row[label+'_error']=str(exc) or type(exc).__name__
            cases.append(row)
            print(json.dumps({k:v for k,v in row.items() if not k.endswith('_screens')},ensure_ascii=False),flush=True)
        except (ValueError,KeyError,TypeError) as exc:errors.append(dict(proof=str(path),error=str(exc)))
    totals={label:dict(sum((Counter(row[label]) for row in cases if label in row),Counter())) for label in ('before','after')}
    report=dict(baseline=args.baseline,unique_proofs=len(seen),cases=len(cases),excluded_invalid_proofs=errors,
        before_failures=sum('before_error' in c for c in cases),after_failures=sum('after_error' in c for c in cases),
        totals=totals,regressions=[dict(proof=c['proof'],metrics={k:[c['before'][k],c['after'][k]]
            for k in c['before'] if c['after'][k]>c['before'][k]}) for c in cases
            if 'before' in c and 'after' in c and any(c['after'][k]>c['before'][k] for k in c['before'])],results=cases)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in {'results','regressions'}},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
