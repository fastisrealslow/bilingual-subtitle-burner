import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import caption_lines as C
import presentation as V
import produce_cn as P
import caption_readability as R


def test_word_clock_survives_asr_merge_cleaning_and_excerpt_shift():
    cues=P._funasr_tokens_to_cues(['我们','我们','买入','，','贵州茅台','。'],
        [10,10.3,11,12,14,15],0,16,end_timestamps=[10.3,10.6,12,12,15,15])
    entries=[P.cue_caption_entry(c,10) for c in P._merge_cues(cues)]
    cleaned,proof=R.clean_entries(entries)
    assert ''.join(c['zh'] for c in cleaned)=='我们买入，贵州茅台。'
    anchors=[a for c in cleaned for a in c['caption_chars']]
    assert anchors[0][1]==pytest.approx(.3)
    assert next(a[1] for a in anchors if a[0]=='贵')==4
    assert R.replay_edit_proof(proof)==(proof['raw_text'],proof['display_text'])
    screens=C.one_line_screens(cleaned,V.layout_for(720,1280))
    assert [a for c in screens for a in c['caption_chars']]==anchors


def test_single_line_split_preserves_nonuniform_word_times_and_silence():
    entries=[dict(start_sec=0,end_sec=7,zh='政策力度很大股市会被推起来',
        caption_chars=[(c,i*.2,(i+1)*.2) for i,c in enumerate('政策力度很大')]+
                      [(c,5+i*.25,5+(i+1)*.25) for i,c in enumerate('股市会被推起来')])]
    cues=C.one_line_screens(entries,{**V.layout_for(720,1280),'line_capacity':8})
    assert ''.join(c['zh'] for c in cues)==entries[0]['zh']
    assert all(len(c['lines'])==1 and len(c['zh'])<=8 for c in cues)
    assert any(c['start_sec']==5 for c in cues)
    assert not any(c['start_sec']<5<c['end_sec'] for c in cues)
    assert [a for c in cues for a in c['caption_chars']]==entries[0]['caption_chars']


def test_semantic_groups_keep_exact_character_clock_after_pause_and_deletion():
    raw=[dict(start_sec=0,end_sec=2,zh='政策力度大，'),
         dict(start_sec=5,end_sec=7,zh='态度明确。')]
    groups=P.apply_semantic_groups(raw,['政策力度大态度明确'],20)
    anchors,_,_=P.caption_timeline(raw)
    assert groups[0]['caption_chars']==anchors
    cues=C.one_line_screens(groups,V.layout_for(720,1280))
    assert [c['start_sec'] for c in cues]==[0,5]


def test_corporate_name_and_number_never_break_at_asr_cue_boundary():
    raw=[dict(start_sec=0,end_sec=2,zh='我长期持有贵州茅'),
         dict(start_sec=2,end_sec=5,zh='台现金流增长3.5亿元')]
    cues=C.one_line_screens(raw,V.layout_for(720,1280))
    assert sum(c['zh'].count('贵州茅台') for c in cues)==1
    assert sum(c['zh'].count('3.5亿元') for c in cues)==1
    assert ''.join(c['zh'] for c in cues)==''.join(c['zh'] for c in raw)


@pytest.mark.parametrize('anchors',[[('原',0,1)], [('原',2,1),('话',1,2)],
                                  [('原',0,2),('话',1,3)]])
def test_inconsistent_character_evidence_is_rejected(anchors):
    with pytest.raises(ValueError):
        C.character_anchors(dict(zh='原话',start_sec=0,end_sec=3,caption_chars=anchors))


def test_hidden_sentence_marks_survive_semantic_grouping_and_final_render():
    raw=[dict(start_sec=0,end_sec=4,zh='不要追涨。也不要杀跌。')]
    groups=P.apply_semantic_groups(raw,['不要追涨也不要杀跌'],20)
    assert groups[0]['caption_boundaries']==[(4,'。'),(9,'。')]
    screens=C.one_line_screens(groups,V.live_card_layout())
    assert [s['zh'] for s in screens]==['不要追涨','也不要杀跌']
    assert [a for s in screens for a in s['caption_chars']]==groups[0]['caption_chars']


