from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import publishing_experiment as E


def choice(event):
    return dict(kind='personal_story',subject_quote=event,event_quote=event,
        detail_quote='隐隐约约感觉到有抹不掉的风险。')


def test_actual_bank_exit_cannot_have_objectless_title():
    s=choice('有研究，银行是这样的，我们就这个板块，我们一六年四月份已经退出了。')
    focus=E.title_focus(s)
    assert focus['subjects']==['银行']
    assert E.title_focus_error('林园：一六年四月就退出了，隐隐约约感觉到有风险',focus)
    assert E.title_focus_error('林园：一六年四月退出银行，隐隐约约感觉有风险',focus) is None
    assert E.company_subject_error(s) is None
    assert E.company_subject_error({**s,'kind':'company_case'})


@pytest.mark.parametrize('sector',['银行','医药','白酒','科技','地产'])
def test_literal_sector_action_is_object_not_company_approval(sector):
    focus=E.title_focus(choice('我们当时已经退出'+sector+'了。'))
    assert sector in focus['subjects']


def test_host_hypothesis_cannot_supply_unstated_guest_object():
    s=choice('我们那时候已经退出了。')
    s['subject_quote']='网友问银行现在可以抄底吗？'
    assert not E.title_focus(s)['subjects']


def test_company_name_substring_is_not_a_sector_object():
    assert not E.title_focus(choice('我们当时买入招商银行了。'))['subjects']


def test_nontrading_life_background_does_not_force_sector_keyword():
    assert not E.title_focus(choice('我在银行上班的时候遇到过这件事。'))['subjects']


def test_prompt_keeps_sector_and_uncertainty_without_preapproved_title():
    s=choice('我们当时已经退出银行了。')
    prompt=E.story_title_prompt('原有事实审核',dict(properties=dict(a_reading={})),s)
    assert '银行' in prompt and '不能只写' in prompt and '不确定性' in prompt
    assert E.VERSION==2026101801 and E.SELECTION_REVISION==2026102201
