"""Real CPU source classification and treatment title review. Never publishes."""
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import produce_cn as P
import publishing_experiment as E
from source_selection import select


def main():
    assert E.profile()==E.TREATMENT
    fixture=json.loads((ROOT/'tests/fixtures/linyuan_888_source_selection.json').read_text())
    cues=fixture['cues']
    # Frozen automatic candidates, not manually authored production ranges.
    candidates=select(cues,whole_source=True,limit=None)[:3]
    assert len(candidates)==3 and cues[-1]['end']>=E.MIN_MOTHER_SECONDS
    out=ROOT/'output/field-story-review';out.mkdir(parents=True,exist_ok=True)
    report=dict(fixture='linyuan_888_source_selection.json',model=P.LOCAL_LLM_MODEL,
        source_sha256=fixture['source_sha256'],candidate_ranges=candidates,calls=[],
        scope='Actual CPU selection/title verification, not an approved media render or CTR proof')
    def save():
        (out/'review.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    def call(prompt,schema):
        row=dict(prompt=prompt,schema=schema);report['calls'].append(row);save();start=time.monotonic()
        try:
            result=P.llm([dict(role='user',content=prompt)],'',temperature=0,max_tokens=1600,
                         budget_sec=900,response_schema=schema,read_cache=False)
            row['response']=result
            return result
        finally:row['elapsed_sec']=round(time.monotonic()-start,2);save()
    selection=E.choose_stories(cues,candidates,call,out/'selection.json')
    stories=selection['selected']
    report['selection']=selection
    report['selected']=stories;save()
    assert stories,'Real personal/company examples must not all be discarded'
    negative=[dict(start=0,end=40,text='投资要有认知。长期持有很重要。应该看好公司的基本面。'),
        dict(start=40,end=90,text='一般的ETF是跟踪指数。过段时间企业不行了，它就给你剔除。成份股会随时调整，不能把ETF当静态的。')]
    rejected=E.choose_stories(negative,[dict(start=i,end=i) for i in range(len(negative))],call,out/'negative-selection.json')['selected']
    assert not rejected,'Generic principles alone are not a concrete personal/company story'
    selected=next((p for p in stories if p['story_selection']['kind']=='company_case'),stories[0])
    source=cues[selected['start']:selected['end']+1]
    (out/'title').mkdir(exist_ok=True)
    result=P.copywrite(source,list(range(len(source))),'林园','访谈','',out/'title',
                       story_evidence=selected['story_selection'],
                       source_sha256=fixture['source_sha256'])
    report['copy']=result;save()
    assert result['title_rewrite']['review']['method']=='cpu_text_review'
    assert result['copy_identity']['editorial_profile']==E.TREATMENT
    # This fixed real source explains the investment with 消费升级. It does
    # not establish a new claim that personal incomes suddenly exploded.
    # These are evaluation expectations, never production-provided titles.
    if selected['story_selection']['kind']=='company_case':
        body=result['title'].removeprefix('林园：')
        assert '茅台' in body[:12],'Put the actual company before abstract self-evaluation'
        assert '消费升级' in body,'Retain the source reason, not an invented income-boom shorthand'
        assert not any(t in body for t in ('收入爆发','收入将爆发')),'Do not turn the background into an income-boom claim'
        assert len(body)<=28,'State the concrete choice and reason naturally, not a compressed research summary'
    report['passed']=True;save()
    print(json.dumps(dict(passed=True,story_count=len(stories),title=result['title'],
                         cover=result['cover_title']),ensure_ascii=False))


if __name__=='__main__':main()
