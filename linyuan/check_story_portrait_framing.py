"""Actual portrait visual preflight only; never editorial/media approval."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch
import zipfile


def fetch(artifact,path):
    with path.open('wb') as handle:
        subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],
            stdout=handle,check=True,timeout=300)


class VisualPreflightComplete(Exception):pass


def main():
    import produce_cn as producer
    import transcript_audit
    from editorial_policy import text_digest
    from publishing_experiment import digest
    from presentation import live_card_layout
    out=Path('story-portrait-framing-verification');out.mkdir(exist_ok=True)
    result=dict(passed=False,diagnostic_only=True,visual_only=True,media_approved=False,
        registered_stock=False,published=False,editorial_stages_not_retested=True)
    def save(): (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    try:
        assert os.environ.get('LINYUAN_EDITORIAL_PROFILE')=='field-story-v1'
        assert os.environ.get('OUTPUT_LAYOUT')=='portrait'
        with tempfile.TemporaryDirectory(prefix='story-portrait-original-') as tmp:
            directory=Path(tmp);editorial=directory/'editorial.zip';mother=directory/'mother.zip'
            fetch(12417458565,editorial)
            with zipfile.ZipFile(editorial) as z:
                source=json.loads(z.read('_tmp/source_quality.json'))
                cues=json.loads(z.read('_tmp/cues_raw.json'))
                pick=json.loads(z.read('_tmp/story_selection.json'))['selected'][0]
                copy=json.loads(z.read('_tmp/copywrite_1.json'))
                audit=json.loads(z.read('_tmp/transcript_audit_1.json'))
                argument=json.loads(z.read('_tmp/editorial_review_1.json'))
            assert source['passed'] and source['source_sha256']=='6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d'
            text=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
            assert pick['story_selection']['kind']=='company_case'
            assert digest(text)==pick['story_selection']['transcript_sha256']
            assert audit['passed'] and audit['transcript_sha256']==text_digest(text)
            assert not producer.title_quality_error(copy['title'],'林园',text,rewrite_proof=copy['title_rewrite'])
            fetch(12414668738,mother)
            video=directory/'video.mp4'
            with zipfile.ZipFile(mother) as z:
                names=[n for n in z.namelist() if Path(n).name=='video.mp4'];assert len(names)==1
                with z.open(names[0]) as stream,video.open('wb') as handle:shutil.copyfileobj(stream,handle)
            assert producer._sha256_file(video)==source['source_sha256']
            work=out/'preflight';work.mkdir(exist_ok=True)
            # Isolate visual routing. Existing immutable editorial results are
            # not claimed as new approval; stop before any copy/caption model.
            with patch.object(producer,'argument_record_for_render',return_value=argument),\
                 patch.object(transcript_audit,'review',return_value=audit),\
                 patch.object(producer,'copywrite',side_effect=VisualPreflightComplete),\
                 patch.object(producer,'llm',side_effect=AssertionError('No model calls in visual-only replay')):
                try:
                    producer._produce_one(video,work,out,cues,'林园','访谈','',False,1920,1080,
                        '_actual',preselected_picks=[pick],source_report=source,require_live_video=True)
                except VisualPreflightComplete:pass
                else:raise AssertionError('Visual diagnostic must stop before editorial generation')
            tracked=work/'tracked_actual1.mp4';proof=json.loads(tracked.with_suffix('.json').read_text())
            motion=json.loads((work/'tracked-motion_actual1.json').read_text())
            assert proof['passed'] and proof['framing']['policy']=='shot_scale_lock_deadzone_pan' and motion['passed']
            start=cues[pick['start']]['start'];duration=cues[pick['end']]['end']-start
            portrait=producer.extract_audio_card_portrait(producer._download_speaker_reference('林园',work),work/'reference.png')
            background=producer.make_audio_card(work/'canvas.png','林园',copy['cover_title'],
                portrait_path=portrait,require_portrait=True,live_video=True)
            preview=out/'portrait-visual-preview.mp4'
            subprocess.run(['ffmpeg','-y','-loglevel','error','-loop','1','-framerate','30','-i',str(background),
                '-i',str(tracked),'-ss',str(start),'-i',str(video),'-filter_complex',
                '[0:v][1:v]overlay=44:360:shortest=1[v]','-map','[v]','-map','2:a:0',
                '-t',str(duration),'-af','asetpts=PTS-STARTPTS','-c:v','libx264','-preset','veryfast',
                '-crf','18','-c:a','aac',str(preview)],check=True,timeout=300)
            width,height=producer.video_size(preview)
            assert (width,height)==(720,1280)
            identity=producer.verify_final_live_identity(preview,work,'林园','',suffix='_portrait',
                live_region=live_card_layout()['live_region'])
            subprocess.run(['ffmpeg','-v','error','-i',str(preview),'-f','null','-'],check=True,timeout=300)
            for second in (5,15,25):
                if second>=duration:continue
                subprocess.run(['ffmpeg','-y','-loglevel','error','-ss',str(second),'-i',str(preview),
                    '-frames:v','1',str(out/f'frame-{second}.jpg')],check=True,timeout=60)
            result.update(passed=True,source_sha256=source['source_sha256'],transcript_sha256=digest(text),
                producer_sha256=hashlib.sha256(Path(producer.__file__).read_bytes()).hexdigest(),
                actual_resolution=dict(width=width,height=height),full_decode_passed=True,
                tracking=proof,motion=motion,identity=identity,
                note='Actual moving-window route and pixels only. No subtitles or new editorial acceptance; not publishable media.')
    except BaseException as exc:
        result['error']=str(exc);save();raise
    save();print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
