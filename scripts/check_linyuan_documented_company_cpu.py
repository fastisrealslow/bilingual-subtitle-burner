"""Live, uncached review added to an immutable full-source production replay."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import produce_cn as P
import publishing_experiment as E
import documented_company_story as D


def main():
    f=json.loads(Path('tests/fixtures/linyuan_documented_company_source_20261030.json').read_text())
    report=dict(model=P.LOCAL_LLM_MODEL,source_run_id=f['source_run_id'],source_sha256=f['source_sha256'],
        fixture_sha256=E.digest(f),baseline_selected=0,passed=False,text_only=True,
        accepted_mp4_count=0,production_dispatch=False,primary_replayed_calls=0,calls=[],controls=[])
    path=Path('documented-company-cpu.json')
    def save():path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    primary=iter(zip(f['original_responses'],f['primary_request_bindings']))
    def live(prompt,schema):
        row=dict(prompt=prompt,schema=schema);report['calls'].append(row);save()
        row['response']=P.llm([dict(role='user',content=prompt)],'',temperature=0,max_tokens=1600,
            budget_sec=240,response_schema=schema,read_cache=False)
        save();return row['response']
    def call(prompt,schema):
        if 'a_evidence' in schema['properties']:return live(prompt,schema)
        raw,binding=next(primary)
        assert E.digest(prompt)==binding['prompt_sha256']
        assert E.digest(schema)==binding['schema_sha256']
        report['primary_replayed_calls']+=1
        return raw
    began=time.monotonic();save()
    try:
        selected=E.choose_stories(f['source_cues'],f['candidates'],call,None)
        report['selection']=selected;save()
        assert next(primary,None) is None and report['primary_replayed_calls']==4
        assert len(selected['selected'])==1 and not selected['unresolved']
        item=selected['selected'][0]
        assert (item['start'],item['end'])==(f['candidates'][1]['start'],f['candidates'][1]['end'])
        s=item['story_selection'];assert D.selection_error(s) is None
        assert s['event_quote'] in ''.join(c['text'] for c in f['source_cues'][item['start']:item['end']+1])
        # Both controls qualify for a review, but their full context denies
        # the SAME-guest/SAME-company connection. Numbers alone cannot admit.
        controls=[
            ('quoted_other_author','主持人：请读一下别人的文章，这不是您自己的判断。'
             '嘉宾：好的，下面都是那位作者的原话。招商银行是这段的对象。'
             '我当年写过文章讨论招商银行。那本书记录了二零零六年的结果。'
             '嘉宾：读完了，这是别人的经历，我没有发表过这种判断。'),
            ('different_company_result','我当年写过文章讨论招商银行，那篇文章没有记录后续结果。'
             '另一本书记录的是茅台二零零六年的结果。两件事情完全无关。'),
        ]
        for name,text in controls:
            cues=[dict(start=0,end=40,text=text)];pick=dict(start=0,end=0)
            request=D.request(cues,pick);assert request is not None
            verdict=D.normalize(json.loads(live(*request)))
            report['controls'].append(dict(name=name,kind=verdict['kind']))
            assert verdict['kind']=='skip',name+' false positive'
        report['passed']=True
    except Exception as exc:
        report['error']=type(exc).__name__+': '+str(exc)
        raise
    finally:
        report['elapsed_seconds']=round(time.monotonic()-began,2);save()
        print(json.dumps({k:v for k,v in report.items() if k not in {'calls','selection'}},ensure_ascii=False))


if __name__=='__main__':main()
