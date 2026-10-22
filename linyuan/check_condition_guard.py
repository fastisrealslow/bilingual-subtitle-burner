"""Reject real machine false approvals; no drafting, stock or publication."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


def main():
    import title_rewrite as title
    from editorial_policy import text_digest
    with tempfile.TemporaryDirectory(prefix='condition-original-') as tmp:
        data={}
        for artifact in (12407526223,12406859515,12410065151,12410941172):
            archive=Path(tmp)/(str(artifact)+'.zip')
            with archive.open('wb') as handle:
                subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],
                    stdout=handle,check=True,timeout=180)
            with zipfile.ZipFile(archive) as z:
                if artifact==12407526223:
                    source=json.loads(z.read('_tmp/source_quality.json'))
                    cues=json.loads(z.read('_tmp/cues_raw.json'))
                    pick=json.loads(z.read('_tmp/story_selection.json'))['selected'][0]
                    audit=json.loads(z.read('_tmp/transcript_audit.json'))
                else:
                    data[artifact]=json.loads(z.read('boundary.json'))
                    if artifact==12410065151:
                        positive=json.loads(z.read('title_response-2.txt'))['c_candidates'][0]
    assert source['source_sha256']=='1df744406655d5b19d20e13ccb0c8138955953aaa6d49978ee6fbb0053b320be'
    assert source['passed'] is True and audit['passed'] is True
    text=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
    assert text_digest(text)==audit['transcript_sha256']
    controls=[]
    for artifact,row in data.items():
        assert row['source_sha256']==source['source_sha256']
        issue=title.human_condition_actor_error(row['title'],row['cover_title'],text)
        assert issue and title.forecast_copy_error(row['title'],row['cover_title'],text)
        controls.append(dict(artifact=artifact,title=row['title'],cover_title=row['cover_title'],rejected=True,reason=issue))
    assert positive['title']=='林园：我只投我看好的消费，在一亩三分地耕耘'
    assert title.human_condition_actor_error(positive['title'],positive['cover_title'],text) is None
    result=dict(source_sha256=source['source_sha256'],transcript_sha256=audit['transcript_sha256'],
        title_policy_sha256=hashlib.sha256(Path(title.__file__).read_bytes()).hexdigest(),
        controls=controls,positive_not_blocked_by_this_guard=True,passed=True,
        diagnostic_only=True,media_approved=False,registered_stock=False,published=False)
    Path('condition-guard-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
