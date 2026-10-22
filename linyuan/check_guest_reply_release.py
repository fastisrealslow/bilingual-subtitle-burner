"""Fresh production copywriter replay on immutable actual failed-source evidence."""
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
    import title_rewrite as title
    from editorial_policy import text_digest
    from publishing_experiment import digest
    assert producer.TEXT_BACKEND=='local' and producer.LOCAL_LLM_MODEL=='qwen3.5:9b'
    assert producer._copy_style_identity('林园')['title_draft_profile']=='production'
    output=Path('guest-reply-release-verification');output.mkdir(exist_ok=True)
    proof=dict(passed=False,diagnostic_only=True,registered_stock=False,published=False,
        title_draft_profile='production',classifier_replayed=False,media_approved=False)
    def save():
        (output/'result.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    save()
    try:
        with tempfile.TemporaryDirectory(prefix='guest-reply-actual-') as tmp:
            archive=Path(tmp)/'editorial.zip'
            with archive.open('wb') as handle:
                subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/12407526223/zip'],
                    stdout=handle,check=True,timeout=180)
            with zipfile.ZipFile(archive) as z:
                source=json.loads(z.read('_tmp/source_quality.json'))
                raw=json.loads(z.read('_tmp/cues_raw.json'))
                selected=json.loads(z.read('_tmp/story_selection.json'))['selected']
                audit=json.loads(z.read('_tmp/transcript_audit.json'))
                request=json.loads(z.read('_tmp/title_request-1.json'))
        assert source['source_sha256']=='1df744406655d5b19d20e13ccb0c8138955953aaa6d49978ee6fbb0053b320be'
        assert source['passed'] is True and len(selected)==1 and audit['passed'] is True
        pick=selected[0];cues=raw[pick['start']:pick['end']+1]
        units=[c['text'] for c in cues];text=''.join(units)
        assert text_digest(text)==audit['transcript_sha256']
        assert digest(text)==pick['story_selection']['transcript_sha256']
        old_prompt=request['messages'][0]['content']
        original=json.loads(re.search(r'按原顺序编号的完整字幕：(\{[^\n]+\})',old_prompt)[1])
        assert units==[original[str(i)] for i in range(len(original))]
        old=json.loads(re.search(r'以下编号不能归为林园回答：(\[[^\n]+?\])。',old_prompt)[1])
        new=sorted(title.explicit_host_cues(units))
        assert 6 in old and 6 not in new and 5 in new
        proof.update(artifact=12407526223,source_sha256=source['source_sha256'],
            transcript_sha256=audit['transcript_sha256'],old_excluded=old,new_excluded=new,
            title_editor_sha256=hashlib.sha256(Path(title.__file__).read_bytes()).hexdigest())
        save()
        # No title/reader response or approval is replayed. This uses the normal
        # production writer and fresh independent review, with all current guards.
        generated=producer.copywrite(cues,list(range(len(cues))),'林园','', '',output,
            source_sha256=source['source_sha256'],story_evidence=pick['story_selection'])
        error=producer.title_quality_error(generated['title'],'林园',text,
            rewrite_proof=generated['title_rewrite'])
        if error:raise ValueError(error)
        error=title.forecast_copy_error(generated['title'],generated['cover_title'],text)
        if error:raise ValueError(error)
        proof.update(passed=True,title=generated['title'],cover_title=generated['cover_title'])
        save();print(json.dumps(proof,ensure_ascii=False),flush=True)
    except BaseException as exc:
        proof['error']=str(exc);save();raise


if __name__=='__main__':main()
