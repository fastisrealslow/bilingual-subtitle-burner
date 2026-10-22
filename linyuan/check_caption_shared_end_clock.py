"""Replay actual failed caption clocks; does not approve media or publish."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


def main():
    import caption_lines as current
    import caption_lines_legacy as strict
    import caption_readability as readability
    import publishing_experiment as experiment
    from presentation import live_card_layout
    from unittest.mock import patch
    out=Path('caption-shared-end-clock-verification');out.mkdir(exist_ok=True)
    proof=dict(passed=False,media_approved=False,stock_registered=False,sample_registered=False,
        published=False,raw_asr_modified=False,checks=[])
    def save():(out/'result.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    save()
    try:
        artifact=int(os.environ.get('CAPTION_EDITORIAL_ARTIFACT','12453554379'))
        with tempfile.TemporaryDirectory(prefix='caption-clock-actual-') as tmp:
            archive=Path(os.environ.get('CAPTION_EDITORIAL_ZIP') or str(Path(tmp)/'editorial.zip'))
            if not os.environ.get('CAPTION_EDITORIAL_ZIP'):
                with archive.open('wb') as stream:
                    subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],
                        stdout=stream,check=True,timeout=180)
            with zipfile.ZipFile(archive) as z:
                source=json.loads(z.read('_tmp/source_quality.json'))
                raw=json.loads(z.read('_tmp/cues_raw.json'))
                picks=json.loads(z.read('_tmp/story_selection.json'))['selected']
                audits={i:json.loads(z.read(f'_tmp/transcript_audit_{i}.json')) for i in range(1,len(picks)+1)}
        assert source['passed'] is True and source['source_sha256']=='1df744406655d5b19d20e13ccb0c8138955953aaa6d49978ee6fbb0053b320be'
        original=deepcopy(raw);fixed=controls=0
        for i,pick in enumerate(picks,1):
            cues=raw[pick['start']:pick['end']+1]
            assert experiment.digest(''.join(c['text'] for c in cues))==pick['story_selection']['transcript_sha256']
            assert audits[i]['passed'] is True
            origin=cues[0]['start']
            entries=[dict(zh=c['text'],start_sec=c['start']-origin,end_sec=c['end']-origin,
                caption_chars=[(char,a-origin,b-origin) for char,a,b in c['caption_chars']]) for c in cues]
            before_error=None
            with patch.object(current,'character_anchors',strict.character_anchors):
                try:previous,old_proof=readability.clean_entries(entries)
                except ValueError as exc:before_error=str(exc)
            cleaned,edit=readability.clean_entries(entries)
            assert readability.replay_edit_proof(edit)==(edit['raw_text'],edit['display_text'])
            clock=edit.get('source_clock_proof')
            if before_error:
                assert '逐字时间倒退或重叠' in before_error and clock
                fixed+=1
            else:
                assert not clock and cleaned==previous and edit==old_proof
                controls+=1
            screens=current.one_line_screens(cleaned,live_card_layout())
            assert screens and all(len(s['lines'])==1 for s in screens)
            assert [a for s in screens for a in s['caption_chars']]==[a for e in cleaned for a in e['caption_chars']]
            readability.write_edit_proof(out/f'edit-proof-{i}.json',edit)
            proof['checks'].append(dict(part=i,source_transcript_sha256=pick['story_selection']['transcript_sha256'],
                old_error=before_error,source_clock_proof=clock,screen_count=len(screens),single_line=True,
                unaffected_control_unchanged=not before_error))
        assert fixed>=1 and controls>=1 and raw==original
        proof.update(passed=True,artifact=artifact,source_sha256=source['source_sha256'],
            corrected_actual_candidates=fixed,unchanged_actual_controls=controls,
            character_starts_preserved=True,word_order_and_raw_source_preserved=True,
            caption_code_sha256=hashlib.sha256(Path(current.__file__).read_bytes()).hexdigest(),
            readability_code_sha256=hashlib.sha256(Path(readability.__file__).read_bytes()).hexdigest(),
            limitation='Caption-only replay; scene cover, rights, actual media and final publication gates are NOT approved')
        save();print(json.dumps(proof,ensure_ascii=False))
    except BaseException as exc:proof['error']=str(exc);save();raise


if __name__=='__main__':main()
