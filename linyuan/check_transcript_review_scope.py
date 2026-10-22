"""Replay actual failed transcripts; no media approval, stock or publication."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

from editorial_policy import text_digest
from transcript_audit import review, VERSION


CASES=[('relative_zero',12408528520,
        '67ed2a6894ef419b44607632623c02153fc724907c233a20e33629e7125cd42e',True),
       ('fund',12402754120,
        'e70b4632acd562154e0601cc29e48e6864bca4b7d0e56fa542b54ca37a2b4884',False),
       ('percentage',12404611301,
        'c7a533ce67ed9d7dabb2805a6c8396370ad98de4574de748a218ac52154ce362',False)]


def original_text(archive, expected_sha, *, audit_name='_tmp/transcript_audit.json'):
    with zipfile.ZipFile(archive) as z:
        source=json.loads(z.read('_tmp/source_quality.json'))
        cues=json.loads(z.read('_tmp/cues_raw.json'))
        selection=json.loads(z.read('_tmp/story_selection.json'))
        old=json.loads(z.read(audit_name))
    if source.get('source_sha256')!=expected_sha or source.get('passed') is not True:
        raise ValueError('Actual source identity/quality evidence does not match')
    picks=selection.get('selected') or []
    if not picks or (audit_name=='_tmp/transcript_audit.json' and len(picks)!=1):
        raise ValueError('Need the exact actually failed production part')
    pick=picks[0]
    text=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
    if text_digest(text)!=old.get('transcript_sha256'):
        raise ValueError('Reconstructed text is not the exact originally audited transcript')
    return text,old


def main():
    from produce_cn import llm,LOCAL_LLM_MODEL,TEXT_BACKEND
    if TEXT_BACKEND!='local' or LOCAL_LLM_MODEL!='qwen3.5:9b':
        raise ValueError('Must replay the actual current local production text model')
    output=Path('transcript-scope-verification');output.mkdir(exist_ok=True)
    result=dict(version=VERSION,model=LOCAL_LLM_MODEL,diagnostic_only=True,
                media_approved=False,registered_stock=False,published=False,cases=[],passed=False)
    def call(prompt,schema):
        return llm([dict(role='user',content=prompt)],'',temperature=0,max_tokens=1200,
                   budget_sec=600,response_schema=schema,read_cache=False)
    def save():
        (output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    for name,artifact,sha,expected in CASES:
        with tempfile.TemporaryDirectory(prefix='transcript-scope-original-') as directory:
            archive=Path(directory)/'editorial.zip'
            with archive.open('wb') as handle:
                subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],
                               stdout=handle,check=True,timeout=180)
            text,old=original_text(archive,sha,audit_name=('_tmp/transcript_audit_1.json'
                if name=='relative_zero' else '_tmp/transcript_audit.json'))
        proof=review(text,'林园',LOCAL_LLM_MODEL,call,output/(name+'.json'))
        row=dict(name=name,artifact=artifact,source_sha256=sha,
                 transcript_sha256=text_digest(text),original_issues=old['issues'],
                 expected_pass=expected,actual_pass=proof['passed'],
                 audio_verified=proof['audio_verified'])
        result['cases'].append(row);save();print(json.dumps(row,ensure_ascii=False),flush=True)
        if proof['passed'] is not expected or proof['audio_verified'] is not False:
            raise ValueError('Actual replay did not preserve the intended review scope: '+name)
        suspects='\n'.join(issue.get('suspect_quote','') for issue in proof['issues'])
        if name=='fund' and not any(term in suspects for term in ('十八米','往下新股')):
            raise ValueError('Fund negative must retain an actual unintelligible phrase, not a missing-subject objection')
        if name=='percentage' and not any(term in suspects for term in ('百分之二一','百分之五百')):
            raise ValueError('Percentage negative lost the actual malformed numeric phrase')
    # A wrong proper noun is still a blocker, not a numeric/style objection.
    name='corrupted_proper_noun';text='这个指标叫道琼市指数。'
    proof=review(text,'林园',LOCAL_LLM_MODEL,call,output/(name+'.json'))
    result['cases'].append(dict(name=name,expected_pass=False,actual_pass=proof['passed']))
    save()
    if proof['passed']:raise ValueError('Unintelligible proper noun was incorrectly waived')
    result['passed']=True;save()


if __name__=='__main__':main()
