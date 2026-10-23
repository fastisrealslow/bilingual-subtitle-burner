"""Actual unchanged source text and malformed controls; no media approval."""
import hashlib,json,os,subprocess,tempfile,zipfile
from pathlib import Path


def main():
    import produce_cn as P
    from transcript_audit import review
    assert P.TEXT_BACKEND=='local' and P.LOCAL_LLM_MODEL=='qwen3.5:9b'
    root=Path('verified-company-audit-check');root.mkdir(exist_ok=True)
    result=dict(passed=False,diagnostic_only=True,audio_verified=False,
        registered_stock=False,published=False,source_artifact_id=12458840653,
        policy_sha256=hashlib.sha256(Path('linyuan/transcript_audit.py').read_bytes()).hexdigest(),cases=[])
    def save(): (root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    with tempfile.TemporaryDirectory() as tmp:
        archive=Path(tmp)/'actual.zip'
        with archive.open('wb') as f:
            subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/12458840653/zip'],stdout=f,check=True,timeout=120)
        with zipfile.ZipFile(archive) as z:
            cues=json.loads(z.read('_tmp/cues_raw.json'));source=json.loads(z.read('_tmp/source_quality.json'))
            selected=json.loads(z.read('_tmp/story_selection.json'))['selected']
            reports=json.loads(z.read('_tmp/asr_raw_chunks.json'))
    assert source['source_sha256']=='8f379d9cecd36dc6b22d8022beed3a3a698aa217e8e0e48fdf4e2a575294b210'
    assert all(r['source_pcm_sha256']=='c70e8fc606911792fcf2336d4aa52425bd0f8b68140132cf127afb5426b76b84' for r in reports)
    old_company=next(p for p in selected if p['story_selection']['kind']=='company_case')
    old_personal=next(p for p in selected if p['story_selection']['kind']=='personal_story')
    company=''.join(c['text'] for c in cues[old_company['start']:old_company['end']+1])
    personal=''.join(c['text'] for c in cues[old_personal['start']:old_personal['end']+1])
    chunks=[c for r in reports for c in r['chunks'] if c['core_start']==1320]
    assert len(chunks)==1 and '郑州百文' in chunks[0]['text'] and 'TCTC' in company
    cases=[('actual-company-with-known-name-and-garbled-acronym',company,False),
           ('actual-intelligible-company-name-window',chunks[0]['text'],True),
           ('actual-unrelated-personal-asr-defect',personal,False)]
    def call(prompt,schema):
        return P.llm([dict(role='user',content=prompt)],'',temperature=0,max_tokens=1200,
            budget_sec=P.text_budget(240),response_schema=schema,read_cache=False)
    for name,text,expected in cases:
        (root/(name+'.input.txt')).write_text(text)
        report=review(text,'林园',P.LOCAL_LLM_MODEL,call,root/(name+'.json'))
        ok=(report['passed'] is expected)
        if not expected and name.startswith('actual-company'):
            ok=ok and any('TC' in i['suspect_quote'].replace(' ','') for i in report['issues'])
        result['cases'].append(dict(name=name,expected_clear=expected,actual_clear=report['passed'],
            passed=ok,input_sha256=hashlib.sha256(text.encode()).hexdigest(),issues=report['issues']))
        save()
    result['passed']=all(c['passed'] for c in result['cases']);save()
    print(json.dumps(result,ensure_ascii=False),flush=True)
    assert result['passed'],'Actual source/control audit failed; do not promote'


if __name__=='__main__':main()
