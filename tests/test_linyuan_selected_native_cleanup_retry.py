from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import produce_cn as P

SOURCE='6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d'


def test_automatic_source_layout_is_only_a_bounded_proposal_after_selected_rejection(monkeypatch):
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true')
    report=dict(source_sha256=SOURCE)
    assert P.reviewed_native_cleanup(report,1920,1080,254.76,281.72) is None
    crop=P.reviewed_native_cleanup(report,1920,1080,254.76,281.72,automatic_proposal=True)
    assert crop==(1920,756,0,0) and crop[1]>=1080*.7
    for r,w,h,a,b in [({},1920,1080,254,282),(report,1280,720,254,282),
                      (report,1920,1080,-1,282),(report,1920,1080,282,254)]:
        assert P.reviewed_native_cleanup(r,w,h,a,b,automatic_proposal=True) is None


def test_real_runtime_quality_exception_reaches_one_measured_retry_and_cannot_bypass_failure(monkeypatch,tmp_path):
    import source_geometry
    import presentation
    import stage_context
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY','true')
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE','baseline')
    monkeypatch.setattr(P,'argument_record_for_render',lambda *a:{})
    monkeypatch.setattr(P,'_download_speaker_reference',lambda *a:None)
    monkeypatch.setattr(P,'_local_face_models',lambda:None)
    monkeypatch.setattr(stage_context,'plan',lambda *a,**k:None)
    monkeypatch.setattr(P.subprocess,'run',lambda *a,**k:None)
    monkeypatch.setattr(source_geometry,'refine_native_crop',lambda src,vf,w,h,*a,**k:(vf,w,h,{}))
    monkeypatch.setattr(source_geometry,'landscape_crop_plan',lambda *a,**k:(None,{}))
    monkeypatch.setattr(presentation,'verify_render',lambda *a,**k:None)
    monkeypatch.setattr(P,'has_existing_subtitles',lambda *a,**k:True)
    retries=[]
    def verify_proposal(*args,**kwargs):
        retries.append((args,kwargs))
        assert kwargs['proposed_crop']==(1920,756,0,0)
        assert kwargs['caption_footer'] is True
        assert kwargs['proposed_logos']==[(.9,52/1080,.95,148/1080)]
        return None  # A source-bound proposal is never approval.
    monkeypatch.setattr(P,'selected_native_clean_plan',verify_proposal)
    monkeypatch.setattr(P,'copywrite',lambda *a,**k:pytest.fail('Bad pictures reached title generation'))
    with pytest.raises(P.VisualQualityError,match='重新取景未通过'):
        P._produce_one(tmp_path/'source.mp4',tmp_path,tmp_path,
            [dict(start=254.76,end=281.72,text='完整原始问答')],
            '林园','','',False,1920,1080,'',preselected_picks=[dict(start=0,end=0)],
            source_report=dict(source_sha256=SOURCE,clean_strategy='crop',
                clean_video_filter='crop=1920:972:0:0',
                clean_output_resolution=dict(width=1920,height=972)),require_live_video=True)
    assert len(retries)==1
    assert not list(tmp_path.glob('final*.mp4'))


def test_single_line_footer_preserves_native_picture_without_large_white_canvas(tmp_path):
    import presentation as V
    from produce_cn import make_ass
    layout=V.footer_layout_for(1744,756,single_line=True)
    legacy=V.footer_layout_for(1744,756)
    assert layout['canvas']['height']<legacy['canvas']['height']
    assert layout['source_region']==dict(x=0,y=0,width=1744,height=756)
    assert layout['source_region']['height']/layout['canvas']['height']>.82
    assert layout['subtitle_region']['y']>=756
    assert layout['subtitle_region']['height']>=layout['subtitle_font_px']*1.448
    assert layout['subtitle_font_px']>=70
    assert 'color=0x101c2b' in V.footer_filter(layout)
    p=tmp_path/'single-line.ass'
    entries=[dict(start_sec=0,end_sec=4,zh='我们还是继续持有茅台',en='')]
    screens=make_ass(entries,p,1744,756,layout_override=layout)
    assert all(len(s['lines'])==1 for s in screens)
    assert f"PlayResY: {layout['canvas']['height']}" in p.read_text(encoding='utf-8-sig')
