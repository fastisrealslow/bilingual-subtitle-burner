import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import source_selection as S


def test_actual_whole_source_keeps_the_missed_holding_case_and_every_original_cue(monkeypatch):
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(S.editorial,'MIN_SECONDS',20)
    f=json.loads((Path(__file__).parent/'fixtures/linyuan_actual_missing_company_followup.json').read_text())
    cues=f['cues'];before=json.dumps(cues,ensure_ascii=False)
    # Actual old full-source production omitted this original complete exchange.
    assert not any(p['start']==95 and p['end']==105 for p in f['actual_old_candidates'])
    picks=S.select(cues,limit=None,whole_source=True)
    pick=next(p for p in picks if p['start']==95)
    assert pick['end']==105 and S.boundary_error(cues,pick) is None
    assert cues[pick['start']]['text']==f['missed_question']
    assert cues[pick['start']]['start']==254.76 and cues[pick['end']]['end']==281.72
    assert '接近二十年' in ''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
    assert cues[pick['end']]['text']=='心了，是吧？无所谓的。'
    assert json.dumps(cues,ensure_ascii=False)==before
    assert 'passed' not in pick and pick['selection_method']=='source_question_answer_v2'


@pytest.mark.parametrize('text',[
    '那这未来会继续看好茅台？','那未来会继续持有五粮液吗？',
    '那么，以后还会继续投资片仔癀吗？',
])
def test_literal_asset_continuation_question_without_addressee(text):
    assert S.question_unit(text)


@pytest.mark.parametrize('text',[
    '我觉得未来会继续看好茅台。','比如，那未来会继续持有茅台吗？',
    '为什么呢？','是吧？','那未来股市会涨吗？',
])
def test_guest_claims_examples_and_bare_tags_are_not_the_new_host_pattern(text):
    assert not S.question_unit(text)


@pytest.mark.parametrize('text',[
    '我们当时买入后一直持有，就是这个选择没有变，是吧？无所谓的。',
    '这些事情发生后也都正常看待，对吧？很正常的。',
])
def test_interior_guest_tag_with_substantive_closing_assertion_is_complete(monkeypatch,text):
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    assert not S.unresolved_question_tail(text)


@pytest.mark.parametrize('text',[
    '您觉得现在需要改变这个策略了，是吧？那好吧。',
    '那下次还有没有这样的机会？嗯，好的。',
    '这个问题我们还要请您介绍一下，是吧？无所谓的。',
    '我们当时就是这样想的，是吧？对，对，对。',
    '我们当时就是这样想的，是吧？',
])
def test_unanswered_host_turns_and_empty_acknowledgements_stay_blocked(monkeypatch,text):
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    assert S.unresolved_question_tail(text)


def test_actual_trial_uses_full_source_and_production_gates_without_editorial_answers_or_publication():
    import yaml
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
    from build_linyuan_company_followup_actual import build_trial
    d=build_trial()
    assert d['permissions']==dict(contents='read',actions='read')
    assert d['concurrency']['cancel-in-progress'] is False
    saved=yaml.safe_load((Path(__file__).resolve().parents[1]/'.github/workflows/linyuan-company-followup-actual.yml').read_text())
    assert saved==d
    steps=d['jobs']['preview']['steps'];ids=[s.get('id') for s in steps]
    assert ids.index('source_admission')<ids.index('src')<ids.index('source_gate')<ids.index('render')
    source=next(s for s in steps if s.get('id')=='src')
    assert '12422073680' in source['run'] and '6f5ddecc6db4f204' in source['run']
    assert 'ci_fetch_bilibili.py --url' not in source['run']
    assert d['env']['LINYUAN_AUTOMATIC_ONLY']=='true' and d['env']['OUTPUT_LAYOUT']=='portrait'
    assert not {s.get('name') for s in steps}.intersection({'上传成片到 Release','B 站投稿','明确触发库存核验与自动补位'})
    assert any('verify_linyuan_story_delivery.py' in s.get('run','') for s in steps)
    assert any('--preview-only' in s.get('run','') for s in steps)


def test_actual_larger_model_mixed_next_cue_keeps_complete_guest_sentence(monkeypatch):
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(S.editorial,'MIN_SECONDS',20)
    f=json.loads((Path(__file__).parent/'fixtures/linyuan_actual_17b_company_followup.json').read_text())
    cues=f['cues'];before=json.dumps(cues,ensure_ascii=False)
    picks=S.select(cues,limit=None,whole_source=True)
    pick=next(p for p in picks if cues[p['start']]['start']==255.24)
    assert cues[pick['end']]['end']==281.0
    text=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
    assert '地球' in text and text.endswith('平常心了，是吧？')
    assert '无所谓的。涨到' not in text and S.boundary_error(cues,pick) is None
    assert json.dumps(cues,ensure_ascii=False)==before
    units=S.sentence_units(cues)
    j=next(i for i,u in enumerate(units) if u['end']==pick['end'])
    assert S.unresolved_question_tail(units[j]['text'])
    assert S.closed_guest_tag_before_next_question(units,j)
    units[j+1]['text']='嗯，好的。您会去做一些减仓吗？'
    assert not S.closed_guest_tag_before_next_question(units,j)
    units[j+1]['text']='无所谓的。'
    assert not S.closed_guest_tag_before_next_question(units,j)
