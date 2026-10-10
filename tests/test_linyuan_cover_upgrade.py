import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import editorial_cover as C
import presentation as V
import upgrade_cover_delivery as U
from fc import index as FC


def font():
    try:
        return C.font_path()
    except ValueError:
        pytest.skip('Chinese font required')


def bundle(tmp_path):
    Image.new('RGB', (1280,720), '#84735e').save(tmp_path/'cover.jpg')
    (tmp_path/'final.mp4').write_bytes(b'unchanged verified media fixture')
    (tmp_path/'subtitles.ass').write_bytes(b'unchanged caption fixture')
    row = dict(slug='ly-test', final='final.mp4', cover='cover.jpg', speaker='林园',
        title='林园：长期投资不是永远不变', cover_title='长期投资不是永远不变',
        fingerprints=dict(sha256=U.digest(tmp_path/'final.mp4')),
        subtitle_files=['subtitles.ass'], cover_person_image_verified=True,
        cover_person_image_source='authority_reference',
        cover_proof=dict(style='dark', face_box=[744,42,972,278]))
    (tmp_path/'meta.json').write_text(json.dumps(row, ensure_ascii=False))
    return row


@pytest.mark.parametrize('title',['长期投资不是永远不变','人均收入不减少十二三年投资回本'])
@pytest.mark.parametrize('style',['dark','light'])
def test_both_real_portrait_palettes_keep_complete_copy_and_pass_actual_hash_gate(tmp_path,title,style):
    Image.new('RGB',(320,440),'#ba9868').save(tmp_path/'portrait.jpg')
    image,lines,size,boxes,face=V.dark_cover(tmp_path/'portrait.jpg',title,'林园',font(),
        C.font_face_index(font()),palette=style)
    image.save(tmp_path/'cover.jpg')
    proof=V.cover_proof(image,tmp_path/'cover.jpg',lines,size,boxes,style=style,
        face_box=face,feed_crop=V.FEED_WIDE_CROP)
    assert ''.join(lines)==title
    assert size==112 and all(box[2]<=face[0] for box in boxes)
    assert FC.artifact_cover_error(dict(cover='cover.jpg',cover_proof=proof),tmp_path) is None
    assert FC.cover_quality_error({**proof,'face_box':[500,200,1000,600]})


def test_upgrade_keeps_video_captions_copy_and_provenance(tmp_path):
    original=bundle(tmp_path)
    before={n:U.digest(tmp_path/n) for n in ['final.mp4','subtitles.ass']}
    report=U.upgrade(tmp_path,'ly-test',10,20,validate=lambda *a:None,font=font())
    updated=json.loads((tmp_path/'meta.json').read_text())
    assert report['changed'] and report['media_and_subtitles_unchanged']
    assert {n:U.digest(tmp_path/n) for n in before}==before
    assert {k:v for k,v in updated.items() if k not in ['cover_proof','cover_upgrade']}=={
        k:v for k,v in original.items() if k!='cover_proof'}
    assert updated['cover_proof']['cover_layout_version']==3
    assert updated['cover_proof']['headline_lines']==['长期投资','不是永远','不变']
    assert updated['cover_proof']['face_box'][2]-updated['cover_proof']['face_box'][0]==352
    assert updated['cover_upgrade']['portrait_source_resolution']==[228,236]
    assert FC.cover_quality_error(updated['cover_proof']) is None
    second=U.upgrade(tmp_path,'ly-test',10,20,validate=lambda *a:None)
    assert not second['changed']


@pytest.mark.parametrize('change',[
    dict(slug='ly-other'), dict(cover='../outside.jpg'),
    dict(fingerprints=dict(sha256='0'*64)),
    dict(cover_proof=dict(style='dark',face_box=[744,42,float('nan'),278]))])
def test_invalid_origin_or_files_fail_before_cover_changes(tmp_path,change):
    row=bundle(tmp_path);row.update(change)
    (tmp_path/'meta.json').write_text(json.dumps(row))
    cover_hash=U.digest(tmp_path/'cover.jpg')
    with pytest.raises(ValueError):
        U.upgrade(tmp_path,'ly-test',10,20,validate=lambda *a:None)
    assert U.digest(tmp_path/'cover.jpg')==cover_hash


def test_unrelated_failure_cannot_be_repaired_as_cover_only(tmp_path):
    bundle(tmp_path)
    before=U.digest(tmp_path/'cover.jpg')
    with pytest.raises(ValueError,match='Original delivery'):
        U.upgrade(tmp_path,'ly-test',10,20,validate=lambda *a:'Subtitle alignment failed')
    assert before==U.digest(tmp_path/'cover.jpg')


def test_verified_scene_is_never_relabelled_as_reference(tmp_path):
    row=bundle(tmp_path);row['cover_person_image_source']='verified_source_frame'
    (tmp_path/'meta.json').write_text(json.dumps(row))
    assert not U.upgrade(tmp_path,'ly-test',10,20,validate=lambda *a:None)['changed']
