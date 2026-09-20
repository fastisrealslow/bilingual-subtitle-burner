"""Summarize every scheduled title trial, retaining missing and failed rows."""
import collections
import json
from pathlib import Path
import sys


def summarize(directory, output, expected_trials=18):
    if expected_trials < 1:
        raise ValueError('expected_trials must be positive')
    paths=list(Path(directory).rglob('case-*-repeat-*.json'))
    rows=[];seen=set()
    for path in sorted(paths):
        row=json.loads(path.read_text());key=(row['case'],row['repeat'])
        if key in seen:raise ValueError(f'duplicate result: {key}')
        seen.add(key);rows.append(row)
    groups=collections.defaultdict(list)
    for r in rows:groups[r['case']].append(r)
    finished=[r for r in rows if r.get('status') in ('generated','unresolved')]
    generated=[r for r in rows if r.get('status')=='generated']
    if len(rows)>expected_trials:
        raise ValueError('more results than scheduled trials')
    summary=dict(expected_trials=expected_trials,received_trials=len(rows),finished_trials=len(finished),
        missing_or_interrupted=expected_trials-len(finished),generated=len(generated),
        model_reviewed=sum(r.get('method')=='cpu_text_review' for r in generated),
        quote_fallback=sum(r.get('method')=='source_quote' for r in generated),
        unresolved=sum(r.get('status')=='unresolved' for r in rows),
        screening_flags=sum(bool(r.get('screening_signals')) for r in generated),
        code_hashes=sorted({r['title_code_sha256'] for r in rows}),
        disclaimer='模型通过、原话兜底和人工吸引力评价分开；不能推断点击率。',
        cases=[dict(case=k,titles=[dict(repeat=r['repeat'],status=r.get('status','interrupted'),
            title=r.get('result',{}).get('title'),method=r.get('method'),
            signals=r.get('screening_signals'),error=r.get('error'),
            appeal=r.get('result',{}).get('title_rewrite',{}).get('review',{}).get('appeal'))
            for r in sorted(v,key=lambda r:r['repeat'])]) for k,v in groups.items()])
    if len(summary['code_hashes'])>1: raise ValueError('mixed title code versions')
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    lines=['# 标题重复生成批测','',f"计划{expected_trials}次；完成{len(finished)}次，缺失/中断{expected_trials-len(finished)}次。",
        f"独立模型审核输出{summary['model_reviewed']}；原话兜底{summary['quote_fallback']}；未解决{summary['unresolved']}。",
        '',summary['disclaimer'],'','| 案例 | 重复 | 标题 | 路径 | 筛查提示 |','|---|---:|---|---|---|']
    for r in rows:
        title=r.get('result',{}).get('title') or r.get('error','未完成')
        lines.append('| '+ ' | '.join([r['case'],str(r['repeat']),title.replace('|','/').replace('\n',' '),str(r.get('method',r.get('status','interrupted'))),'、'.join(r.get('screening_signals',[]))])+' |')
    (out/'summary.md').write_text('\n'.join(lines)+'\n')
    return summary

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory');parser.add_argument('output')
    parser.add_argument('--expected-trials',type=int,default=18)
    args=parser.parse_args()
    print(json.dumps(summarize(args.directory,args.output,args.expected_trials),ensure_ascii=False,indent=2))
