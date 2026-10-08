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
