"""A real failed batch used a fifteen-year forecast as personal-story detail."""
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import publishing_experiment as E


@pytest.mark.parametrize('quote',[
    '这个时间不会长，十五年以后就能见分晓。',
    '十五年以后就能见分晓。','三年后才会看到结果。','再过二十年以后见成效。'])
def test_future_only_timing_is_not_story_detail(quote):
    assert E.personal_detail_error(quote)


@pytest.mark.parametrize('quote',[
    '我等了十五年才见到结果。','我当时认为十五年以后就能见分晓。',
    '十五年以后就能见分晓，所以我当年买了它。','我决定持有十五年。',
    '因为当时毛利率下降很厉害。','嗯，如果他还真是个人，他少不了。',
    '我们当时提出了一个理论是消费升级。'])
def test_past_outcomes_choices_reasons_and_attributed_expectations_remain_available(quote):
    assert E.personal_detail_error(quote) is None


def real_bad_choice():
    return dict(kind='personal_story',
        subject_quote='好的，那么呃林总刚刚聊完您的一个呃，产产品的情况啊，您说到您基本是呃不卖出的。',
        event_quote='啊，这个我不是说这个这个，就是我们投资了公司。',
        detail_quote='这个时间不会长，十五年以后就能见分晓。')


def test_selection_and_delivery_both_reject_the_real_forecast_detail():
    row=real_bad_choice()
    text=''.join(row[k] for k in ('subject_quote','event_quote','detail_quote'))
    with pytest.raises(ValueError,match='未来.*经历细节'):
        E.validate_choices([dict(start=0,end=30,text=text)],[dict(start=0,end=0)],
                           dict(choices=[dict(index=0,**row)]))
    meta=dict(editorial_profile=E.TREATMENT,experiment_proof=dict(version=E.VERSION,
        profile=E.TREATMENT,source_duration_sec=1800,
        selection=dict(version=E.VERSION,transcript_sha256=E.digest(text),**row)))
    assert '未来' in E.proof_error(meta)


def test_generic_time_is_not_a_literal_headline_hook_but_real_numbers_still_need_review():
    row=dict(kind='personal_story',subject_quote='我当时买了这家公司',
        event_quote='我买过它',detail_quote='我等了很长时间。')
    focus=E.title_focus(row)
    assert '时间' not in focus['details']
    assert E.title_focus_error('林园：买了它以后，我一直等',focus) is None
    # This only removes a generic lexical demand; no facts/title are certified.
    assert focus['source']==[row[k] for k in ('subject_quote','event_quote','detail_quote')]


def test_invalid_timing_retries_the_same_complete_candidate_without_providing_a_replacement(tmp_path):
    # Synthetic protocol test; this is not an approved production selection.
    text='我当年卖了电器。因为毛利率下降了。这个时间不会长，十五年以后就能见分晓。'
    cues=[dict(start=0,end=40,text=text)]
    replies=iter([dict(candidate_0=dict(kind='personal_story',subject_sentence=0,event_sentence=0,detail_sentence=2)),
                  dict(candidate_0=dict(kind='personal_story',subject_sentence=0,event_sentence=0,detail_sentence=1))])
    prompts=[]
    def call(prompt,schema):prompts.append(prompt);return next(replies)
    result=E.choose_stories(cues,[dict(start=0,end=0)],call,tmp_path/'selection.json')
    assert result['selection_completed'] and len(result['selected'])==1
    assert result['selected'][0]['start']==result['selected'][0]['end']==0
    assert len(prompts)==2 and '未来多久见分晓' in prompts[1]
    assert result['selected'][0]['story_selection']['detail_quote']=='因为毛利率下降了。'