def test_legacy_semantic_flag_does_not_freeze_half_a_phrase():
    raw=[dict(start_sec=0,end_sec=1,zh='我们感觉大',semantic_group=True),
         dict(start_sec=1,end_sec=3,zh='概率事件仍在延续',semantic_group=True)]
    screens=C.one_line_screens(raw,V.live_card_layout())
    assert sum('大概率' in s['zh'] for s in screens)==1
    assert ''.join(s['zh'] for s in screens)=='我们感觉大概率事件仍在延续'


def test_complete_parallel_phrase_keeps_one_line_at_original_font():
    raw=[dict(start_sec=0,end_sec=2,zh='它进可攻，退可守。')]
    layout=V.live_card_layout()
    groups=P.apply_semantic_groups(raw,['它进可攻退可守'],layout['line_capacity'])
    screens=C.one_line_screens(groups,layout)
    assert [s['zh'] for s in screens]==['它进可攻退可守']
    assert screens[0]['font_px']==layout['subtitle_font_px']


def test_question_and_next_subject_are_not_mixed_to_fill_a_row():
    raw=[dict(start_sec=0,end_sec=4,zh='为什么不买？我们只看现金流。')]
    groups=P.apply_semantic_groups(raw,['为什么不买？我们只看现金流'],20)
    screens=C.one_line_screens(groups,V.live_card_layout())
    assert [s['zh'] for s in screens]==['为什么不买？','我们只看现金流']


def test_replanning_is_idempotent_and_keeps_exact_source_anchors():
    raw=[dict(start_sec=0,end_sec=4,zh='我们认为现在就是可转债的投资机会。')]
    groups=P.apply_semantic_groups(raw,['我们认为现在就是可转债的投资机会'],20)
    layout=V.live_card_layout();screens=C.one_line_screens(groups,layout)
    again=C.one_line_screens(screens,layout)
    assert [s['zh'] for s in again]==[s['zh'] for s in screens]
    assert [a for s in screens for a in s['caption_chars']]==groups[0]['caption_chars']
    assert all(len(s['lines'])==1 and len(s['zh'])<=layout['line_capacity'] for s in screens)


def test_reported_finance_entities_and_complete_quantifier_are_not_cut():
    raw=[dict(start_sec=0,end_sec=5,zh='顶只有一个，但是我们感觉大概率事件还在延续。')]
    groups=P.apply_semantic_groups(raw,['顶只有一个但是我们感觉大概率事件还在延续'],20)
    screens=C.one_line_screens(groups,V.live_card_layout())
    assert screens[0]['zh']=='顶只有一个'
    assert sum('大概率' in s['zh'] for s in screens)==1
    text='我们认为可转债市场是有很大的机会'
    cues=C.one_line_screens([dict(zh=text,start_sec=0,end_sec=4)],V.live_card_layout())
    assert sum('可转债市场' in s['zh'] for s in cues)==1


@pytest.mark.parametrize('hint',[(99,'。'),(2,''),(True,'。'),(2,'猜')])
def test_invalid_source_boundary_proof_is_rejected(hint):
    with pytest.raises(ValueError,match='断句边界'):
        C.one_line_screens([dict(zh='保持原话',start_sec=0,end_sec=2,
            semantic_group=True,caption_boundaries=[hint])],V.live_card_layout())


def test_caption_compounds_do_not_change_title_tokenization():
    text='我们感觉大概率事件'
    before=V.word_spans(text)
    assert '大概率事件' not in V.PROTECTED
    assert any(text[a:b]=='大概率事件' for a,b in C.caption_word_spans(text))
    assert V.word_spans(text)==before


def test_new_flash_fragments_fall_back_without_changing_words_or_clock(monkeypatch):
    from caption_lines_legacy import one_line_screens as legacy
    from caption_boundary_quality import metrics
    raw=[dict(zh='长期持有好公司不必每天盯着市场',start_sec=0,end_sec=4)]
    layout=V.live_card_layout();previous=legacy(raw,layout)
    atoms=C.character_anchors(raw[0])
    proposed=[C.caption_screen(atoms[a:a+2],{},layout) for a in range(0,len(atoms),2)]
    assert metrics(proposed,raw,layout)['flashes']>metrics(previous,raw,layout)['flashes']
    monkeypatch.setattr(C,'_natural_screens',lambda *a:proposed)
    assert C.one_line_screens(raw,layout)==previous
