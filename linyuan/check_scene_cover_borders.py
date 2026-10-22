"""Replay actual native-cover failures on original pixels, no models or posts."""
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


def sha(path):
    result=hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda:handle.read(4*1024*1024),b''):result.update(chunk)
    return result.hexdigest()


def main():
    import produce_cn as producer
    import cover_scene_geometry
    from editorial_policy import text_digest
    from publishing_experiment import digest
    out=Path('scene-cover-verification');out.mkdir(exist_ok=True)
    result=dict(passed=False,diagnostic_only=True,registered_stock=False,published=False,
        media_approved=False,parts=[])
    def save(): (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    with tempfile.TemporaryDirectory(prefix='scene-cover-actual-') as tmp:
        directory=Path(tmp);editorial=directory/'editorial.zip';mother=directory/'mother.zip'
        fetch(12417458565,editorial)
        with zipfile.ZipFile(editorial) as z:
            source=json.loads(z.read('_tmp/source_quality.json'))
            cues=json.loads(z.read('_tmp/cues_raw.json'))
            picks=json.loads(z.read('_tmp/story_selection.json'))['selected']
            copies=[json.loads(z.read(f'_tmp/copywrite_{i}.json')) for i in range(1,len(picks)+1)]
            audits=[json.loads(z.read(f'_tmp/transcript_audit_{i}.json')) for i in range(1,len(picks)+1)]
        assert source['passed'] and source['source_sha256']=='6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d'
        assert len(picks)==2
        fetch(12414668738,mother)
        video=directory/'video.mp4'
        with zipfile.ZipFile(mother) as z:
            names=[n for n in z.namelist() if Path(n).name=='video.mp4']
            assert len(names)==1
            with z.open(names[0]) as stream,video.open('wb') as handle:shutil.copyfileobj(stream,handle)
        assert sha(video)==source['source_sha256']
        reference=producer._download_speaker_reference('林园',out)
        result.update(source_sha256=source['source_sha256'],reference_sha256=sha(reference),
            original_run_id=39753799731,editorial_artifact=12417458565,mother_artifact=12414668738)
        for i,(pick,copy,audit) in enumerate(zip(picks,copies,audits),1):
            text=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
            assert digest(text)==pick['story_selection']['transcript_sha256']
            assert audit['passed'] and text_digest(text)==audit['transcript_sha256']
            assert producer.title_quality_error(copy['title'],'林园',text,rewrite_proof=copy['title_rewrite']) is None
            row=dict(index=i,title=copy['title'],cover_title=copy['cover_title'],passed=False,
                transcript_sha256=audit['transcript_sha256'],samples=[])
            result['parts'].append(row);save()
            for variant in ('control','proposed'):
                work=out/f'part{i}'/variant;work.mkdir(parents=True,exist_ok=True)
                captured=work/'samples';captured.mkdir(exist_ok=True)
                original_ocr=producer.detect_corner_logos_in_images
                def record_frames(frames,*args,**kwargs):
                    if not row.get('captured_'+variant):
                        for k in sorted({0,len(frames)//2,len(frames)-1}):
                            dest=captured/f'original-{k}.png';shutil.copyfile(frames[k],dest)
                            row['samples'].append(str(dest.relative_to(out)))
                        row['captured_'+variant]=True;save()
                    return original_ocr(frames,*args,**kwargs)
                def no_fix(frames):return [],dict(control_original_crop_logic=True,final_quality_approved=False)
                try:
                    with patch.object(producer,'detect_corner_logos_in_images',record_frames):
                        if variant=='control':
                            with patch.object(cover_scene_geometry,'border_exclusions',no_fix):
                                producer.make_cover(video,cues[pick['start']]['start'],cues[pick['end']]['end'],
                                    copy['cover_title'],'林园',work/'cover.jpg',video_filter='',
                                    preferred_time=(source.get('visual_identity') or {}).get('best_cover_time'),
                                    reference_path=reference,style='scene',allow_editorial_fallback=False,clean_scene_crop=True)
                        else:
                            producer.make_cover(video,cues[pick['start']]['start'],cues[pick['end']]['end'],
                                copy['cover_title'],'林园',work/'cover.jpg',video_filter='',
                                preferred_time=(source.get('visual_identity') or {}).get('best_cover_time'),
                                reference_path=reference,style='scene',allow_editorial_fallback=False,clean_scene_crop=True)
                except Exception as exc:
                    row[variant+'_error']=str(exc);save()
                    if variant=='control':
                        assert '现场原画封面存在黑色填充边' in str(exc)
                    else:raise
                else:
                    if variant=='control':raise ValueError('Original failure did not reproduce')
                    proof=json.loads((work/'cover.jpg.proof.json').read_text())
                    assert all(proof[k] is True for k in ('no_added_text','no_black_bars','no_qr','face_fully_visible','feed_safe_face'))
                    row.update(passed=True,proof=proof,cover_sha256=sha(work/'cover.jpg'));save()
        result['passed']=all(row['passed'] for row in result['parts']);save()
        assert result['passed']
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
