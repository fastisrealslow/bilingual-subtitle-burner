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

spec = importlib.util.spec_from_file_location('presentation_fc', ROOT / 'linyuan/fc/index.py')
FC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FC)

def test_shared_rules_are_active_in_both_production_consumers():
    assert P.PRESENTATION_RULES_VERSION == V.VERSION == 1
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
