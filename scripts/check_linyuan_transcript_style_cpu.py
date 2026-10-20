"""Real CPU style/ASR-audit controls. No source edits, FC calls or publishing."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import transcript_audit as audit


def main():
    model=os.environ.get('LOCAL_LLM_MODEL','')
    if model not in {'qwen3:8b','qwen3.5:9b'}:
        raise ValueError('Only current/preview CPU models are allowed for controls')
    out=Path('transcript-style-cpu-results');out.mkdir(exist_ok=True)
    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=15) as r:
        tags=json.load(r)
    actual=next(m for m in tags['models'] if m.get('name')==model)
    cases=[
        ('spoken_style', '我们谈的是科技行业投资。'
         '我投百分之十去拥抱它，或者百分之五去拥抱它。'
         '我自知我的能力，那个东西我就搞一点，整天睡不着就要影响生活了。'
         '我我还没有这种感觉。'
         '你要一下跌，跌个百分之七八十、九十，可能跌了百分之九十，'
         '按照最终的结果，这个还是个底部，那不见得是错误。',True),
        ('corrupt_term', '我们分析企业的现金流。道琼市指数影响投资。',False),
    ]
    rows=[]
    for name,text,expected in cases:
        calls=[]
        def call(prompt,schema):
            started=time.monotonic()
            payload=dict(model=model,messages=[dict(role='user',content=prompt)],
                format=schema,stream=False,think=False,
                options=dict(temperature=0,num_predict=1200,num_ctx=16384))
            req=urllib.request.Request('http://127.0.0.1:11434/api/chat',
                data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=600) as response:
                result=json.load(response)
            content=result['message']['content']
            calls.append(dict(prompt=prompt,response=content,
                seconds=round(time.monotonic()-started,3),model=result.get('model')))
            return content
        row=dict(case=name,transcript=text,expected_clear=expected,
            transcript_sha256=hashlib.sha256(text.encode()).hexdigest(),calls=calls)
        try:
            proof=audit.review(text,'林园',model,call,out/(name+'-proof.json'))
            row.update(proof=proof,control_passed=proof['passed'] is expected)
        except Exception as exc:
            row.update(control_passed=False,error=str(exc))
        finally:
            rows.append(row)
            report=dict(diagnostic_only=True,publication_approved=False,audio_verified=False,
                source_cues_changed=False,production_current_model='qwen3:8b',
                tested_model=model,production_model_changed=False,model_digest=actual['digest'],
                audit_version=audit.VERSION,
                audit_code_sha256=hashlib.sha256(Path(audit.__file__).read_bytes()).hexdigest(),
                commit=os.environ.get('GITHUB_SHA'),run_id=os.environ.get('GITHUB_RUN_ID'),
                cases=rows,passed=len(rows)==len(cases) and all(r['control_passed'] for r in rows))
            (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(dict(case=name,control_passed=row['control_passed']),ensure_ascii=False),flush=True)
    if not report['passed']:
        raise SystemExit('CPU style/real-error controls did not both pass; do not promote')


if __name__=='__main__':main()
