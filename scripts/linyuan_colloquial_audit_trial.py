"""Actual CPU text-only audit comparison; never approval or publication."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import transcript_audit as audit


# Real source excerpts and explicit synthetic negative controls. Expected labels
# are evaluation data, NEVER passed to the reviewer or production selection.
CASES=(
    ('mouth_metaphor','所以我们刚才首先我刚才讲的都是赛道，赛道，嘴巴刚需。',True,'actual_source_ly-1013-8a7cb7; terminology reference https://www.stcn.com/article/detail/818811.html; not audio verification'),
    ('colloquial_operation','就那个时候，我们是可以知道市场，那也是胡来的，是吧？',True,'actual_source_ly-1009-2f94e6; stylistic lead, not audio verification'),
    ('repeated_subject','我我还没有这种感觉。',True,'synthetic positive control'),
    ('alternative_ratios','百分之十，或者百分之五，都是可以考虑的比例。',True,'synthetic positive control'),
    ('ordinary_metaphor','我们拥抱这个行业，先搞一点。',True,'synthetic positive control'),
    ('wrong_speaker_name','我们因为我们投资，我们凌源投资是在全球范围内。',False,'actual_source_ly-1013-8a7cb7; known suspect name'),
    ('wrong_named_index','我看的是道琼市指数。',False,'synthetic negative control'),
    ('missing_percentage','这个收益率是百分之。',False,'synthetic negative control'),
    ('broken_negation','我没有不没买这只股票。',False,'synthetic negative control'),
)


def run(call,model,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    results=[]
    for ident,text,expected,basis in CASES:
        for profile in ('production','colloquial-v1'):
            started=time.monotonic();row=dict(case=ident,profile=profile,
                transcript_sha256=audit.text_digest(text),expected_pass=expected,basis=basis)
            try:
                proof=audit.review(text,'林园',model,call,out/f'{ident}-{profile}.json',prompt_profile=profile)
                row.update(observed_pass=proof['passed'],matched=proof['passed']==expected,
                    issues=proof['issues'],audio_verified=proof['audio_verified'])
            except Exception as exc:
                row.update(observed_pass=None,matched=False,error=f'{type(exc).__name__}: {exc}')
            row['elapsed_seconds']=round(time.monotonic()-started,2)
            results.append(row)
            report=dict(model=model,publication_allowed=False,production_default_changed=False,
                model_evaluation_not_mother_yield=True,results=results)
            (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps(row,ensure_ascii=False),flush=True)
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',default='colloquial-audit-results')
    args=parser.parse_args()
    import produce_cn as producer
    if producer.TEXT_BACKEND!='local' or os.environ.get('CUDA_VISIBLE_DEVICES',''):
        raise SystemExit('Trial requires CPU local backend, never cloud fallback')
    def call(prompt,schema):
        return producer.llm([{'role':'user','content':prompt}],'',temperature=0,
            max_tokens=800,budget_sec=180,response_schema=schema,read_cache=False)
    run(call,producer.LOCAL_LLM_MODEL,args.out)


if __name__=='__main__':main()
