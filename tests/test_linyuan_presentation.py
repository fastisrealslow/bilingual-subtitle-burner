"""Shared rendering rules; no network, source credentials, or publishing."""
import copy
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import presentation as V
import produce_cn as P


def test_real_face_beats_rightmost_cloth_and_background_patterns():
    faces=[(1338,268,65,65),(774,225,342,342),(1088,786,71,71)]
    assert P.select_interview_face(faces,1920,1080)==(774,225,342,342)
    assert P.select_interview_face([(359,277,73,73),(783,216,308,308)],1920,1080)==(783,216,308,308)


def test_comparable_split_screen_guest_stays_on_right():
    assert P.select_interview_face([(250,220,300,300),(1200,240,290,290)],1920,1080)==(1200,240,290,290)
    assert P.select_interview_face([(1088,786,71,71)],1920,1080) is None


def test_measured_crop_is_bound_to_source_bytes_and_dimensions():
    report={'source_sha256':'9dc2b7c6f82570984a52ccdff5c4a41a7595c0a129b1919df81d7539a266a345'}
    assert P.reviewed_source_live_crop(report,1280,720).startswith('crop=632:470:646:128,')
    assert P.reviewed_source_live_crop(report,1920,1080) is None
    assert P.reviewed_source_live_crop({'source_sha256':'unseen'},1280,720) is None


def test_encoded_qr_inside_card_source_window_is_rejected(tmp_path):
    import cv2
    import numpy as np
    frame=np.full((1280,720,3),238,dtype=np.uint8)
    code=cv2.QRCodeEncoder_create().encode('actual-source-qr')
    code=cv2.resize(code,(200,200),interpolation=cv2.INTER_NEAREST)
    frame[460:660,220:420]=cv2.cvtColor(code,cv2.COLOR_GRAY2BGR)
    video=tmp_path/'card-qr.mp4'
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),12,(720,1280))
    for _ in range(12):writer.write(frame)
    writer.release()
    with pytest.raises(ValueError,match='二维码'):
        V.verify_render(video,V.layout_for(720,1280,True))
    with pytest.raises(P.VisualQualityError,match='二维码'):
        P.verify_live_region_after_render(video)

spec = importlib.util.spec_from_file_location('presentation_fc', ROOT / 'linyuan/fc/index.py')
FC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FC)

def test_shared_rules_are_active_in_both_production_consumers():
    assert P.PRESENTATION_RULES_VERSION == V.VERSION == 2
    assert callable(FC.presentation_quality_error)


@pytest.mark.parametrize('w,h,mode', [(1280,720,'landscape'), (720,1280,'portrait'),
                                    (720,720,'square'), (1640,720,'landscape')])
def test_native_aspect_and_center(w,h,mode):
    layout = V.layout_for(w,h)
    assert layout['mode'] == mode
    assert layout['canvas'] == {'width':w,'height':h}
    r = layout['subtitle_region']
    assert r['y'] + r['height'] <= h
    assert layout['subtitle_vertical_alignment'] == 'center'


@pytest.mark.parametrize('word', V.PROTECTED + ('2017年10月27日','100倍','3.5亿元'))
def test_semantic_tokens_never_split(word):
    text='我说'+word+'要完整'
    for a,b in V.word_spans(text):
        assert not 2 < b < 2+len(word)


@pytest.mark.parametrize('w,h,card', [(1280,720,False),(720,1280,False),
                                    (720,720,False),(720,1280,True)])
def test_asr_cross_screen_entity_reconnected(w,h,card,tmp_path):
    entries=[{'start_sec':0,'end_sec':3.84,'zh':'作为一个长期持有茅台的价值投资者，飙升的茅'},
             {'start_sec':3.84,'end_sec':6.12,'zh':'台股价对它又有怎样的影响呢？'}]
    path=tmp_path/'captions.ass'
    cues=V.write_ass(entries,path,V.layout_for(w,h,card),'Noto Sans CJK SC')
    assert ''.join(e['zh'] for e in cues)==''.join(e['zh'] for e in entries)
    lines=[line for e in cues for line in e['lines']]
    assert sum(line.count('茅台') for line in lines)==2
    assert not any(line.endswith('茅') or line.startswith('台') for line in lines)
    assert all(len(e['lines'])<=2 for e in cues)
    assert all(a['end_sec']<=b['start_sec'] for a,b in zip(cues,cues[1:]))
    assert '\\an5\\pos(' in path.read_text(encoding='utf-8-sig')


