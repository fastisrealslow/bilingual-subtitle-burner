"""Read-only replay of real title inputs. Never loads credentials or renders/posts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))


def signals(title):
    # Screening flags only; not a substitute for semantic or human acceptance.
    rules = {
        'verbal_repair': r'是应该是|买买|对人人体|你首先买买的',
        'missing_object': r'^林园[：:]?(?:都是|一个是|我是永远|是应该)|人家告诉你$',
        'report_voice': r'配置低估值|坚持投资理念|趋势明确|确定性增长|核心逻辑|深度解析',
        'return_claim_needs_review': r'几万倍|稳赚|保证|一定赚',
    }
    return [name for name, pattern in rules.items() if re.search(pattern, title)]


def cases():
    corpus = json.loads((ROOT/'linyuan/simulations/title-batch-20260920/corpus.json').read_text())
    selected = [corpus[i] for i in (0, 3, 6, 8, 10)]
    old = json.loads((ROOT/'tests/fixtures/linyuan_0913_landscape_title.json').read_text())
    selected.append(dict(id='host-premise-control', cues=old['cues'], old_title=old['old_title']))
    return selected


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--case', type=int, required=True);ap.add_argument('--repeat', type=int, required=True)
    ap.add_argument('--model', choices=['qwen3:8b','qwen3:14b','qwen3.5:9b'], default='qwen3:8b',
                    help='Isolated replay model; production default is unchanged')
    ap.add_argument('--corpus', type=Path, help='Optional frozen, source-hashed production inputs')
    ap.add_argument('--draft-profile', choices=['production', 'concise', 'source_limits', 'spoken_focus', 'source_choices', 'answer_focus'], default='production')
    args=ap.parse_args()
    assert os.environ.get('TEXT_BACKEND') == 'local'
    assert os.environ.get('LOCAL_LLM_MODEL') == args.model
    # Exercise the same drafting path and cache identity as full production.
    # Older lab runs injected the profile after identity was built.
    os.environ['LINYUAN_TITLE_DRAFT_PROFILE']=args.draft_profile
    import produce_cn as p
    import editorial_policy as ep
    import title_rewrite as te
    corpus=json.loads(args.corpus.read_text()) if args.corpus else cases()
    case=corpus[args.case];text=''.join(c['text'] for c in case['cues'])
    if case.get('transcript_sha256'): assert ep.text_digest(text)==case['transcript_sha256']
    out=Path('title-batch-results');out.mkdir(exist_ok=True)
    row=dict(case=case['id'],repeat=args.repeat,old_title=case['old_title'],old_signals=signals(case['old_title']),
             transcript_sha256=ep.text_digest(text),source_artifact=case.get('artifact_id'),
             title_code_sha256=hashlib.sha256(Path(te.__file__).read_bytes()).hexdigest(),
             commit=os.environ.get('GITHUB_SHA'),model=p.LOCAL_LLM_MODEL,temperature=.35,draft_profile=args.draft_profile,
             source_run_id=case.get('source_run_id'),source_sha256=case.get('source_sha256'),
             editorial_approved=False,scope='Text-only replay; not a produced video or source100 pass',
             num_ctx=16384,max_tokens=2300,cache_reads=False,draft_interventions=0,calls=[])
    target=out/f'case-{args.case}-repeat-{args.repeat}.json'
    def save(): target.write_text(json.dumps(row, ensure_ascii=False, indent=2))
    original=p.llm
    def uncached(*a, **kw):
        kw['read_cache']=False
        schema=kw.get('response_schema') or {}
        if args.draft_profile != 'production' and 'c_candidates' in schema.get('properties',{}):
            if args.draft_profile in ('source_choices','answer_focus'):
                fields=schema['properties']['c_candidates']['items']['properties']
                if list(fields)!=['a_focus','title','cover_title'] or 'b_focus' in schema['properties']:
                    raise ValueError('Each independent draft must plan its own source claim first')
            if args.draft_profile in ('source_limits','spoken_focus'):
                focus=schema['properties']['b_focus']
                if 'a_0_source_limits' not in focus['properties']:
                    raise ValueError('Production path did not apply source-limits schema')
                if args.draft_profile=='spoken_focus' and 'a_00_hook_options' not in focus['properties']:
                    raise ValueError('Production path did not apply spoken-focus schema')
                expected=(['a_00_hook_options'] if args.draft_profile=='spoken_focus' else [])+['a_0_source_limits','a_claim','b_evidence_ids']
                if list(focus['properties'])!=expected:
                    raise ValueError('Source planning fields must precede the claim in the actual request')
            row['draft_interventions']+=1
        call=dict(prompt=a[0],schema=kw.get('response_schema'),budget=kw.get('budget_sec'));t=time.monotonic()
        try:
            result=original(*a, **kw);call['response']=result
            if args.draft_profile in ('source_limits','spoken_focus') and 'c_candidates' in schema.get('properties',{}):
                parsed=json.loads(result) if isinstance(result,str) else result
                call['source_plan_before_claim']=list(parsed.get('b_focus',{}))==expected
            elif args.draft_profile in ('source_choices','answer_focus') and 'c_candidates' in schema.get('properties',{}):
                parsed=json.loads(result) if isinstance(result,str) else result
                candidates=parsed.get('c_candidates') or []
                call['source_plan_before_claim']=len(candidates)==3 and all(
                    list(c)==['a_focus','title','cover_title'] and
                    list(c.get('a_focus',{}))==['a_0_source_limits','a_claim','b_evidence_ids'] for c in candidates)
            return result
        except Exception as exc:
            call['error']=str(exc);raise
        finally:
            call['seconds']=round(time.monotonic()-t,2);row['calls'].append(call);save()
    p.llm=uncached
    start=time.monotonic();save()
    try:
        with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=15) as response:
            tags=json.load(response)
        actual=next((m for m in tags.get('models',[]) if m.get('name')==args.model or m.get('model')==args.model),None)
        if not actual or not actual.get('digest'):
            raise ValueError('loaded model digest unavailable')
        row['model_digest']=actual['digest'];save()
        with tempfile.TemporaryDirectory() as work:
            result=p.copywrite(case['cues'],list(range(len(case['cues']))),'林园','访谈','',Path(work))
        row['result']=result
        row['proof_error']=te.error(result['title'],result['title_rewrite'],text)
        row['method']=result['title_rewrite']['review']['method']
        row['screening_signals']=signals(result['title'])
        row['status']='generated'
    except Exception as exc:
        row['status']='unresolved';row['error']=f'{type(exc).__name__}: {exc}'
    row['seconds']=round(time.monotonic()-start,2);save()
    row['experiment_valid'] = args.draft_profile == 'production' or row['draft_interventions'] > 0
    if args.draft_profile in ('source_limits','spoken_focus','source_choices','answer_focus'):
        observed=[c['source_plan_before_claim'] for c in row['calls'] if 'source_plan_before_claim' in c]
        row['planning_order_verified']=bool(observed) and all(observed)
        row['experiment_valid']=row['experiment_valid'] and row['planning_order_verified']
    save()
    print(json.dumps({k:v for k,v in row.items() if k not in ('calls','result')},ensure_ascii=False))
    if row.get('result'):print('TITLE:',row['result']['title'])

if __name__=='__main__': main()
