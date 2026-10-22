"""Replay actual source752 evidence failure; never cut, approve or publish it."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


def main():
    import produce_cn as producer
    import editorial_evidence as evidence
    from source_selection import question_unit
    from editorial_policy import text_digest
    from publishing_experiment import digest as story_digest
    assert producer.TEXT_BACKEND=='local' and producer.LOCAL_LLM_MODEL=='qwen3.5:9b'
    assert producer.automatic_only()
    output=Path('claim-span-verification');output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='claim-span-actual-') as tmp:
        archive=Path(tmp)/'editorial.zip'
        with archive.open('wb') as handle:
            subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/12411399718/zip'],
                stdout=handle,check=True,timeout=180)
        with zipfile.ZipFile(archive) as z:
            source=json.loads(z.read('_tmp/source_quality.json'))
            cues=json.loads(z.read('_tmp/cues_raw.json'))
            selected=json.loads(z.read('_tmp/story_selection.json'))['selected']
            old=json.loads(z.read('_tmp/editorial_response-1.txt'))
    assert source['source_sha256']=='6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d'
    assert source['passed'] is True and len(selected)==1
    text=''.join(c['text'] for p in selected for c in cues[p['start']:p['end']+1])
    assert story_digest(text)==selected[0]['story_selection']['transcript_sha256']
    units=evidence.sentences(text)
    excluded=[i for i,u in enumerate(units) if question_unit(u)]
    old_range=old['analysis']['claim_range']
    assert old_range==[0,3] and any(i in excluded for i in range(old_range[0],old_range[1]+1))
    claim_schema=evidence.schema(units,excluded)['claim_range']
    assert not any(all(i in b['items']['enum'] for i in old_range) for b in claim_schema['anyOf'])
    report=dict(source_sha256=source['source_sha256'],transcript_sha256=text_digest(text),
        artifact_id=12411399718,old_claim_range=old_range,excluded=excluded,
        evidence_editor_sha256=hashlib.sha256(Path(evidence.__file__).read_bytes()).hexdigest(),
        passed=False,diagnostic_only=True,registered_stock=False,published=False)
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    # Keep the complete automatically selected passage, including ALL host
    # context. No hand-selected claim, title, cue edit or per-source approval.
    result=producer.review_complete_argument(cues,selected,'林园','',output,'')
    assert result['claim_quote'] and result['claim_quote'] in text
    assert not any(question_unit(u) for u in evidence.sentences(result['claim_quote']))
    assert result['opening_quote']==units[0] and result['ending_quote']==units[-1]
    report.update(passed=True,claim_quote=result['claim_quote'],reasoning_quote=result['reasoning_quote'],
        conclusion_quote=result['conclusion_quote'],review=result)
    (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