def test_source_timing_gaps_preserved():
    cues=V.prepare_captions([{'start_sec':0,'end_sec':2,'zh':'长期投资'},
                            {'start_sec':5,'end_sec':7,'zh':'不要追涨'}],V.layout_for(1280,720))
    assert [(x['start_sec'],x['end_sec']) for x in cues]==[(0,2),(5,7)]


@pytest.mark.parametrize('text,expected', [('供需关系，','供需关系'),('长期持有。','长期持有'),
    ('为什么？','为什么？'),('不要追涨！','不要追涨！'),('收益3.5%。','收益3.5%'),
    ('他说“长期持有。”','他说“长期持有”')])
def test_caption_terminal_punctuation_display_only(tmp_path,text,expected):
    path=tmp_path/'terminal.ass'
    cues=V.write_ass([dict(start_sec=0,end_sec=3,zh=text)],path,V.layout_for(1280,720),'Noto Sans CJK SC')
    assert cues[0]['zh']==text
    assert cues[0]['display_lines']==[expected]
    assert path.read_text(encoding='utf-8-sig').splitlines()[-1].endswith(expected)


def test_larger_fonts_keep_two_line_room():
    for w,h,card in [(1280,720,False),(1640,620,False),(480,620,False),(720,1280,True)]:
        layout=V.layout_for(w,h,card)
        assert layout['subtitle_region']['height']>=2*layout['subtitle_font_px']
    assert V.layout_for(720,1280,True)['subtitle_font_px']==44
    assert V.layout_for(1640,620)['subtitle_font_px']>=44
    from caption_readability import ass_font_size
    assert ass_font_size(44,'Noto Sans CJK SC')==64


def test_complete_date_fits_narrow_frame_without_splitting():
    text='2017年10月27日，茅台的股价攀升6%'
    cues=V.prepare_captions([dict(start_sec=0,end_sec=6,zh=text)],V.layout_for(480,620))
    assert ''.join(x['zh'] for x in cues)==text
    assert any('2017年10月27日' in line for cue in cues for line in cue['lines'])
    assert all(28<=cue['font_px']<=34 for cue in cues)


def test_question_ending_is_not_left_on_a_flashing_screen():
    text='价值投资者，飙升的茅台股价对它又有怎样的影响呢？'
    cues=V.prepare_captions([dict(start_sec=0,end_sec=4,zh=text)],V.layout_for(720,1280,True))
    assert ''.join(cue['zh'] for cue in cues)==text
    assert all(cue['end_sec']-cue['start_sec']>=.8 for cue in cues)
    assert cues[-1]['zh'].endswith('呢？') and len(cues[-1]['zh'])>=4


def test_cover_styles_preserve_scene_and_offer_safe_choice():
    assert {V.select_cover_style(True,str(n)) for n in range(30)}=={'scene','photo','light','dark'}
    assert V.select_cover_style(True,'访谈主题','photo')=='photo'
    assert {V.select_cover_style(False,str(n)) for n in range(20)}=={'light','dark'}
    assert V.select_cover_style(True,'访谈主题','dark')=='dark'
    with pytest.raises(ValueError):
        V.select_cover_style(False,'不合格原画','photo')


