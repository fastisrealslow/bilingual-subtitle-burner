"""Same nine actual candidates, current real CPU selector, no media approval."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


def load_original(archive):
    import publishing_experiment as experiment
    with zipfile.ZipFile(archive) as z:
        cues=json.loads(z.read('_tmp/cues_raw.json'))
        original=json.loads(z.read('_tmp/story_selection.json'))
        quality=json.loads(z.read('_tmp/source_quality.json'))
    assert quality['source_sha256']=='6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d'
    assert original['transcript_sha256']==experiment.digest(cues)
    candidates=original['candidates']
    assert len(candidates)==9 and original['selected']==[]
    assert {(p['start'],p['end']) for p in candidates}>={(76,87),(333,360),(106,123)}
    # Ensure the same complete evidence, not a shortened or handpicked input.
    for call in original['calls']:
        prompt,schema=experiment.selection_request(cues,[candidates[i] for i in call['indices']])
        units=json.loads(prompt.split('\n',1)[1])
        previous=json.loads(call['prompt'].split('\n',1)[1])
        assert units==previous and schema==call['schema']
    return cues,candidates,quality


def main():
    import produce_cn as production
    import publishing_experiment as experiment
    assert production.TEXT_BACKEND=='local' and production.LOCAL_LLM_MODEL=='qwen3.5:9b'
    target=Path('company-qa-selection-verification');target.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='company-qa-original-') as tmp:
        archive=Path(tmp)/'actual.zip'
        with archive.open('wb') as handle:
            subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/12428005891/zip'],
                stdout=handle,check=True,timeout=180)
        cues,candidates,quality=load_original(archive)
    def call(prompt,schema):
        return production.llm([dict(role='user',content=prompt)],'',temperature=0,
            max_tokens=1600,budget_sec=production.text_budget(240),response_schema=schema,read_cache=False)
    report=experiment.choose_stories(cues,candidates,call,target/'story_selection.json')
    selected={(p['start'],p['end']):p for p in report['selected']}
    expected=(76,87)
    result=dict(source_sha256=quality['source_sha256'],transcript_sha256=experiment.digest(cues),
        selection_policy_sha256=hashlib.sha256(Path(experiment.__file__).read_bytes()).hexdigest(),
        candidates_tested=len(candidates),actual_company_qa_selected=expected in selected,
        generic_theory_rejected=(333,360) not in selected,
        future_company_comparison_rejected=(106,123) not in selected,
        selection_completed=report['selection_completed'],diagnostic_only=True,
        media_approved=False,registered_stock=False,published=False)
    if expected in selected:
        result['actual_company_selection']=selected[expected]['story_selection']
    result['passed']=all(result[k] for k in ('actual_company_qa_selected','generic_theory_rejected',
        'future_company_comparison_rejected','selection_completed'))
    (target/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    assert result['passed'],'Actual complete company Q&A or negative controls failed; not a production approval'


if __name__=='__main__':main()
