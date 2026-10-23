"""Replay a real failed source/selection with actual old and current covers."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from check_scene_cover_borders import fetch,original_function,sha


def main():
    import produce_cn as P
    import publishing_experiment as E
    out=Path('scene-cover-frame-text-verification');out.mkdir(exist_ok=True)
    result=dict(passed=False,registered_stock=False,published=False,final_media_approved=False,
        source_run_id=39938160707,editorial_artifact_id=12496550349,
        mother_artifact_id=12493948765,control_ref='0f1e27d208f0d98c3acb3a15d4e72763250b839d')
    def save():(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    with tempfile.TemporaryDirectory(prefix='scene-frame-actual-') as folder:
        folder=Path(folder);editorial=folder/'editorial.zip';mother=folder/'mother.zip';debug=folder/'debug.zip'
        fetch(result['editorial_artifact_id'],editorial)
        with zipfile.ZipFile(editorial) as z:
            source=json.loads(z.read('_tmp/source_quality.json'))
            cues=json.loads(z.read('_tmp/cues_raw.json'))
            selected=json.loads(z.read('_tmp/story_selection.json'))['selected']
            batch=json.loads(z.read('batch_report.json'))
        assert source['passed'] and source['source_sha256']=='fc8b5dc5ebf11a64b701bc871c60bc8af8eaf57599968aa6c5478d9c93cfb7f2'
        assert len(selected)==1 and batch['accepted']==0
        assert '原始像素中没有避开来源文字' in batch['rejected'][0]['reason']
        pick=selected[0];proof=pick['story_selection']
        E.validate_choices(cues,[pick],dict(choices=[dict(index=0,**{
            k:proof[k] for k in ('kind','subject_quote','event_quote','detail_quote')})]))
        start,end=cues[pick['start']]['start'],cues[pick['end']]['end']
        fetch(result['mother_artifact_id'],mother)
        video=folder/'video.mp4'
        with zipfile.ZipFile(mother) as z:
            names=[n for n in z.namelist() if Path(n).name=='video.mp4'];assert len(names)==1
            with z.open(names[0]) as src,video.open('wb') as dest:shutil.copyfileobj(src,dest)
        assert sha(video)==source['source_sha256']
        fetch(12496525467,debug)
        with zipfile.ZipFile(debug) as z:
            assert json.loads(z.read('_tmp/source_quality.json'))['source_sha256']==source['source_sha256']
            reference=folder/'reference.jpg';reference.write_bytes(z.read('_tmp/speaker_reference.jpg'))
        old,_,control_sha=original_function('linyuan/produce_cn.py',result['control_ref'],'make_cover',P.__dict__)
        result.update(source_sha256=sha(video),reference_sha256=sha(reference),
            original_cues_sha256=hashlib.sha256(json.dumps(cues,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
            selection=dict(start=start,end=end),control_producer_sha256=control_sha,
            proposed_producer_sha256=sha(Path(P.__file__)))
        for name,make in (('control',old),('proposed',P.make_cover)):
            work=out/name;work.mkdir(exist_ok=True)
            row=dict(created=False)
            try:
                make(video,start,end,'','林园',work/'cover.jpg',video_filter='',
                    preferred_time=source['visual_identity']['best_cover_time'],reference_path=reference,
                    style='scene',allow_editorial_fallback=False,clean_scene_crop=True)
                row.update(created=True,sha256=sha(work/'cover.jpg'),
                    proof=json.loads((work/'cover.jpg.proof.json').read_text()))
            except (ValueError,P.VisualQualityError) as exc:row['error']=f'{type(exc).__name__}: {exc}'
            result[name]=row;save()
        assert not result['control']['created'] and '没有避开来源文字' in result['control']['error']
        assert result['proposed']['created']
        cover=result['proposed']['proof'];identity=cover['source_identity']
        assert cover['style']=='scene' and cover['no_added_text'] and cover['face_fully_visible']
        assert cover['no_black_bars'] and cover['no_qr'] and cover['feed_safe_face']
        assert min(identity['face_box'][2:])>=96 and identity['sharpness']>=60
        assert identity['source_frame_crop']['input']==[1280,720]
        result['passed']=True;save()
    print(json.dumps({k:v for k,v in result.items() if k not in ('control','proposed')},ensure_ascii=False))


if __name__=='__main__':main()