def test_encoded_qr_is_still_rejected(tmp_path):
    import cv2
    import numpy as np
    code=cv2.QRCodeEncoder_create().encode('presentation-regression')
    code=cv2.resize(code,(240,240),interpolation=cv2.INTER_NEAREST)
    frame=np.full((480,640,3),255,dtype=np.uint8)
    frame[120:360,200:440]=cv2.cvtColor(code,cv2.COLOR_GRAY2BGR)
    path=tmp_path/'qr.avi'
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),12,(640,480))
    assert writer.isOpened()
    for _ in range(12): writer.write(frame)
    writer.release()
    with pytest.raises(ValueError,match='二维码'):
        V.verify_render(path,V.layout_for(640,480))


def test_damaged_undecodable_qr_keeps_surviving_finder_gate():
    import cv2
    import numpy as np
    code=cv2.QRCodeEncoder_create().encode('damaged-qr-regression')
    # Remove quiet border and destroy payload while retaining finder corners.
    ink=np.argwhere(code==0)
    lo=ink.min(axis=0);hi=ink.max(axis=0)+1
    code=code[lo[0]:hi[0],lo[1]:hi[1]]
    code=cv2.resize(code,(224,224),interpolation=cv2.INTER_NEAREST)
    code[75:190,75:190]=127
    points=np.array([[[0,0],[223,0],[223,223],[0,223]]],dtype=np.float32)
    assert V.qr_candidate_has_finders(code,points)
    assert not V.qr_candidate_has_finders(np.full((224,224),127,dtype=np.uint8),points)


def test_tiny_spoken_filler_merges_without_losing_text_or_faking_time():
    entries=[dict(start_sec=0,end_sec=2,zh='我们已经看到了曙光'),
             dict(start_sec=2,end_sec=2.1,zh='啊')]
    groups=P.apply_semantic_groups(entries,['我们已经看到了曙光','啊'],12,48)
    assert len(groups)==1
    assert groups[0]['zh']=='我们已经看到了曙光啊'
    assert groups[0]['end_sec']==2.1


def test_observed_half_second_caption_does_not_flash_alone():
    entries=[dict(start_sec=0,end_sec=5.4,zh='肾透析透析的那个地方人满得很'),
             dict(start_sec=5.56,end_sec=6.17,zh='要给你排')]
    groups=P.apply_semantic_groups(entries,[e['zh'] for e in entries],12,48)
    assert len(groups)==1 and groups[0]['end_sec']==6.17
    assert groups[0]['zh']==''.join(e['zh'] for e in entries)


def test_observed_jiushi_boundary_is_joined_without_changing_words():
    groups=P.repair_semantic_boundaries(['他们也是春天来了就是','因为老龄化'])
    assert groups==['他们也是春天来了就是因为老龄化']


@pytest.mark.parametrize('tail',['最后我相信是中国人会','目前市场虽然','买入一个'])
def test_real_incomplete_tails_do_not_hide_inside_dictionary_tokens(tail):
    assert P.unfinished_caption_tail(tail)


def test_reviewed_subtitle_boundaries_still_reject_text_changes(tmp_path):
    entries=[dict(start_sec=0,end_sec=4,zh='最后我相信中国人会在这个领域独大')]
    with pytest.raises(ValueError,match='改写或丢失'):
        P.semantic_caption_entries(entries,None,V.layout_for(720,1280,True),tmp_path/'proof.json',
                                   reviewed_groups=['最后我相信中国人不会在这个领域独大'])


def test_reviewed_subtitles_keep_original_punctuation_boundary(tmp_path):
    entries=[
        dict(start_sec=0,end_sec=2,zh='我们明眼人一看就知道不会错。'),
        dict(start_sec=2,end_sec=4,zh='钱怎么来的还得怎么回去。'),
    ]
    result=P.semantic_caption_entries(
        entries,None,V.layout_for(720,1280,True),tmp_path/'proof.json',
        reviewed_groups=['我们明眼人一看就知道不会错','钱怎么来的还得怎么回去'])
    assert ''.join(row['zh'] for row in result)=='我们明眼人一看就知道不会错钱怎么来的还得怎么回去'


