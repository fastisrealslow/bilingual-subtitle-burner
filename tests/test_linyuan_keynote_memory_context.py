"""Real keynote proposals omitted the actual meeting background."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import keynote_proposals as K


def actual():return json.loads((Path(__file__).parent/'fixtures/linyuan_actual_keynote_memory_context.json').read_text())


def test_actual_memory_clause_is_only_an_original_timed_alternative(monkeypatch):
    monkeypatch.setattr(K.editorial,'CONTENT_POLICY','reference_v1');monkeypatch.setattr(K.editorial,'MIN_SECONDS',20)
    f=actual();cues=f['cues'];before=deepcopy(cues);rows=K.windows(cues)[0]
    assert any(r.get('memory_context') and r['start']==f['memory_start'] for r in rows)
    assert any(r['start']<f['memory_start']<=r['end'] and '如果今天' in r['text'] for r in rows)
    prompt,schema=K.request(rows);props=schema['properties']['picks']['items']['properties']
    assert f['memory_start'] in props['start']['enum']
    assert f['rejected_parent']['start'] not in props['start']['enum']
    picks,rejected=K.validate(cues,rows,dict(picks=[dict(start=f['memory_start'],end=f['story_end'])]))
    assert not rejected and len(picks)==1 and cues==before
    p=picks[0];text=''.join(c['text'] for c in cues[p['start']:p['end']+1])
    assert text.startswith('我记得') and '线上的会议' in text and '媒体就报道' in text
    assert text.endswith('我说：那是提起风险。')
    assert '真正的投资' not in text and '没有删除条件' in prompt
    assert not any(k in p for k in ['passed','story_selection','title'])


@pytest.mark.parametrize('lead',['假设前两年，','假如前两年，','如果我当年前两年，','我编一个故事前两年，'])
def test_a_governing_fiction_or_past_hypothesis_cannot_be_cut_away(lead):
    cues=[dict(start=0,end=10,text=lead),dict(start=10,end=30,text='我记得两年前参加了一场会议。'),
        dict(start=30,end=55,text='我说投资要注意风险，后来媒体作了报道。')]
    assert not any(r.get('memory_context') for rows in K.windows(cues) for r in rows)


def test_no_midword_timestamp_or_generic_self_assessment_can_be_added():
    cues=[dict(start=0,end=10,text='前两年，我记得'),dict(start=10,end=30,text='自己当时想参加会议。')]
    assert not any(r.get('memory_context') for rows in K.windows(cues) for r in rows)
    assert not K.opening_allowed('那时候问我，我说要注意投资风险。')
    assert K.opening_allowed('我当年买了这家公司，后来一直持有。')


def test_complete_recollection_is_proposed_even_when_model_only_selects_host_intro(monkeypatch):
    monkeypatch.setattr(K.editorial,'CONTENT_POLICY','reference_v1');monkeypatch.setattr(K.editorial,'MIN_SECONDS',20)
    f=actual();before=deepcopy(f['cues']);picks=K.memory_episode_proposals(f['cues'])
    assert [(p['start'],p['end']) for p in picks]==[(f['memory_start'],f['story_end'])]
    assert f['cues']==before
    assert not any(k in picks[0] for k in ('passed','story_selection','title','media_approved'))


@pytest.mark.parametrize('change',['no_reaction','no_reply','fiction'])
def test_incomplete_or_fictional_recollection_cannot_supply_a_proposal(change):
    f=actual();cues=deepcopy(f['cues'])
    for c in cues:
        if change=='no_reaction':c['text']=c['text'].replace('结果是媒体就报道','大家后来又聊到')
        elif change=='no_reply':c['text']=c['text'].replace('我说：那是提起风险。','投资需要注意风险。')
        elif c['text'].startswith('我记得'):c['text']='假设'+c['text']
    assert not K.memory_episode_proposals(cues)


def actual_17():
    return json.loads((Path(__file__).parent/'fixtures/linyuan_actual_keynote_memory_17.json').read_text())


def test_actual_17_reply_in_same_sentence_stops_at_existing_cue_before_new_topic(monkeypatch):
    monkeypatch.setattr(K.editorial,'CONTENT_POLICY','reference_v1');monkeypatch.setattr(K.editorial,'MIN_SECONDS',20)
    f=actual_17();cues=f['cues'];before=deepcopy(cues);rows=K.windows(cues)[0]
    assert any(r.get('reported_reply_end') and r['end']==f['story_end'] for r in rows)
    assert any(r['start']<f['story_end']<r['end'] and '真正的投资' in r['text'] for r in rows)
    picks=K.memory_episode_proposals(cues)
    assert [(p['start'],p['end']) for p in picks]==[(f['memory_start'],f['story_end'])]
    text=''.join(c['text'] for c in cues[picks[0]['start']:picks[0]['end']+1])
    assert text.startswith('我记得') and text.endswith('我说那是提醒风险，')
    assert '线上的会议' in text and '媒体就报道' in text and '真正的投资' not in text
    assert cues==before and cues[picks[0]['end']]['end']==416.04
    assert not any(k in picks[0] for k in ('passed','story_selection','title','media_approved'))


@pytest.mark.parametrize('change',['no_reaction','no_reply','incomplete_reply','fiction'])
def test_same_sentence_reply_still_needs_literal_reaction_and_complete_correction(change):
    f=actual_17();cues=deepcopy(f['cues'])
    for cue in cues:
        if change=='no_reaction':cue['text']=cue['text'].replace('结果这媒体就报道出去','大家后来又聊到')
        elif change=='no_reply':cue['text']=cue['text'].replace('我说那是提醒风险，','投资需要注意风险，')
        elif change=='incomplete_reply':cue['text']=cue['text'].replace('我说那是提醒风险，','我说那是因为这个，')
        elif cue['text'].startswith('我记得'):cue['text']='假设'+cue['text']
    assert not K.memory_episode_proposals(cues)


def test_actual_pilot_future_opportunity_plan_is_not_a_completed_personal_story():
    import publishing_experiment as E
    event='我不能说说我最后说那个结果是个错的，你们听了，啊，这个这个就是，我也要正视啊，我们自己啊，我这也是人生，我今年六十一岁了，我要抓住这最后一次机会，我要看看我能不能抓住这是人生的最后一大机会啊！'
    assert 'prospective' in E.personal_event_error(event)
    assert E.personal_event_error('我记得两年前参加了一场线上的会议。') is None
    assert E.personal_event_error('我买了这家公司，未来我要抓住它增长的机会。') is None
    assert E.personal_event_error('我说那是提醒风险，媒体误解了我的意思。') is None


@pytest.mark.parametrize('event',[
    '但是如果今天这个位置，如果我觉得不该说这个话的时候，我可能什么都不说，甚至前两年，我记得两两两年前还是三年前，也是一个线上的会议，说是有很多人参加。',
    '如果我当年参加了会议，我可能会说需要谨慎。',
    '假设我买了这家公司，后来我会一直持有。',
    '我先假设当时参加了会议，再谈可能的回应。',
])
def test_actual_prompt_false_positive_cannot_borrow_past_words_from_a_conditional(event):
    import publishing_experiment as E
    assert 'hypothetical' in E.personal_event_error(event)
    assert E.personal_event_error('我当时制定了如果价格下跌就继续买入的计划。') is None


def test_actual_temporal_completed_activity_is_a_whole_original_opening():
    text='那么从去年九月份到现在，实际上我们组织了很多次活动，我见的人超过五千人。'
    assert K.temporal_action_opening(text) and K.opening_allowed(text)
    cues=[dict(start=326.2,end=332.84,text=text),dict(start=334.28,end=334.6,text='为什么？'),
        dict(start=335.48,end=344.28,text='那就是我觉得这个位置，是让人有磨难，最后能让人能够记得起来的事儿。'),
        dict(start=345.88,end=348.44,text='一生当中啊，能够记起、记得起来。')]
    before=deepcopy(cues);rows=K.windows(cues)[0];prompt,schema=K.request(rows)
    assert 0 in schema['properties']['picks']['items']['properties']['start']['enum']
    assert '实际组织' in prompt and cues==before


@pytest.mark.parametrize('text',[
    '那么明年九月份，我们计划组织很多次活动。',
    '那么从去年九月份到现在，如果我们组织了活动，可能就有机会。',
    '那么从去年九月份到现在，实际上我们没有组织过活动。',
    '那么从去年九月份到现在，未来我们要组织很多次活动。',
    '那么我现在的判断是，过了时间不长的时候，你们会想起我说过一些话。'])
def test_temporal_opening_exception_does_not_admit_future_denied_or_hypothetical_activity(text):
    assert not K.temporal_action_opening(text)
    assert not K.opening_allowed(text)


def test_actual_activity_original_cues_become_available_without_selecting_a_range():
    f=json.loads((Path(__file__).parent/'fixtures/linyuan_actual_keynote_activity_17.json').read_text())
    cues=f['cues'];before=deepcopy(cues);rows=K.windows(cues)[0]
    actual=[r for r in rows if r.get('temporal_action_context')]
    assert len(actual)==1 and actual[0]['start']==f['activity_start']
    assert actual[0]['text']==''.join(c['text'] for c in cues[actual[0]['start']:actual[0]['end']+1])
    assert f['activity_start'] in K.request(rows)[1]['properties']['picks']['items']['properties']['start']['enum']
    assert cues==before and not any(k in actual[0] for k in ('picks','passed','story_selection','title'))


def test_completed_actual_activity_keeps_motive_before_new_conditional_recollection(monkeypatch):
    monkeypatch.setattr(K.editorial,'CONTENT_POLICY','reference_v1');monkeypatch.setattr(K.editorial,'MIN_SECONDS',20)
    f=json.loads((Path(__file__).parent/'fixtures/linyuan_actual_keynote_activity_17.json').read_text())
    cues=f['cues'];before=deepcopy(cues);picks=K.completed_activity_proposals(cues)
    assert len(picks)==1
    p=picks[0];text=''.join(c['text'] for c in cues[p['start']:p['end']+1])
    assert p['start']==f['activity_start'] and cues[p['end']]['end']==379.72
    assert '组织了很多次活动' in text and '为什么？' in text
    assert text.endswith('也是有目的。') and '能不能印证' in text
    assert '但是如果' not in text and cues==before
    assert not any(k in p for k in ('passed','story_selection','title','media_approved'))


@pytest.mark.parametrize('change',['future','denied','hypothetical','no_motive'])
def test_activity_boundary_does_not_supply_a_story_or_drop_its_governing_condition(change):
    f=json.loads((Path(__file__).parent/'fixtures/linyuan_actual_keynote_activity_17.json').read_text())
    cues=deepcopy(f['cues'])
    for c in cues:
        if change=='future':c['text']=c['text'].replace('组织了','计划组织')
        elif change=='denied':c['text']=c['text'].replace('组织了','没有组织过')
        elif change=='hypothetical' and '那么从去年' in c['text']:c['text']='假设'+c['text']
        elif change=='no_motive':c['text']=c['text'].replace('为什么？','谢谢大家。')
    assert K.completed_activity_proposals(cues)==[]
