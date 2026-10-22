"""Paired original-media picture replay. No editorial approval, stock or posts."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch
import zipfile


CASES=(
    dict(name='first_usable_wide',editorial=12417458565,mother=12437479863,
         delivery=12440338758,sha='6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d',
         expected=['portrait','native']),
    dict(name='old_portrait_duplicate_not_stock',editorial=12259757037,mother=12457656605,
         delivery=12260021686,sha='8f379d9cecd36dc6b22d8022beed3a3a698aa217e8e0e48fdf4e2a575294b210',
         expected=['portrait']),
)


def fetch(artifact,path):
    with path.open('wb') as handle:
        subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],
                       stdout=handle,check=True,timeout=300)


def read_json(archive,suffix):
    names=[n for n in archive.namelist() if n==suffix or n.endswith('/'+suffix)]
    if len(names)!=1:raise ValueError('Expected exactly one '+suffix)
    return json.loads(archive.read(names[0]))


class PictureCheckpoint(BaseException):pass


def main():
    import produce_cn as producer
    import transcript_audit
    from publishing_experiment import digest
    out=Path('safe-portrait-preference-verification');out.mkdir(exist_ok=True)
    result=dict(passed=False,diagnostic_only=True,visual_only=True,editorial_stages_not_retested=True,
                media_approved=False,registered_stock=False,published=False,cases=[])
    def save():(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    try:
        assert os.environ.get('LINYUAN_AUTOMATIC_ONLY')=='true'
        assert os.environ.get('LINYUAN_EDITORIAL_PROFILE')=='field-story-v1'
        assert os.environ.get('OUTPUT_LAYOUT')=='portrait'
        for case in CASES:
            work=out/case['name'];work.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='portrait-paired-original-') as directory:
                directory=Path(directory)
                for key in ('editorial','delivery','mother'):fetch(case[key],directory/(key+'.zip'))
                with zipfile.ZipFile(directory/'editorial.zip') as z:
                    source=read_json(z,'_tmp/source_quality.json');cues=read_json(z,'_tmp/cues_raw.json')
                    choices=read_json(z,'_tmp/story_selection.json')['selected']
                with zipfile.ZipFile(directory/'delivery.zip') as z:meta=read_json(z,'meta.json')
                assert source['passed'] is True and source['source_sha256']==meta['source_sha256']==case['sha']
                matches=[p for p in choices if len(meta['segments'])==1
                    and abs(cues[p['start']]['start']-meta['segments'][0]['start'])<.001
                    and abs(cues[p['end']]['end']-meta['segments'][0]['end'])<.001]
                assert len(matches)==1
                pick=matches[0];text=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
                assert digest(text)==pick['story_selection']['transcript_sha256']
                video=directory/'video.mp4'
                with zipfile.ZipFile(directory/'mother.zip') as z:
                    names=[n for n in z.namelist() if Path(n).name=='video.mp4'];assert len(names)==1
                    with z.open(names[0]) as stream,video.open('wb') as handle:shutil.copyfileobj(stream,handle)
                assert producer._sha256_file(video)==case['sha']
                calls=[];original=producer._produce_one
                def record(*args,**kwargs):
                    calls.append('portrait' if kwargs.get('_portrait_attempt') else 'native')
                    return original(*args,**kwargs)
                # Stop, do not approve, at the first independent ASR-review call.
                # No cached approval/model response or made-up title is supplied.
                with patch.object(producer,'_produce_one',record),\
                     patch.object(producer,'argument_record_for_render',return_value=dict(status='not_retested')),\
                     patch.object(transcript_audit,'review',side_effect=PictureCheckpoint),\
                     patch.object(producer,'copywrite',side_effect=AssertionError('No title approval in picture replay')),\
                     patch.object(producer,'llm',side_effect=AssertionError('No model calls in picture replay')):
                    try:
                        producer.produce_part_with_budget(video,work,out,cues,'林园','访谈','',False,
                            source['resolution']['width'],source['resolution']['height'],'_paired',
                            preselected_picks=[pick],source_report=source,require_live_video=True,budget_sec=900)
                    except PictureCheckpoint:pass
                    else:raise AssertionError('Must stop before ASR/copy, never generate approved delivery')
                assert calls==case['expected'],calls
                row=dict(name=case['name'],source_sha256=case['sha'],transcript_sha256=digest(text),
                         route_calls=calls,picture_checkpoint_reached=True,media_approved=False)
                if calls[-1]=='native':
                    preflight=work/'native-preflight_paired.mp4'
                    width,height=producer.video_size(preflight)
                    assert width>height
                    rejected=json.loads((work/'tracked_paired_portrait1.preflight.json').read_text())
                    assert rejected['outcome']=='impossible'
                    row.update(native_resolution=[width,height],portrait_rejection=rejected)
                else:
                    tracked=work/'tracked_paired_portrait1.mp4'
                    tracking=json.loads(tracked.with_suffix('.json').read_text())
                    motion=json.loads((work/'tracked-motion_paired_portrait1.json').read_text())
                    assert tracking['passed'] and motion['passed']
                    start=cues[pick['start']]['start'];duration=cues[pick['end']]['end']-start
                    # A labelled picture probe, not a subtitle/title/cover candidate.
                    preview=work/'diagnostic-portrait.mp4'
                    subprocess.run(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i',
                        'color=c=black:s=720x1280:r=30','-i',str(tracked),'-ss',str(start),'-i',str(video),
                        '-filter_complex','[0:v][1:v]overlay=44:360:shortest=1[v]',
                        '-map','[v]','-map','2:a:0','-t',str(duration),'-af','asetpts=PTS-STARTPTS',
                        '-c:v','libx264','-preset','veryfast','-crf','18','-c:a','aac',str(preview)],
                        check=True,timeout=300)
                    assert producer.video_size(preview)==(720,1280)
                    identity=producer.verify_final_live_identity(preview,work,'林园','',suffix='_paired',
                        live_region=dict(x=44,y=360,width=632,height=470))
                    subprocess.run(['ffmpeg','-v','error','-i',str(preview),'-f','null','-'],check=True,timeout=180)
                    for seconds in (5,25,60):
                        if seconds<duration:
                            subprocess.run(['ffmpeg','-y','-loglevel','error','-ss',str(seconds),'-i',str(preview),
                                '-frames:v','1',str(work/f'frame-{seconds}.jpg')],check=True,timeout=45)
                    row.update(actual_resolution=[720,1280],tracking=tracking,motion=motion,
                               final_identity=identity,full_decode_passed=True,
                               publication_status='old overlapping example, not a stock or publication candidate')
                result['cases'].append(row);save()
        result.update(passed=True,producer_sha256=hashlib.sha256(Path(producer.__file__).read_bytes()).hexdigest())
    except BaseException as exc:
        result['error']=str(exc);save();raise
    save();print(json.dumps(dict(passed=True,visual_only=True,media_approved=False,
        cases=[dict(name=r['name'],route_calls=r['route_calls']) for r in result['cases']])))


if __name__=='__main__':main()