def test_zero_area_qr_false_candidate_is_not_decoded(tmp_path,monkeypatch):
    import cv2
    import numpy as np
    path=tmp_path/'plain.avi'
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),12,(640,480))
    assert writer.isOpened()
    for _ in range(12): writer.write(np.full((480,640,3),120,dtype=np.uint8))
    writer.release()
    class DegenerateDetector:
        def detect(self, frame):
            return True,np.array([[[10,10],[20,20],[30,30],[40,40]]],dtype=np.float32)
        def decode(self,*args):
            raise AssertionError('zero-area false candidate must not reach decoder')
    monkeypatch.setattr(cv2,'QRCodeDetector',DegenerateDetector)
    assert V.verify_render(path,V.layout_for(640,480))['no_qr_verified']


def test_zero_length_cue_rejected():
    with pytest.raises(ValueError):
        V.prepare_captions([{'start_sec':1,'end_sec':1,'zh':'茅台'}],V.layout_for(720,1280))


def test_thumbnail_headline_is_short_without_splitting():
    assert V.cover_headline('林园：电视机整天在降价，酒在涨价，两个相反的方向') == ['电视机整天在降价','酒在涨价']
    lines=V.cover_headline('林园：作为一个长期持有茅台的价值投资者，谈谈未来')
    assert max(map(len,lines))<=9
    assert not any(x.endswith('茅') for x in lines)


def valid_meta(w=1280,h=720,card=False):
    return {'presentation_version':1,'resolution':{'width':w,'height':h},
            'layout_proof':V.layout_for(w,h,card),'render_mode':'audio_card' if card else 'direct',
            'subtitle_word_boundaries_verified':True,
            'render_checks':{'frames_checked':12,'dimensions_match':True,'qr_detected':False},
            'cover_proof':{'font_px':100,'thumbnail_font_px':12.5,'headline_lines':['测试大字'],
                           'no_overflow':True,'thumbnail':'cover_list_160.jpg'}}


@pytest.mark.parametrize('w,h,card',[(1280,720,False),(720,1280,False),(720,720,False),(720,1280,True)])
def test_fc_accepts_actual_layout(w,h,card):
    assert FC.presentation_quality_error(valid_meta(w,h,card)) is None


@pytest.mark.parametrize('mutation',[
    lambda m:m['resolution'].update(width=720),
    lambda m:m['layout_proof'].update(mode='unknown'),
    lambda m:m['layout_proof']['subtitle_region'].update(y=9000),
    lambda m:m['layout_proof'].update(subtitle_max_lines=3),
    lambda m:m['cover_proof'].update(font_px=50),
    lambda m:m['render_checks'].update(frames_checked=0),
    lambda m:m['render_checks'].update(qr_detected=True),
    lambda m:m.update(subtitle_word_boundaries_verified=False),
])
def test_fc_rejects_incomplete_evidence(mutation):
    meta=copy.deepcopy(valid_meta())
    mutation(meta)
    assert FC.presentation_quality_error(meta)


def test_portrait_card_cannot_use_placeholder(tmp_path):
    with pytest.raises(P.VisualQualityError):
        P.make_audio_card(tmp_path/'card.png','林园','投资要看供需关系',
                          width=720,height=1280,require_portrait=True)


def test_long_asr_cue_does_not_end_screen_inside_intent_group():
    entries=[{'start_sec':0,'end_sec':3.42,'zh':'守住现金流的企业，大家还更'},
             {'start_sec':3.42,'end_sec':5.34,'zh':'愿意买，相反那些巨额投入的'},
             {'start_sec':5.4,'end_sec':6.3,'zh':'大家会有些担忧'}]
    cues=V.prepare_captions(entries,V.layout_for(720,1280,True))
    assert ''.join(c['zh'] for c in cues)==''.join(e['zh'] for e in entries)
    assert any('大家还更愿意买' in c['zh'] for c in cues)
    assert not any(c['zh'].endswith(('还更','投入的')) for c in cues)


