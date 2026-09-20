"""Retain every trial, including rejected and missing rows, in one report."""
import json
from pathlib import Path
import sys

rows = {}
for path in Path(sys.argv[1]).rglob('case-*.json'):
    r = json.loads(path.read_text())
    number = r['case_number']
    if number in rows:
        raise ValueError(f'Duplicate case {number}')
    rows[number] = r
out = Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
for number in range(1, 11):
    rows.setdefault(number, dict(case_number=number, status='missing', old_title='', drafts=[], selected_index=None))
ordered = [rows[i] for i in range(1,11)]
summary = dict(expected=10, accepted=sum(r['status']=='accepted' for r in ordered),
               rows=[{k:v for k,v in r.items() if k not in ('calls','model_details')} for r in ordered])
(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
lines = ['# Qwen3 8B：园园风格历史标题实测', '',
         '真实CPU推理；旧标题仅用于输出比较，不输入模型。拟稿温度0.35，阅读/复核温度0，固定seed。',
         '模型复核通过不等于用户认可或点击率提高。拒绝和失败保留，不以人工改写补齐。', '',
         '| # | 原标题 | Qwen风格首选（不代表发布合格） | 事实及格式检查状态 |', '|---:|---|---|---|']
for r in ordered:
    i = r.get('style_selected_index', r.get('selected_index'))
    title = r['drafts'][i]['title'] if i is not None else '未通过，无自动选中标题'
    lines.append(f"| {r['case_number']} | {r.get('old_title','')} | {title} | {r['status']} |")
    print('QWEN_CASE ' + json.dumps({k:v for k,v in r.items() if k not in ('calls','model_details')},ensure_ascii=False))
lines += ['', '## 全部候选及复核', '']
for r in ordered:
    lines += [f"### {r['case_number']}. {r.get('case','missing')}", '', f"状态：{r['status']}", '']
    for i,c in enumerate(r.get('drafts',[])):
        lines += [f"- 候选 {i+1}：{c['title']}；封面：{c['cover']}"]
    lines += ['', '```json', json.dumps(r.get('reviews',[]),ensure_ascii=False,indent=2), '```', '']
    if r.get('error'):lines += [r['error'], '']
(out/'comparison.md').write_text('\n'.join(lines))
print('SUMMARY ' + json.dumps(dict(expected=10,accepted=summary['accepted']),ensure_ascii=False))
