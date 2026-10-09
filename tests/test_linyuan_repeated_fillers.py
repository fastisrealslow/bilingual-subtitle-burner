from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import caption_readability as C


def entry(text,start=0):
    return dict(start_sec=start,end_sec=start+len(text)*.1,zh=text,
                caption_chars=[(c,start+i*.1,start+(i+1)*.1) for i,c in enumerate(text)])


@pytest.mark.parametrize('raw,expected',[
    ('嗯嗯嗯，股息率是8%。','股息率是8%。'),
    ('嗯，啊，呃，企业需要现金流。','企业需要现金流。'),
    ('现金流很重要，嗯嗯啊，所以我不会卖。','现金流很重要，所以我不会卖。'),
    ('呃呃呃投资不能忽视风险。','投资不能忽视风险。'),
    ('嗯，企业利润不是100万元。','企业利润不是100万元。'),
])
def test_repeated_fillers_are_display_only_deletions_with_original_timing(raw,expected):
    original=[entry(raw)];snapshot=deepcopy(original)
    result,proof=C.clean_entries(original)
    assert original==snapshot
    assert proof['raw_text']==raw and proof['display_text']==expected
    assert all(tuple(atom) in original[0]['caption_chars'] for row in result for atom in row['caption_chars'])
    assert C.replay_edit_proof(proof)==(raw,expected)
    assert proof['edits']


@pytest.mark.parametrize('raw',[
    '嗯。','嗯嗯。','啊？','嗯哼。','呃逆，哈尔滨。',
    '没有没有，不是不是真的，金额100万元。',
    '百分之嗯嗯八，不能不能卖。',
])
def test_affirmations_questions_lexical_words_negations_and_amounts_survive(raw):
    _,proof=C.clean_entries([entry(raw)])
    assert proof['display_text']==raw


def test_fillers_do_not_cross_long_pauses_or_new_turns():
    source=[entry('嗯',0),entry('嗯投资要谨慎',3)]
    _,proof=C.clean_entries(source)
    assert proof['display_text']=='嗯嗯投资要谨慎'


def test_oct8_proof_keeps_real_anchors_and_exact_historical_edits():
    source=[entry('嗯嗯嗯，股息率是8%。')]
    _,old=C.clean_entries(source,policy_version=C.OCT8_VERSION)
    assert old['display_text']==source[0]['zh']
    assert old['raw_entries'][0]['caption_chars']==source[0]['caption_chars']
    assert C.replay_edit_proof(old)==(old['raw_text'],old['display_text'])
    forged=deepcopy(old);forged['version']=C.VERSION
    with pytest.raises(ValueError):C.replay_edit_proof(forged)
