"""Real CPU style/ASR-audit controls. No source edits, FC calls or publishing."""
import hashlib
import json
import os
import io
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import transcript_audit as audit


def actual_range_cases():
    """Replay exact failed-production text, never supply production answers."""
    artifact=12467582807
    repo=os.environ['GITHUB_REPOSITORY']
    raw=subprocess.check_output(['gh','api',f'repos/{repo}/actions/artifacts/{artifact}/zip'],timeout=120)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        source=json.loads(archive.read('_tmp/source_quality.json'))
        if source.get('source_sha256')!='8b5c9d11737003b7a409fabf803ff7b185ed50bd4ed171a9e0377ac95e67e313':
            raise ValueError('Actual source identity changed')
        cues=json.loads(archive.read('_tmp/cues_raw.json'))
        selection=json.loads(archive.read('_tmp/story_selection.json'))
        expected={
            '4f768c17e28129716d0441144f29db47028f0ab8b25c1098cb86aa38260ddcb7':
                ('actual_spoken_price_range',True),
        }
        rows=[]
        for pick in selection['selected']:
            text=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
            digest=hashlib.sha256(text.encode()).hexdigest()
            if digest in expected:
                name,clear=expected[digest];rows.append((name,text,clear))
            elif 'TCATCA' in text:
                rows.append(('actual_corrupt_company_name',text,False))
        if {r[0] for r in rows}!={'actual_spoken_price_range','actual_corrupt_company_name'}:
            raise ValueError('Need both untouched actual production cases')
    return rows,dict(run_id=39864415088,artifact_id=artifact,
        source_sha256=source['source_sha256'],artifact_zip_sha256=hashlib.sha256(raw).hexdigest(),
        usage='text_plausibility_diagnostic_only_not_stock_or_audio_approval')


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
        ('spoken_ranges','当时大概五六块钱，过了两三年，我花了七八百元。',True),
        ('corrupt_numeric_unit','我们说的是股价，股价是百分之元，收益率是点点。',False),
    ]
    actual_cases,actual_provenance=actual_range_cases()
    cases+=actual_cases
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
                source_cues_changed=False,production_current_model='qwen3.5:9b',
                tested_model=model,production_model_changed=False,model_digest=actual['digest'],
                audit_version=audit.VERSION,
                spoken_range_policy_version=audit.SPOKEN_RANGE_POLICY_VERSION,
                actual_provenance=actual_provenance,
                audit_code_sha256=hashlib.sha256(Path(audit.__file__).read_bytes()).hexdigest(),
                commit=os.environ.get('GITHUB_SHA'),run_id=os.environ.get('GITHUB_RUN_ID'),
                cases=rows,passed=len(rows)==len(cases) and all(r['control_passed'] for r in rows))
            (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(dict(case=name,control_passed=row['control_passed']),ensure_ascii=False),flush=True)
    if not report['passed']:
        raise SystemExit('CPU style/real-error controls did not both pass; do not promote')


if __name__=='__main__':main()