def test_semantic_group_model_cannot_change_original_words():
    entries=[dict(start_sec=0,end_sec=3,zh='大家还更'),dict(start_sec=3,end_sec=5,zh='愿意买。')]
    groups=P.apply_semantic_groups(entries,['大家还更愿意买。'],12)
    assert groups[0]['start_sec']==0 and groups[0]['semantic_group'] is True
    assert len(V.prepare_captions(groups,V.layout_for(720,1280,True)))==1
    with pytest.raises(ValueError,match='改写或丢失'):
        P.apply_semantic_groups(entries,['大家不愿意买。'],12)
    with pytest.raises(ValueError):
        P.apply_semantic_groups(entries,['大家还更','愿意买。'],12)


def test_complete_semantic_group_uses_bounded_per_cue_font_before_rejecting():
    # The protected/balanced word boundaries need 13 characters on one line,
    # although the complete clause is still within the nominal 24 characters.
    text = '您平均持有一家企业能在几年的时间呢我们的变化挺大'
    entries = [dict(start_sec=0, end_sec=5, zh=text)]
    groups = P.apply_semantic_groups(entries, [text], 12, font_px=48)
    assert groups[0]['line_capacity'] == 13
    assert 38 <= groups[0]['font_px'] < 48
    prepared = V.prepare_captions(groups, V.layout_for(720,1280,True))
    assert ''.join(prepared[0]['lines']) == text
    assert prepared[0]['font_px'] == groups[0]['font_px']


def test_semantic_group_still_rejects_beyond_38px_two_line_capacity():
    text = '守住现金流的企业大家还更愿意买' * 3
    entries = [dict(start_sec=0, end_sec=5, zh=text)]
    with pytest.raises(ValueError, match='无法放入两行'):
        P.apply_semantic_groups(entries, [text], 12, font_px=48)


def test_long_model_group_rebalances_only_at_original_cue_boundaries():
    entries = [
        dict(start_sec=0, end_sec=3, zh='我们买入贵州茅台'),
        dict(start_sec=9, end_sec=12, zh='以后就长期持有'),
    ]
    groups = P.apply_semantic_groups(
        entries, ['我们买入贵州茅台以后就长期持有'], 12, font_px=48)
    assert [g['zh'] for g in groups] == ['我们买入贵州茅台', '以后就长期持有']
    assert groups[0]['end_sec'] == 3
    assert groups[1]['start_sec'] == 9


def test_long_group_does_not_split_at_incomplete_source_cue():
    entries = [
        dict(start_sec=0, end_sec=4, zh='我们因为'),
        dict(start_sec=10, end_sec=14, zh='看好医药所以买入'),
    ]
    with pytest.raises(ValueError, match='无法安全重分'):
        P.apply_semantic_groups(
            entries, ['我们因为看好医药所以买入'], 12, font_px=48)


def test_long_group_can_use_real_punctuation_inside_an_asr_cue():
    entries = [
        dict(start_sec=0, end_sec=4, zh='快速消费是与'),
        dict(start_sec=4, end_sec=10,
             zh='嘴巴有关的与生命有关的，这些企业在慢慢增长'),
    ]
    groups = P.apply_semantic_groups(
        entries, ['快速消费是与嘴巴有关的与生命有关的这些企业在慢慢增长'],
        12, font_px=48)
    assert [g['zh'] for g in groups] == [
        '快速消费是与嘴巴有关的与生命有关的', '这些企业在慢慢增长']


def test_semantic_caption_repairs_oversized_parent_with_focused_model_call(
        tmp_path, monkeypatch):
    monkeypatch.setattr(P,'source_caption_groups',lambda *a: (_ for _ in ()).throw(ValueError('Exercise model fallback')))
    entries = [
        dict(start_sec=0, end_sec=2, zh='我们长期持有优秀企业'),
        dict(start_sec=2, end_sec=4, zh='因为现金流能够持续增长'),
        dict(start_sec=4, end_sec=6, zh='所以不会因为波动卖出'),
    ]
    transcript = ''.join(e['zh'] for e in entries)
    calls = []

    def fake_llm(messages, *_args, **_kwargs):
        calls.append(messages[0]['content'])
        if len(calls) == 1:
            return P.json.dumps({'break_after_tokens': [13]})
        return P.json.dumps({'break_after_tokens': [4, 8, 13]})

    monkeypatch.setattr(P, 'llm', fake_llm)
    layout = V.layout_for(720, 1280, True)
    groups = P.semantic_caption_entries(
        entries, 'test-key', layout, tmp_path / 'semantic.json')
    assert len(calls) == 2
    assert '只修复下面这一个过长' in calls[1]
    assert ''.join(g['zh'] for g in groups) == transcript
    assert len(groups) >= 2
    assert all(len(g['zh']) <= 30 for g in groups)
    assert all(g['end_sec'] - g['start_sec'] <= 8 for g in groups)


