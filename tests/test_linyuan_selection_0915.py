"""Replay actual source boundaries, model failures and attribution mistakes."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import produce_cn as p
from source_selection import select, boundary_error, OUTRO


def fixture(number):
    return json.loads((Path(__file__).parent / f'fixtures/linyuan_{number}_source_selection.json').read_text())


def test_real_891_opening_does_not_erase_later_questions():
    cues=fixture(891)['sections']['opening']
    assert not OUTRO.search('进入我们今天的访谈对话环节，有请林总和我们的投资人打声招呼吧。')
    picks=select(cues)
    assert [(x['start'],x['end']) for x in picks]==[(27,65)]
    assert p.editorial.range_seconds(cues,picks[0])==pytest.approx(142.64)


def test_real_891_multisentence_host_summary_is_removed():
    cues=fixture(891)['sections']['host_summary']
    pick=select(cues)[0]
    assert (pick['start'],pick['end'])==(0,35)
    assert p.editorial.range_seconds(cues,pick)==pytest.approx(122.08)
    assert '好的，林总也提到了' not in ''.join(c['text'] for c in cues[:pick['end']+1])


def test_real_891_new_investor_question_cannot_extend_short_leverage_answer():
    cues=fixture(891)['sections']['investor_question']
    # Old regex missed “也有投资者问到…林总说”, joining two independent questions.
    assert select(cues)==[]


def test_real_891_medical_answer_survives_without_host_summary():
    cues=fixture(891)['sections']['medical_answer']
    pick=select(cues)[0]
    assert (pick['start'],pick['end'])==(0,48)
    assert p.editorial.range_seconds(cues,pick)==pytest.approx(161.04)


def test_real_891_outro_cannot_make_a_short_answer_publishable():
    cues=fixture(891)['sections']['outro']
    assert select(cues,whole_source=True)==[]
    assert boundary_error(cues,dict(start=0,end=len(cues)-1))


def test_real_902_attribution_fails_before_cached_face_approval(tmp_path):
    data=fixture(902)
    transcript=''.join(c['text'] for c in data['cues'])
    assert '我们借这段话' in transcript and '我对林元这段话的理解' in transcript
    with patch.object(p,'_file_sha256',return_value=data['source_sha256']), \
            patch.object(p,'verified_source_evidence') as cache:
        report=p.run_source_quality_gate(Path('source.mp4'),tmp_path,'林园','')
        assert report['passed'] is False and report['retryable'] is False
        assert '第三方' in report['reason']
        cache.assert_not_called()
        with pytest.raises(p.VisualQualityError,match='第三方'):
            p.load_source_quality_report(Path('source.mp4'),tmp_path/'source_quality.json')
    assert '第三方' in p.editorial.metadata_error(dict(speaker='林园',source_sha256=data['source_sha256']))


def test_production_no_candidate_never_reenters_disabled_model_review(tmp_path,monkeypatch):
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST','true')
    cues=[dict(start=i*40,end=(i+1)*40,text='企业需要持续增长的需求。') for i in range(10)]
    # A mechanical chunk end is not a source ending. Repeating a model request
    # cannot create the missing source boundary; this is not a service outage.
    with patch.object(p,'llm',side_effect=AssertionError('disabled review called')), \
            patch.object(p,'pick_argument_context',side_effect=AssertionError('topic map called')):
        assert p.pick_highlights(cues,'林园','',tmp_path)==[]
        assert p.pick_highlights(cues,'林园','',tmp_path)==[]


def test_stale_positive_cache_does_not_preserve_bad_outro(tmp_path,monkeypatch):
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST','true')
    cues=fixture(891)['sections']['outro']
    (tmp_path/'highlights.json').write_text(json.dumps(dict(
        identity=dict(editorial=p.editorial.plan_identity(cues,p.TARGET_SEC),selector_version=15),
        picks=[dict(start=0,end=len(cues)-1,score=9)])))
    with patch.object(p,'llm',side_effect=AssertionError('model called')):
        assert p.pick_highlights(cues,'林园','',tmp_path)==[]


def test_short_original_speech_keeps_all_conditions_and_only_natural_source_end():
    cues=[dict(start=0,end=40,text='企业的长期增长取决于持续的需求。'),
          dict(start=40,end=95,text='如果需求下降，即使估值低也不能只看价格。'),
          dict(start=95,end=145,text='所以投资时应同时关注企业需求和投资风险。')]
    before=json.dumps(cues,ensure_ascii=False)
    assert select(cues)==[]
    assert [(p['start'],p['end']) for p in select(cues,whole_source=True)]==[(0,2)]
    assert json.dumps(cues,ensure_ascii=False)==before
    cues[-1]['text']='所以投资时应同时关注'
    assert select(cues,whole_source=True)==[]


def test_short_speech_cannot_use_next_question_for_duration():
    cues=[dict(start=0,end=110,text='企业的长期增长取决于持续的需求。'),
          dict(start=110,end=145,text='您对科技股怎么看？')]
    assert select(cues,whole_source=True)==[]


def test_actual_moving_wordmark_is_not_discarded_as_short_background_text():
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_0915_moving_wordmark.json').read_text())
    # These are actual OCR results in source coordinates and final-window
    # coordinates. The prior 8-character rule ignored both despite .929 OCR.
    for row in (data['source_ocr'],data['final_ocr']):
        bands=p.source_edge_text_exclusions([row])
        assert len(bands)==1 and bands[0][0]==0 and bands[0][2:]==(1,1)
        assert bands[0][1]<row['rect'][1]
        assert not p.source_edge_text_exclusions([{**row,'confidence':.3}])
        assert not p.source_edge_text_exclusions([{**row,'text':'企业产品说明'}])


def test_real_source_can_avoid_the_wordmark_without_losing_the_face_or_resolution():
    from live_tracking import crop_box
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_0915_moving_wordmark.json').read_text())
    bands=p.source_edge_text_exclusions([data['source_ocr']])
    for face in data['source_faces']:
        x,y,w,h=crop_box(face,*data['source_dimensions'],exclusions=data['source_marks']+bands)
        fx,fy,fw,fh=face
        assert w>=316 and h>=235
        assert x+8<=fx and fx+fw<=x+w-8 and y+max(8,fh*.22)<=fy and fy+fh<=y+h-2
        # Source slices are half-open: ending at the exclusion's first row
        # retains none of that row. The OCR box still has its two-pixel border.
        assert y+h<=bands[0][1]*data['source_dimensions'][1]


@pytest.mark.parametrize('digest',[
    '1bb0d11ecff2e072f19e2817d55daf4e0deede035167a1e16dc0737439d4ff05',
    'edf7eeeabc0ab08b8d1b38c2082fd5ad3ef9e713707324271f8ba4c820f13127'])
def test_manually_verified_dirty_files_cannot_reenter_publication(digest):
    assert '红色来源水印' in p.editorial.metadata_error(dict(fingerprints=dict(sha256=digest)))


def test_actual_799s_head_movement_keeps_shot_scale_without_entering_text_band():
    from live_tracking import shot_crop
    from stable_framing import StableFraming
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_0915_moving_wordmark.json').read_text())
    marks=data['source_marks']+p.source_edge_text_exclusions([data['source_ocr']])
    framing=StableFraming(30)
    shape=None
    for i,row in enumerate(data['motion_samples']):
        face=row['face']
        for repeat in range(15):
            box=shot_crop(framing,face,1280,620,i*15+repeat,marks)
            if shape is None:shape=box[2:]
            assert box[2:]==shape
            x,y,w,h=box;fx,fy,fw,fh=face
            assert w>=316 and h>=235
            assert x+8<=fx and fx+fw<=x+w-8 and y+fh*.22<=fy and fy+fh<=y+h-2
            assert all(not(x<c*1280 and x+w>a*1280 and y<d*620 and y+h>b*620)
                       for a,b,c,d in marks)


def test_actual_873s_frame_is_rejected_when_face_and_watermark_margins_conflict():
    from live_tracking import avoid_overlays
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_0915_moving_wordmark.json').read_text())
    # Run 34930079177, source frame 26210. This is a genuine quality rejection,
    # not an excuse to trim the chin or relax the watermark safety boundary.
    face=[377.7059631347656,299.3099670410156,146.926025390625,196.7473602294922]
    marks=data['source_marks']+p.source_edge_text_exclusions([data['source_ocr']])
    with pytest.raises(ValueError,match='完整人脸'):
        avoid_overlays((234,181,428,318),face,1280,620,marks)


def test_888_guest_rhetorical_questions_do_not_cut_off_the_continuing_answer():
    from source_selection import QUESTION
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_888_source_selection.json').read_text())
    cues=data['cues']
    for text in ('你没花都不是钱，都不是你的，没用，明白？',
                 '当然你们认为，他是靠人赚钱的那种人，是吧？',
                 '你如果投资方向没错的话呢，就管理自己。'):
        assert not QUESTION.search(text)
    picks=select(cues,limit=6,whole_source=True)
    assert [(p['start'],p['end']) for p in picks]==[(171,289)]
    assert cues[171]['start']==459.0 and cues[289]['end']==747.24
    # The bearish-market preamble at 566s keeps its actual reply from 582s;
    # the next host turn about investment books at 747s stays out.
    assert '凡是认为这个牛市' in ''.join(c['text'] for c in cues[171:290])


def test_888_displayed_ass_keeps_every_source_question_in_the_edit_proof(tmp_path,monkeypatch):
    from caption_readability import clean_entries,display_payload_text
    from presentation import layout_for
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_888_caption_entries.json').read_text())
    entries=data['raw_entries']
    cleaned,proof=clean_entries(entries)
    layout=layout_for(1742,972,False)
    cache=tmp_path/'semantic-1.json'
    cache.with_name('semantic-1.readable-2026091301.json').write_text(json.dumps(data['cached_groups']))
    monkeypatch.setattr(p,'llm',lambda *a,**kw:pytest.fail('Validated source boundaries must be reusable'))
    captions=p.semantic_caption_entries(entries,'',layout,cache)
    ass=tmp_path/'subtitles.ass'
    p.make_ass(captions,ass,1742,972)
    actual=p.editorial.subtitle_files_text(tmp_path,[ass.name])
    assert display_payload_text(actual)==display_payload_text(proof['display_text'])
    assert '是不是？' in actual


def test_888_complete_short_reply_does_not_send_the_entire_clip_back_to_a_model(tmp_path,monkeypatch):
    from caption_readability import clean_entries,display_payload_text
    from presentation import layout_for
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_888_source_selection.json').read_text())
    entries=[dict(start_sec=c['start']-459,end_sec=c['end']-459,zh=c['text'],en='')
             for c in data['cues'][171:290]]
    cleaned,proof=clean_entries(entries)
    monkeypatch.setattr(p,'llm',lambda *a,**kw:pytest.fail('A source-punctuated reply needs no model retry'))
    captions=p.semantic_caption_entries(entries,'',layout_for(1742,972,False),tmp_path/'semantic.json')
    assert display_payload_text(''.join(c['zh'] for c in captions))==display_payload_text(proof['display_text'])
    assert captions[-1]['end_sec']==pytest.approx(p.caption_timeline(cleaned)[0][-1][2])
    assert p.unfinished_caption_tail('是')
    assert not p.caption_affirmation_ends([dict(zh='问题是。')])
    assert not p.caption_affirmation_ends([dict(zh='哎，是')])


def test_measured_888_news_panel_crop_is_scoped_to_the_reviewed_source_interval():
    report=dict(source_sha256='6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d')
    crop=p.reviewed_native_cleanup(report,1920,1080,459,747.24)
    w,h,x,y=crop
    assert y+h<789 and min(w,h)>=720 and h>=1080*.7
    from presentation import layout_for
    assert layout_for(1742,1080,False)['subtitle_region']['y']>=h
    assert p.reviewed_native_cleanup({},1920,1080,459,747.24) is None
    assert p.reviewed_native_cleanup(report,1920,1080,1302.44,1428.52) is None
    assert p.reviewed_native_cleanup(report,1280,720,459,747.24) is None
