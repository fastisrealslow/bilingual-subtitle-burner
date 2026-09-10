"""Approved preview regression: text fidelity, pauses, timing and actual style."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import caption_readability as R
import presentation as V
import produce_cn as P
import editorial_policy as E


def entry(text,a=0,b=5):
    return dict(zh=text,start_sec=a,end_sec=b)


def test_edit_removes_padding_but_keeps_raw_evidence_and_character_times():
    original=[entry('啊，那么我我对这个价值投资的理解，'),
              entry('我主要是从这个行业的行业去去把握。',5,10)]
    snapshot=copy.deepcopy(original)
    out,proof=R.clean_entries(original)
    assert original==snapshot
    assert ''.join(x['zh'] for x in out)=='我对价值投资的理解，我主要从行业去把握。'
    assert proof['raw_text']==''.join(x['zh'] for x in original)
    assert proof['edits'] and proof['raw_sha256']!=proof['display_sha256']
    atoms,_,_=P.caption_timeline(out)
    assert all(a<=b for _,a,b in atoms)
    assert atoms[0][1]>0  # A deleted filler doesn't pull later speech earlier.


@pytest.mark.parametrize('text',[
    '不是不是，我不买，也不会卖贵州茅台，股息率不到8%不买。',
    '朋友的朋友看看，人人都越来越好。',
    '如果价格不降，那么我主要还是持有。',
    '金额100万元，额度不够，亏了99%。',
    '嗯。',
])
def test_substantive_words_negation_quantities_and_emphasis_survive(text):
    out,proof=R.clean_entries([entry(text)])
    assert ''.join(x['zh'] for x in out)==text
    assert not proof['edits']


def test_repeated_pronoun_after_a_real_turn_gap_is_not_deleted():
    out,proof=R.clean_entries([entry('我',0,1),entry('我来回答',5,8)])
    assert ''.join(x['zh'] for x in out)=='我我来回答'
    assert not proof['edits']


def test_accepted_source_uses_default_editor_and_replans_old_cache(monkeypatch,tmp_path):
    source=json.loads((ROOT/'tests/fixtures/linyuan_readability_0909.json').read_text())
    cache=tmp_path/'semantic.json'
    cache.write_text('["old layout must not survive"]')
    monkeypatch.setattr(P,'llm',lambda *a,**k:pytest.fail('Recorded source has local semantic boundaries'))
    result=P.semantic_caption_entries(source,'',V.layout_for(720,1280,True),cache)
    text=''.join(x['zh'] for x in result)
    assert '行业的行业' not in text and '我我' not in text and '去去' not in text
    assert any(x['zh']=='我主要从行业去把握' for x in result)
    assert any(x['zh']=='好的企业从哪里来？' for x in result)
    assert all(.8<=x['end_sec']-x['start_sec']<=6.001 for x in result)
    assert all(a['end_sec']<=b['start_sec'] for a,b in zip(result,result[1:]))
    proof=json.loads(cache.with_suffix('.editing.json').read_text())
    normalized=lambda s:P.re.sub(r'[\s，。！？；：、]','',s)
    assert normalized(text)==normalized(proof['display_text'])
    assert '过世' in text  # An uncertain ASR word must NOT become a guessed correction.
    assert list(tmp_path.glob('*.readable-*.json'))


@pytest.mark.parametrize('w,h,card',[(720,1280,True),(1280,720,False),(720,1280,False),(720,720,False)])
def test_new_style_is_readable_and_fc_parses_text_without_graphics(w,h,card,tmp_path):
    layout=V.layout_for(w,h,card);target=tmp_path/'captions.ass'
    source=[dict(**entry('股息率不到8%我不会买'),semantic_group=True)]
    V.write_ass(source,target,layout,'Noto Sans CJK SC')
    ass=target.read_text(encoding='utf-8-sig')
    assert '&H00422C18' in ass and '&H0000D7FF' not in ass
    assert E.ass_dialogue_text(ass)==source[0]['zh']
    if card:
        assert r'\fs64' in ass and r'\pos(360,957)' in ass
    else:
        assert ',3,10,0,5,' in ass  # Opaque light background behind dark text.
    assert layout['readability_version']==R.VERSION


@pytest.mark.parametrize('text',['70%来自','有的甚至','他跟银行','我主要从行业'])
def test_no_dangling_screen_tails(text):
    assert P.unfinished_caption_tail(text)
