"""Automatically reselect affected actual source evidence, then use production copy.

Diagnostic only: no manually supplied range/title, no cached model approval,
no media/stock/experiment sample registration and no publication.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile


def main():
    import produce_cn as producer
    import publishing_experiment as experiment
    import title_rewrite as title
    from editorial_policy import text_digest
    assert producer.TEXT_BACKEND=='local' and producer.LOCAL_LLM_MODEL=='qwen3.5:9b'
    assert producer._copy_style_identity('林园')['title_draft_profile']=='production'
    output=Path('story-future-detail-verification');output.mkdir(exist_ok=True)
    proof=dict(passed=False,diagnostic_only=True,registered_stock=False,published=False,
        sample_registered=False,media_approved=False,title_draft_profile='production')
    def save():
        (output/'result.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    save()
    try:
        artifact=int(os.environ.get('STORY_EDITORIAL_ARTIFACT','12453279032'))
        with tempfile.TemporaryDirectory(prefix='future-detail-actual-') as tmp:
            archive=Path(tmp)/'editorial.zip'
            with archive.open('wb') as handle:
                subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],
                    stdout=handle,check=True,timeout=180)
            with zipfile.ZipFile(archive) as z:
                source=json.loads(z.read('_tmp/source_quality.json'))
                raw=json.loads(z.read('_tmp/cues_raw.json'))
                original=json.loads(z.read('_tmp/source_question_answers.json'))
                selected=json.loads(z.read('_tmp/story_selection.json'))['selected']
                affected=[];negatives=[]
                original_ranges={(p['start'],p['end']) for p in original}
                for number,pick in enumerate(selected,1):
                    evidence=pick['story_selection']
                    text=''.join(c['text'] for c in raw[pick['start']:pick['end']+1])
                    assert experiment.digest(text)==evidence['transcript_sha256']
                    issue=(experiment.personal_detail_error(evidence['detail_quote'])
                           if evidence['kind']=='personal_story' else None)
                    if not issue:continue
                    assert (pick['start'],pick['end']) in original_ranges
                    audit=json.loads(z.read(f'_tmp/transcript_audit_{number}.json'))
                    assert audit['passed'] is True and audit['transcript_sha256']==text_digest(text)
                    request=json.loads(z.read(f'_tmp/title_request_{number}-1.json'))
                    units=json.loads(re.search(r'按原顺序编号的完整字幕：(\{[^\n]+\})',
                        request['messages'][0]['content'])[1])
                    assert [c['text'] for c in raw[pick['start']:pick['end']+1]]==[
                        units[str(i)] for i in range(len(units))]
                    affected.append({k:v for k,v in pick.items() if k!='story_selection'})
                    negatives.append(dict(selection=evidence,error=issue,rejected=True))
        assert source['passed'] is True and len(affected)==1
        proof.update(artifact=artifact,source_sha256=source['source_sha256'],
            source_question_ranges_sha256=experiment.digest(original),
            old_affected_evidence=negatives,automatically_affected_count=len(affected),
            experiment_code_sha256=hashlib.sha256(Path(experiment.__file__).read_bytes()).hexdigest())
        save()
        def call(prompt,schema):
            return producer.llm([dict(role='user',content=prompt)],'',temperature=0,
                max_tokens=1600,budget_sec=producer.text_budget(240),response_schema=schema,
                read_cache=False)
        result=experiment.choose_stories(raw,affected,call,output/'automatic-selection.json')
        proof.update(classifier_completed=result['selection_completed'],
            classifier_unresolved=result['unresolved'],new_selection_count=len(result['selected']))
        save()
        assert result['selection_completed'] and result['selected'], 'No grounded automatic reselection yet'
        outputs=[]
        for number,pick in enumerate(result['selected'],1):
            cues=raw[pick['start']:pick['end']+1]
            text=''.join(c['text'] for c in cues)
            evidence=pick['story_selection']
            if evidence['kind']=='personal_story':
                assert not experiment.personal_event_error(evidence['event_quote'])
                assert not experiment.personal_detail_error(evidence['detail_quote'])
            copy_dir=output/f'copy-{number}';copy_dir.mkdir(exist_ok=True)
            generated=producer.copywrite(cues,list(range(len(cues))),'林园','', '',copy_dir,
                source_sha256=source['source_sha256'],story_evidence=evidence)
            issue=producer.title_quality_error(generated['title'],'林园',text,
                rewrite_proof=generated['title_rewrite'])
            if issue:raise ValueError(issue)
            issue=title.forecast_copy_error(generated['title'],generated['cover_title'],text)
            if issue:raise ValueError(issue)
            outputs.append(dict(selection=evidence,title=generated['title'],cover_title=generated['cover_title']))
        proof.update(passed=True,outputs=outputs);save()
        print(json.dumps(proof,ensure_ascii=False),flush=True)
    except BaseException as exc:
        proof['error']=str(exc);save();raise


if __name__=='__main__':main()
