"""Restore large side-by-side subjects without silently weakening old receipts."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'linyuan'))
import editorial_cover as C
import presentation as V
from fc import index as FC
from artifact_range import delivery_files
from batch_delivery import archive_accepted


def font():
    try:
        return C.font_path()
    except ValueError:
        pytest.skip('Chinese font required')


def test_actual_oct9_face_restores_oct6_scale_and_full_reviewed_copy(tmp_path):
    source=Image.new('RGB',(720,1280),'#b98768')
    proof=C.render(source,tmp_path/'cover.jpg',[504,416,105,143],
                   '别只看股价要看财务健全','林园',font())
    assert proof['design_version']==3
    assert proof['portrait_panel']==[832,68,1152,652]
    assert proof['face_box'][2]-proof['face_box'][0] >= 190
    assert proof['font_px']==112
    assert ''.join(proof['headline_lines'])=='别只看股价要看财务健全'
    assert min(b[0] for b in proof['text_boxes'])==176
    assert Image.open(tmp_path/proof['feed_preview']).size==(480,360)
    assert FC.cover_quality_error(proof) is None
    assert FC.artifact_cover_error(dict(cover='cover.jpg',cover_proof=proof),tmp_path) is None
    # The feed image is an inspected surface, not an optional decorator.
    Image.new('RGB',(480,360)).save(tmp_path/proof['feed_preview'])
    assert '指纹' in FC.artifact_cover_error(dict(cover='cover.jpg',cover_proof=proof),tmp_path)


@pytest.mark.parametrize('title',['科技热是工业化行业的宏观现象',
    '底部区间分期买入拉低建仓价','人均收入不减少十二三年投资回本'])
def test_dark_cover_keeps_whole_portrait_and_large_type_without_overlap(tmp_path,title):
    reference=tmp_path/'reference.jpg'
    Image.new('RGB',(320,440),'#b98768').save(reference)
    image,lines,size,boxes,face=V.dark_cover(reference,title,'林园',font(),C.font_face_index(font()))
    cover=tmp_path/'cover.jpg'
    image.save(cover)
    proof=V.cover_proof(image,cover,lines,size,boxes,style='dark',face_box=face,feed_crop=V.FEED_WIDE_CROP)
    assert ''.join(lines)==title
    assert size==112
    assert FC.cover_quality_error(proof) is None
    assert all(b[2]<=face[0] for b in boxes)


def test_wide_contract_does_not_accept_fake_dimensions_small_type_or_cut_face(tmp_path):
    image=Image.new('RGB',(1280,720))
    image.save(tmp_path/'cover.jpg')
    proof=V.cover_proof(image,tmp_path/'cover.jpg',['长期持有'],112,
        [[176,280,624,392]],style='dark',face_box=[800,100,1080,600],feed_crop=V.FEED_WIDE_CROP)
    for change in [dict(feed_safe_crop=[100,0,1180,720]),dict(feed_aspect_ratio='1:1'),
                   dict(cover_layout_version=99),dict(font_px=96),dict(face_box=[900,80,1200,680]),
                   dict(face_box=[500,280,1080,600]),dict(feed_preview_sha256='')]:
        assert FC.cover_quality_error({**proof,**change})
    legacy=V.cover_proof(image,tmp_path/'old.jpg',['长期持有'],96,
        [[312,404,696,496]],style='dark',face_box=[744,34,972,282])
    assert legacy['feed_safe_crop']==[280,0,1000,720]
    assert FC.cover_quality_error(legacy) is None


def test_wide_feed_companion_is_required_in_both_delivery_paths(tmp_path):
    meta=dict(final='final.mp4',cover='cover.jpg',preview_30s='preview.mp4',contact_sheet_6='sheet.jpg',
              subtitle_files=[],cover_proof=dict(feed_safe_crop=list(V.FEED_WIDE_CROP),feed_preview='cover_feed_4_3.jpg'))
    (tmp_path/'meta.json').write_text(json.dumps(meta))
    (tmp_path/'batch_report.json').write_text('{}')
    for name in ['final.mp4','cover.jpg','preview.mp4','sheet.jpg','cover_feed_4_3.jpg']:
        (tmp_path/name).write_bytes(b'original')
    assert 'cover_feed_4_3.jpg' in delivery_files(meta)
    archive_accepted(tmp_path,'ly-test')
    assert (tmp_path/'_accepted'/'cover_feed_4_3.jpg').read_bytes()==b'original'
    assert (tmp_path/'_accepted'/'ly-test.cover_feed_4_3.jpg').read_bytes()==b'original'
    for name in ['../outside.jpg','/outside.jpg','']:
        bad=copy.deepcopy(meta)
        bad['cover_proof']['feed_preview']=name
        with pytest.raises(ValueError):delivery_files(bad)


def test_native_single_line_captions_are_larger_without_changing_aspect():
    assert V.layout_for(720,1280)['subtitle_font_px']>=56
    assert V.layout_for(1920,1080)['subtitle_font_px']>=93
    for width,height in [(720,1280),(1920,1080)]:
        layout=V.layout_for(width,height)
        assert layout['canvas']==dict(width=width,height=height)
        assert layout['subtitle_max_lines']==1
        assert layout['subtitle_style']=='white-outline'
