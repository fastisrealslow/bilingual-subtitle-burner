from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T
import publishing_experiment as E

SOURCE='所以你要是每个都投一点，每个投一点，你抓住了一万倍的也没什么太大的用处。'


@pytest.mark.parametrize('title,cover',[
    ('林园：抓一万倍企业没用太大用处','抓一万倍企业没用太大用处'),
    ('林园：分散投一点，抓万倍股也没多大用','万倍股没多大用'),
    ('林园：万倍股没有意义','分散投一点，万倍股也没多大用'),
])
def test_conditional_position_size_cannot_become_blanket_useless_return(title,cover):
    assert T.allocation_condition_error(title,cover,SOURCE)
    assert T.forecast_copy_error(title,cover,SOURCE)


def test_preserved_condition_and_unrelated_unconditional_source_are_not_blocked():
    assert T.allocation_condition_error('林园：每个都投一点，抓万倍股也没多大用',
        '仓位太小，万倍股也没多大用',SOURCE) is None
    assert T.allocation_condition_error('林园：万倍股没用','万倍股没用','我认为万倍股没用。') is None


@pytest.mark.parametrize('event',[
    '我们最后的理论就是白猫黑猫，找到好公司一定要敢于下重注。',
    '我们的原则就是要集中投资。',
])
def test_method_recap_is_not_itself_a_personal_event(event):
    detail='我们过去也研究过一些公司。'
    text=event+detail
    row=dict(index=0,kind='personal_story',subject_quote=event,event_quote=event,detail_quote=detail)
    with pytest.raises(ValueError,match='normative method'):
        E.validate_choices([dict(text=text)],[dict(start=0,end=0)],dict(choices=[row]))


def test_actual_action_inside_a_recap_remains_eligible_not_automatically_approved():
    event='我的方法是要研究产品，我去年买了这家公司。'
    detail='我去看过实际产品。'
    row=dict(index=0,kind='personal_story',subject_quote=event,event_quote=event,detail_quote=detail)
    assert E.validate_choices([dict(text=event+detail)],[dict(start=0,end=0)],dict(choices=[row]))
