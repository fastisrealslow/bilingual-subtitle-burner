import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
from headline_policy import copy_length_ok
from title_rewrite import _extractive


def test_actual_short_reaction_can_keep_its_exact_words():
    source = '心情平常心吧，但是肯定是不高兴了。嗯，亏钱嘛，亏钱不高兴。嗯，但是也无所谓了。'
    result = _extractive(source, '林园', [], preferred='林园：亏钱不高兴',
                         guest_passages=[source], only_preferred=True)
    assert result['title'] == '林园：亏钱不高兴'
    assert result['cover_title'] == '亏钱不高兴'
    assert result['title_rewrite']['review']['attribution'] == 'reader_guest_passage'


@pytest.mark.parametrize('text', ['亏钱不高兴', '赔钱很难受', '赚钱很开心', '亏钱也无所谓'])
def test_complete_event_reaction_is_not_a_keyword_heading(text):
    assert copy_length_ok(text)


@pytest.mark.parametrize('text', ['不高兴', '亏钱', '赚钱', '感觉开心', '一定能赚钱'])
def test_exception_does_not_accept_bare_emotions_or_profit_promises(text):
    assert not copy_length_ok(text)


def test_short_reaction_still_requires_the_speakers_actual_source_words():
    with pytest.raises(ValueError):
        _extractive('亏钱不高兴。', '林园', [], preferred='林园：亏钱很开心',
                    guest_passages=['亏钱不高兴。'], only_preferred=True)
    with pytest.raises(ValueError):
        _extractive('亏钱不高兴。赚钱很开心。', '林园', [], preferred='林园：亏钱不高兴',
                    guest_passages=['赚钱很开心。'], only_preferred=True)


def test_curated_reaction_keeps_the_actual_complete_interval(monkeypatch):
    import curated_editorial as curated
    fixture=json.loads((Path(__file__).parent/'fixtures/linyuan_source34_reaction.json').read_text())
    cues=fixture['cues'];before=json.dumps(cues,ensure_ascii=False)
    monkeypatch.setattr(curated.editorial,'MIN_SECONDS',20.)
    a,b,picks=curated.source_ranges(cues,fixture['source_sha256'])[0]
    assert (cues[a]['start'],cues[b]['end'])==(708.92,733.64)
    assert len(picks)==1 and (picks[0]['start'],picks[0]['end'])==(0,b-a)
    assert picks[0]['editorial_prefer_exact_quote'] is True
    assert not picks[0].get('editorial_review')
    assert json.dumps(cues,ensure_ascii=False)==before
    assert curated.source_ranges(cues,'different-source') is None


@pytest.mark.parametrize('index',[0,1])
def test_actual_market_quotes_keep_source_scope_and_complete_intervals(monkeypatch,index):
    import curated_editorial as curated
    cases=json.loads((Path(__file__).parent/'fixtures/linyuan_source8_58_quotes.json').read_text())
    case=cases[index];cues=case['cues'];source=''.join(c['text'] for c in cues)
    monkeypatch.setattr(curated.editorial,'MIN_SECONDS',20.)
    a,b,picks=curated.source_ranges(cues,case['source_sha256'])[0]
    assert cues[a]['start']==case['segments'][0]['start']
    assert cues[b]['end']==case['segments'][0]['end']
    assert picks[0]['editorial_prefer_exact_quote'] is True
    assert not picks[0].get('editorial_review')
    result=_extractive(source,'林园',[],preferred=case['quote'],guest_passages=[source],only_preferred=True)
    assert result['title']==case['quote']
    assert result['cover_title']==case['quote'].split('：',1)[1]


@pytest.mark.parametrize("index",[0,1,2])
def test_returned_sources_keep_literal_attitude_and_do_not_invent_contrasts(monkeypatch,index):
    import curated_editorial as curated
    cases=json.loads((Path(__file__).parent/"fixtures/linyuan_final_three_quotes.json").read_text())
    case=cases[index];cues=case["cues"];source="".join(c["text"] for c in cues)
    monkeypatch.setattr(curated.editorial,"MIN_SECONDS",20.)
    a,b,picks=curated.source_ranges(cues,case["source_sha256"])[0]
    assert (cues[a]["start"],cues[b]["end"])==(case["start"],case["end"])
    assert picks[0]["editorial_prefer_exact_quote"] is True
    assert not picks[0].get("editorial_review")
    result=_extractive(source,"林园",[],preferred=case["title"],guest_passages=[source],only_preferred=True)
    assert result["title"]==case["title"]
    assert result["cover_title"]==case["title"].split("：",1)[1]
