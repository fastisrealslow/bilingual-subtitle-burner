"""Derive actual episodes from every old parent; classify/audit without answers."""
import hashlib,json,os,subprocess,tempfile,zipfile
from pathlib import Path


def main():
    import produce_cn as P
    import publishing_experiment as E
    from source_selection import sentence_units
    from story_episodes import propose
    from transcript_audit import review
    assert P.TEXT_BACKEND=='local' and P.LOCAL_LLM_MODEL=='qwen3.5:9b'
    root=Path('complete-company-episode-check');root.mkdir(exist_ok=True)
    result=dict(passed=False,diagnostic_only=True,registered_stock=False,published=False,
        source_artifact_id=12458840653,policy_sha256=hashlib.sha256(Path(E.__file__).read_bytes()).hexdigest())
    def save(): (root/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'actual.zip'
        with path.open('wb') as f:subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/12458840653/zip'],stdout=f,check=True,timeout=120)
        with zipfile.ZipFile(path) as z:
            cues=json.loads(z.read('_tmp/cues_raw.json'))
            parents=json.loads(z.read('_tmp/source_question_answers.json'))
            source=json.loads(z.read('_tmp/source_quality.json'))
    assert source['source_sha256']=='8f379d9cecd36dc6b22d8022beed3a3a698aa217e8e0e48fdf4e2a575294b210'
    before=E.digest(cues);options=propose(cues,parents,sentence_units(cues))
    result.update(all_parent_candidates=len(parents),actual_episode_candidates=len(options),source_sha256=source['source_sha256'])
    save();assert options and E.digest(cues)==before
    def call(prompt,schema):return P.llm([dict(role='user',content=prompt)],'',temperature=0,max_tokens=1600,
        budget_sec=P.text_budget(240),response_schema=schema,read_cache=False)
    selected=E.choose_stories(cues,options,call,root/'actual-episode-selection.json')
    result.update(selection_completed=selected['selection_completed'],actual_selected=len(selected['selected']))
    save();assert selected['selection_completed'] and selected['selected']
    outcomes=[]
    for n,pick in enumerate(selected['selected']):
        text=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
        (root/f'episode-{n}.input.txt').write_text(text)
        report=review(text,'林园',P.LOCAL_LLM_MODEL,call,root/f'episode-{n}.audit.json')
        outcomes.append(dict(start=pick['start'],end=pick['end'],seconds=cues[pick['end']]['end']-cues[pick['start']]['start'],
            kind=pick['story_selection']['kind'],audit_clear=report['passed'],issues=report['issues']))
    result.update(episodes=outcomes,raw_source_unchanged=E.digest(cues)==before,
        passed=all(p['kind']=='company_case' and p['audit_clear'] for p in outcomes))
    save();print(json.dumps(result,ensure_ascii=False),flush=True)
    assert result['passed'],'No fully verified literal episode; do not promote'


if __name__=='__main__':main()