def test_semantic_repair_removes_bad_boundary_without_changing_source():
    texts=['大家还更','愿意买','守住现金流','的企业']
    repaired=P.repair_semantic_boundaries(texts)
    assert repaired==['大家还更愿意买','守住现金流的企业']
    assert ''.join(repaired)==''.join(texts)
    with pytest.raises(ValueError, match='未完成'):
        P.apply_semantic_groups([dict(start_sec=0,end_sec=3,zh='如果')],
                               P.repair_semantic_boundaries(['如果']),12)


def test_model_selects_whole_word_ids_instead_of_inventing_character_offsets():
    tokens=[dict(id=1,end=2,text='投资'),dict(id=2,end=4,text='茅台'),dict(id=3,end=7,text='100股')]
    assert P.token_breaks_to_char_offsets([2,3],tokens)==[4,7]
    for invalid in ([0,3],[3,2],[1,1,3],[1,4],[2],['2',3],[True,3]):
        with pytest.raises(ValueError):P.token_breaks_to_char_offsets(invalid,tokens)


@pytest.mark.parametrize('text',['珍惜现在的机会','这就是现代社会','人的寿命越来越长','地球的资源有限','得到真正的回报'])
def test_complete_words_are_not_mistaken_for_grammatical_fragments(text):
    result=P.apply_semantic_groups([dict(start_sec=0,end_sec=4,zh=text)],[text],12)
    assert ''.join(x['zh'] for x in result)==text


@pytest.mark.parametrize('text',['因为','大家还更','我们愿意','这时他们会'])
def test_real_unfinished_function_words_still_fail(text):
    with pytest.raises(ValueError,match='未完成'):
        P.apply_semantic_groups([dict(start_sec=0,end_sec=3,zh=text)],[text],12)


def test_scene_cover_keeps_pixels_without_headline_and_checks_identity(tmp_path):
    from PIL import Image
    import numpy as np
    import hashlib
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
    import index as fc
    # A colored scene has no logo, black bands or QR. No artificial panel/text is added.
    image=Image.new('RGB',(1280,720),(128,172,151))
    identity=dict(engine='opencv_yunet_sface_cpu',cosine_score=.8,threshold=.363,sharpness=100)
    path=tmp_path/'scene.jpg'
    proof=V.save_scene_cover(image,path,(350,150,240,280),identity)
    assert proof['headline_lines']==[] and proof['font_px']==0
    assert np.abs(np.asarray(Image.open(path)).astype(float)-np.asarray(image)).mean()<2
    assert proof['sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert fc.cover_quality_error(proof) is None
    meta=dict(cover='scene.jpg',cover_proof=proof)
    assert fc.artifact_cover_error(meta,tmp_path) is None
    path.write_bytes(b'wrong-cover')
    assert fc.artifact_cover_error(meta,tmp_path)
    with pytest.raises(ValueError,match='清晰度'):
        V.save_scene_cover(image,path,(350,150,240,280),{**identity,'sharpness':10})
    with pytest.raises(ValueError,match='横向'):
        V.save_scene_cover(Image.new('RGB',(720,1280)),path,(150,200,200,250),identity)
    with pytest.raises(ValueError):
        V.select_cover_style(False,'脏原画','scene')
    assert V.select_cover_style(True,'访谈','scene')=='scene'
