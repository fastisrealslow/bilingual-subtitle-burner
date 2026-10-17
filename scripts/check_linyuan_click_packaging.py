"""Frozen-source packaging comparison. Offline quality, never a CTR result."""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import headline_policy as H
import presentation as P
import title_rewrite as T


@contextmanager
def baseline_modules(ref):
    original={name:sys.modules[name] for name in ('headline_policy','presentation','title_rewrite')}
    modules={}
    try:
        for name in original:
            source=subprocess.check_output(['git','show',f'{ref}:linyuan/{name}.py'],cwd=ROOT,text=True)
            module=types.ModuleType(name)
            module.__file__=str(ROOT/'linyuan'/f'{name}.py')
            sys.modules[name]=module
            exec(compile(source,module.__file__,'exec'),module.__dict__)
            modules[name]=module
        yield modules
    finally:
        sys.modules.update(original)


def quote_result(module,source):
    try:
        result=module._extractive(source,'林园',())
        return dict(title=result['title'],cover=result['cover_title'],
                    source_bound=module.error(result['title'],result['title_rewrite'],source) is None)
    except ValueError as error:
        return dict(error=str(error))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref',default='HEAD')
    parser.add_argument('--out',type=Path,default=ROOT/'output/click-packaging')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    corpus=ROOT/'linyuan/simulations/benchmark-20260921/all-current-title-corpus.json'
    cases=json.loads(corpus.read_text())
    before={};old_titles={}
    with baseline_modules(args.baseline_ref) as old:
        for case in cases:
            source=''.join(c['text'] for c in case['cues'])
            old_titles[case['id']]=quote_result(old['title_rewrite'],source)
            for capacity,minimum in ((5,2),(6,3),(9,3)):
                key=(case['id'],capacity)
                try:
                    before[key]=old['presentation'].cover_headline(case['old_cover'],max_lines=3,
                        line_capacity=capacity,min_line_chars=minimum)
                except ValueError:before[key]=None
    rows=[];regressions=[]
    for case in cases:
        source=''.join(c['text'] for c in case['cues'])
        current=quote_result(T,source)
        row=dict(id=case['id'],source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                 baseline_quote=old_titles[case['id']],current_quote=current,covers=[])
        for capacity,minimum in ((5,2),(6,3),(9,3)):
            previous=before[(case['id'],capacity)]
            try:
                lines=P.cover_headline(case['old_cover'],max_lines=3,line_capacity=capacity,min_line_chars=minimum)
            except ValueError:lines=None
            entry=dict(capacity=capacity,before=previous,after=lines,
                       before_penalty=P.cover_break_penalty(previous) if previous else None,
                       after_penalty=P.cover_break_penalty(lines) if lines else None)
            if previous and (not lines or entry['after_penalty']>entry['before_penalty']):
                regressions.append(dict(id=case['id'],**entry))
            if lines:
                old_text=''.join(previous) if previous else ''.join(lines)
                if ''.join(lines)!=old_text or len(lines)>3 or max(map(len,lines))>capacity:
                    raise ValueError('封面比较改变原文或容量')
            row['covers'].append(entry)
        rows.append(row)
    summary=dict(real_source_cases=len(rows),cover_comparisons=sum(len(r['covers']) for r in rows),
        improved=sum(c['before_penalty'] is not None and c['after_penalty'] is not None
                     and c['after_penalty']<c['before_penalty'] for r in rows for c in r['covers']),
        unchanged=sum(c['before']==c['after'] for r in rows for c in r['covers']),
        regressions=len(regressions),
        baseline_quote_candidates=sum('title' in r['baseline_quote'] for r in rows),
        current_quote_candidates=sum('title' in r['current_quote'] for r in rows))
    report=dict(summary=summary,baseline_ref=args.baseline_ref,
        corpus_sha256=hashlib.sha256(corpus.read_bytes()).hexdigest(),rows=rows,regressions=regressions,
        scope='Frozen real-source structural quote candidates and cover wrapping only. '
              'No live model generation, semantic acceptance, production yield or click-through uplift is claimed.')
    (args.out/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False))
    if regressions:raise SystemExit('Cover phrase quality/availability regressed')


if __name__=='__main__':main()
