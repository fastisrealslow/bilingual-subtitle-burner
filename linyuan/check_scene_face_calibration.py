"""Real large faces, true blur controls, rejected bank and accepted-cover replay."""
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile

import cv2

from check_scene_cover_borders import fetch,sha
from cover_face_quality import measure


def main():
    import produce_cn as P
    from check_scene_cover_preflight import main as full_replay
    full_replay()
    out=Path('scene-cover-preflight-verification')
    known=json.loads((out/'result.json').read_text())
    assert known['known_good_cover_sha256']=='8519b1531d6944d0c4dcdd146791571dcee6eb35e22e3af9ebffd1a537b5bb45'
    result=dict(passed=False,diagnostic_only=True,production_changed=False,
        registered_stock=False,published=False,square_cover_approved=False,
        known_good_cover_byte_identical=True,rows=[])
    target=out/'calibration-result.json'
    def save():target.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    with tempfile.TemporaryDirectory(prefix='scene-face-calibration-') as tmp:
        root=Path(tmp)
        for group,artifact,pattern,source_sha in (
            ('large',12483437959,'/source-corners_11/source-',
             'f0ec81b1d824db72842edaa6d214a445614055d00a69a01f77757b180b07a7d7'),
            ('bank',12484435978,'/source-corners_portrait1/source-',
             '37bdc1449e1ce46a3df375f012cce6f3c1213b51da7f1afe7a6bb0c7da69f7a9')):
            archive=root/(group+'.zip');fetch(artifact,archive)
            folder=root/group;folder.mkdir()
            with zipfile.ZipFile(archive) as z:
                quality=json.loads(z.read('_tmp/source_quality.json'))
                assert quality['source_sha256']==source_sha
                for name in z.namelist():
                    if name.endswith('/speaker_reference.jpg') or pattern in name:
                        (folder/Path(name).name).write_bytes(z.read(name))
            frames=sorted(folder.glob('source-*.jpg'));assert len(frames)==10
            for frame in frames:
                _,native_box,native=P.select_verified_cover_face([frame],folder/'speaker_reference.jpg')
                _,box,identity=P.select_verified_cover_face([frame],folder/'speaker_reference.jpg',scene_quality=True)
                assert box==native_box and identity['cosine_score']==native['cosine_score']
                assert min(box[2:])>=96 and native['sharpness']<60
                x,y,w,h=box;roi=cv2.imread(str(frame))[y:y+h,x:x+w]
                detail=measure(roi)
                blur=[measure(cv2.GaussianBlur(roi,(0,0),sigma))['value'] for sigma in (3,6)]
                assert all(value<60 for value in blur)
                assert (identity['sharpness']>=60)==(group=='large')
                result['rows'].append(dict(group=group,source_sha256=source_sha,
                    frame=frame.name,frame_sha256=sha(frame),face_box=list(box),
                    native_identity=native,candidate_identity=identity,measurement=detail,
                    blur_sigmas=[3,6],blur_values=blur,media_approved=False))
                save()
    result.update(passed=True,actual_frame_cases=len(result['rows']),
                  blur_negative_controls=len(result['rows'])*2)
    save()
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False))


if __name__=='__main__':main()
