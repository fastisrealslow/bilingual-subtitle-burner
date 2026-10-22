"""Immutable actual-source negative/positive routing; no model or media approval."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import zipfile


def main():
    import publishing_experiment as E
    payload=subprocess.check_output(['gh','api',
        f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/12439207071/zip'],timeout=120)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        raw=json.loads(archive.read('_tmp/cues_raw.json'))
        selected=json.loads(archive.read('_tmp/story_selection.json'))['selected']
        source=json.loads(archive.read('_tmp/source_quality.json'))
    assert source['source_sha256']=='3b0c6a7b4d44f0f79d47e89c3dd061021540d98375d19decc8c7b55622ed4ded'
    assert len(selected)==2
    before=copy.deepcopy((raw,selected,source))
    for pick in selected:
        text=''.join(c['text'] for c in raw[pick['start']:pick['end']+1])
        assert E.digest(text)==pick['story_selection']['transcript_sha256']
    negative,positive=selected
    assert negative['story_selection']['event_quote']=='我自己觉得是没问题。'
    def validate(pick):
        return E.validate_choices(raw,[pick],dict(choices=[dict(index=0,
            **{k:pick['story_selection'][k] for k in ('kind','subject_quote','event_quote','detail_quote')})]))
    try:validate(negative)
    except ValueError as exc:assert 'self-assessment' in str(exc)
    else:raise AssertionError('Hypothetical bare assessment was treated as an actual event')
    assert positive['story_selection']['kind']=='company_case'
    assert '持有长虹' in positive['story_selection']['event_quote']
    assert len(validate(positive))==1
    assert (raw,selected,source)==before
    result=dict(passed=True,artifact=12439207071,source_sha256=source['source_sha256'],
        experiment_sha256=hashlib.sha256(Path(E.__file__).read_bytes()).hexdigest(),
        negative_rejected=True,actual_company_choice_still_structurally_valid=True,
        diagnostic_only=True,asr_approved=False,media_approved=False,registered_stock=False,published=False,
        note='Existing real company case still needs original ASR/name, media, identity, rights, copy and dedup gates.')
    out=Path('personal-event-proof-verification');out.mkdir(exist_ok=True)
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
