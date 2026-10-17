"""Actual CPU review of frozen fallback copy; never uploads or publishes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import produce_cn as producer
import title_rewrite as titles


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',type=int,required=True,choices=[3,6,11])
    args=parser.parse_args()
    corpus=ROOT/'linyuan/simulations/benchmark-20260921/all-current-title-corpus.json'
    case=json.loads(corpus.read_text())[args.case]
    source=''.join(c['text'] for c in case['cues'])
    out=ROOT/'output/click-packaging';out.mkdir(parents=True,exist_ok=True)
    target=out/f'cpu-{args.case}.json'
    report=dict(case=case['id'],model=producer.LOCAL_LLM_MODEL,
        source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        baseline=titles._extractive(source,'林园',()),calls=[],
        scope='Live CPU readability review, not a CTR experiment or newly produced video')
    def save():target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    def review(prompt,schema):
        start=time.monotonic();call=dict(prompt=prompt,schema=schema);report['calls'].append(call);save()
        try:
            response=producer.llm([dict(role='user',content=prompt)],'',response_schema=schema,read_cache=False,
                max_tokens=2300,budget_sec=600)
            call['response']=response
            return response
        finally:call['elapsed_sec']=round(time.monotonic()-start,2);save()
    try:
        result=titles._extractive(source,'林园',(),reviewer=review)
        assert titles.error(result['title'],result['title_rewrite'],source) is None
        report.update(status='accepted',result=result)
    except ValueError as error:report.update(status='rejected',error=str(error))
    save()
    assert len(report['calls'])==1,'Must exercise one real, bounded model request'
    if args.case==3:
        if report['status']=='accepted':
            assert report['result']['title']!=report['baseline']['title'],'Known incoherent baseline must not be approved'
    else:
        assert report['status']=='accepted','Clean source copy must remain available'
    print(json.dumps({k:v for k,v in report.items() if k not in ('calls','baseline','result')},ensure_ascii=False))


if __name__=='__main__':main()
