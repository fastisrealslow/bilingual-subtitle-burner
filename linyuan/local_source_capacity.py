#!/usr/bin/env python3
"""Estimate reusable 2-4 minute arguments from cached offline ASR, without APIs."""
import argparse
import glob
import json
import re
import sys
from pathlib import Path

BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
import editorial_policy as editorial

TOPICS=('医药','消费','老龄化','茅台','垄断','竞争','投资','行业','企业','股息','风险','市场','长寿')
BAD_OPEN=('然后','但是','所以说','对对','嗯','啊','这个','那个','就是说')
BAD_END=('就是','但是','所以','因为','然后','这个','那个','的话','我们','可能')


def clean(text):
    return re.sub(r'[，。！？；：、,.!?;\s]+', '', text)


def candidates(cues):
    out = []
    used_until = -1
    for i, start in enumerate(cues):
        if start['start'] < used_until or clean(start['text']).startswith(BAD_OPEN):
            continue
        for j in range(i + 1, len(cues)):
            duration = cues[j]['end'] - start['start']
            if duration < 120:
                continue
            if duration > 240:
                break
            ending = clean(cues[j]['text'])
            if ending.endswith(BAD_END):
                continue
            text = ''.join(x['text'] for x in cues[i:j + 1])
            if editorial.transcript_integrity_error(text):
                continue
            score = sum(text.count(x) for x in TOPICS) + min(8, len(text) // 120)
            if score < 8:
                continue
            out.append({
                'start': round(start['start'], 2),
                'end': round(cues[j]['end'], 2),
                'duration_sec': round(duration, 1),
                'score': score,
                'opening': start['text'][:60],
                'ending': cues[j]['text'][-60:],
            })
            used_until = cues[j]['end']
            break
    return out


def main(root):
    sources = []
    for name in glob.glob(str(root/'**/cues_raw.json'),recursive=True):
        path = Path(name)
        cues = json.loads(path.read_text())
        report = path.parent / 'source_quality.json'
        source_sha = None
        if report.exists():
            source_sha = json.loads(report.read_text()).get('source_sha256')
        rows = candidates(cues)
        sources.append({
            'cache': str(path),
            'source_sha256': source_sha,
            'duration_sec': round(cues[-1]['end'], 1) if cues else 0,
            'candidate_count': len(rows),
            'candidates': rows,
        })
    return {
        'mode': 'offline-capacity-estimate',
        'cloud_calls': 0,
        'warning': '候选只通过便宜的句界和已知错词筛选，仍须本地语义模型及实际MP4画面验收',
        'source_count': len(sources),
        'candidate_count': sum(x['candidate_count'] for x in sources),
        'sources': sources,
    }


if __name__=='__main__':
    p = argparse.ArgumentParser()
    p.add_argument('root', type=Path)
    p.add_argument('--out', type=Path)
    a = p.parse_args()
    result = main(a.root)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if a.out:
        a.out.write_text(text, encoding='utf-8')
    print(text)
