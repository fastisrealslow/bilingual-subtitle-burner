"""Report all 30 attempts, including absent and rejected outputs."""
import json
from pathlib import Path
import sys


def summarize(directory):
    rows = {}
    for path in Path(directory).rglob('case-*-repeat-*.json'):
        row = json.loads(path.read_text())
        key = row['case_number'], row['repeat']
        if key in rows or key[0] not in range(1,11) or key[1] not in (1,2,3):
            raise ValueError(f'Duplicate or invalid trial {key}')
        rows[key] = {k:v for k,v in row.items() if k not in ('calls','model_details')}
    for n in range(1,11):
        for repeat in (1,2,3):
            rows.setdefault((n,repeat), dict(case_number=n, repeat=repeat, status='missing', final_candidate=None))
    ordered = [rows[k] for k in sorted(rows)]
    accepted = lambda r: r['status'] in ('accepted_initial','accepted_repaired') and bool(r.get('final_candidate'))
    return dict(expected=30, completed=sum(r['status'] not in ('missing','unresolved','running') for r in ordered),
                machine_accepted=sum(accepted(r) for r in ordered),
                all_three_accepted_cases=sum(all(accepted(rows[n,i]) for i in (1,2,3)) for n in range(1,11)),
                repaired=sum(r['status']=='accepted_repaired' for r in ordered), rows=ordered)


def main():
    data = summarize(sys.argv[1])
    out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    (out/'summary.json').write_text(json.dumps(data, ensure_ascii=False, indent=2))
    lines = ['# Qwen 园园风格 V3：10 条 × 3 次', '',
             '模型不变；三个不同 seed，候选展示顺序打乱。直接比较选稿，独立核对，最多修正一次后再次核对。',
             '机器通过率不是人工合格率，也不是流量指标。所有失败保留；未接入生产。', '',
             '|素材|第1次|第2次|第3次|','|---|---|---|---|']
    for n in range(1,11):
        titles=[]
        for r in data['rows'][(n-1)*3:n*3]:
            titles.append((r.get('final_candidate') or {}).get('title', '无通过稿')+' / '+r['status'])
        lines.append('|'+str(n)+'|'+'|'.join(titles)+'|')
    for r in data['rows']:
        print('QWEN_V3_CASE '+json.dumps(r,ensure_ascii=False))
    (out/'comparison.md').write_text('\n'.join(lines)+'\n')
    print('SUMMARY '+json.dumps({k:v for k,v in data.items() if k!='rows'}))


if __name__=='__main__':
    main()
