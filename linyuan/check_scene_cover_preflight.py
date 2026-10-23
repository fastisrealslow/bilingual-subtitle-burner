"""Real CPU inset-face regression and known-good native scene replay; no posts."""
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from check_scene_cover_borders import fetch,sha,original_function


def main():
    import produce_cn as P
    out=Path('scene-cover-preflight-verification');out.mkdir(exist_ok=True)
    result=dict(passed=False,diagnostic_only=True,registered_stock=False,published=False,
                model_requests=0,bank_cover_approved=False,bank_frames=[])
    def save():
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    P.llm=lambda *a,**k:(_ for _ in ()).throw(AssertionError('No text model in picture diagnostic'))
    old,_,control_sha=original_function('linyuan/produce_cn.py',
        '3e647f639c910733e787eeaba39d3329e9d08c77','select_verified_cover_face',vars(P))
    result.update(control_producer_sha256=control_sha,candidate_producer_sha256=sha(Path(P.__file__)))
    with tempfile.TemporaryDirectory(prefix='scene-cover-preflight-') as tmp:
        root=Path(tmp);archive=root/'bank.zip';fetch(12484435978,archive)
        bank=root/'bank';bank.mkdir()
        with zipfile.ZipFile(archive) as z:
            source=json.loads(z.read('_tmp/source_quality.json'))
            assert source['source_sha256']=='37bdc1449e1ce46a3df375f012cce6f3c1213b51da7f1afe7a6bb0c7da69f7a9'
            for name in z.namelist():
                if name.endswith('/speaker_reference.jpg') or '/source-corners_portrait1/source-' in name:
                    (bank/Path(name).name).write_bytes(z.read(name))
        result['bank_source_sha256']=source['source_sha256']
        result['bank_original_run_id']=39910123732
        for frame in sorted(bank.glob('source-*.jpg')):
            _,before,before_proof=old([frame],bank/'speaker_reference.jpg')
            _,after,after_proof=P.select_verified_cover_face([frame],bank/'speaker_reference.jpg')
            assert min(before[2:])<96 and before[0]<220
            assert min(after[2:])>=96 and after[0]>1000
            assert after_proof['cosine_score']>=after_proof['threshold']
            # The live person remains blurred. This patch MUST NOT approve it.
            assert after_proof['sharpness']<60
            result['bank_frames'].append(dict(frame=frame.name,sha256=sha(frame),
                control_box=list(before),candidate_box=list(after),
                control_proof=before_proof,candidate_proof=after_proof))
            save()
        assert len(result['bank_frames'])==10
        editorial=root/'editorial.zip';fetch(12417458565,editorial)
        with zipfile.ZipFile(editorial) as z:
            source=json.loads(z.read('_tmp/source_quality.json'))
            cues=json.loads(z.read('_tmp/cues_raw.json'))
            pick=json.loads(z.read('_tmp/story_selection.json'))['selected'][0]
        assert source['source_sha256']=='6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d'
        mother=root/'mother.zip';fetch(12414668738,mother)
        video=root/'video.mp4'
        with zipfile.ZipFile(mother) as z:
            names=[n for n in z.namelist() if Path(n).name=='video.mp4'];assert len(names)==1
            with z.open(names[0]) as stream,video.open('wb') as handle:shutil.copyfileobj(stream,handle)
        assert sha(video)==source['source_sha256']
        reference=P._download_speaker_reference('林园',out)
        cover=out/'cover.jpg'
        P.make_cover(video,cues[pick['start']]['start'],cues[pick['end']]['end'],
            '', '林园',cover,video_filter='',reference_path=reference,style='scene',
            allow_editorial_fallback=False,clean_scene_crop=True,
            preferred_time=(source.get('visual_identity') or {}).get('best_cover_time'))
        proof=json.loads(Path(str(cover)+'.proof.json').read_text())
        assert all(proof[k] is True for k in ('no_added_text','no_black_bars','no_qr',
                                               'face_fully_visible','feed_safe_face','feed_safe_text'))
        identity=proof['source_identity']
        assert min(identity['face_box'][2:])>=96 and identity['sharpness']>=60
        result.update(passed=True,known_good_cover_passed=True,known_good_source_sha256=sha(video),
                      known_good_cover_sha256=sha(cover),known_good_proof=proof)
        save()
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
