"""Real machine false approvals plus normal source-supported controls."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T

SOURCE='我就聚焦于消费。我只投我看好的。除非这个人不吃不喝了，真是变成机器人了，那我也不行。如果他还真是个人，他少不了。'


@pytest.mark.parametrize('title,cover',[
    ('林园：除非变机器人，我少不了投消费','除非变机器人，我少不了投消费'),
    ('林园：除非人变机器人，不然少不了投吃喝','除非人变机器人，不然少不了投吃喝'),
    ('林园：别人都会但我专心，变机器人前我绝不投','变机器人前，我绝不动手'),
    ('林园：我只投消费，避开不吃不喝的怪人','只投消费'),
    ('林园：我只投消费','远离不吃不喝的怪人'),
])
def test_human_condition_cannot_create_investment_obligation_time_or_avoidance(title,cover):
    assert T.human_condition_actor_error(title,cover,SOURCE)
    assert T.forecast_copy_error(title,cover,SOURCE)


@pytest.mark.parametrize('title,cover',[
    ('林园：我只投我看好的消费，在一亩三分地耕耘','只投我看好的消费'),
    ('林园：人少不了吃喝，我只投看好的消费','我只投看好的消费'),
    ('林园：我只投消费，除非人不吃不喝','只投我看好的消费'),
])
def test_guard_does_not_approve_or_block_normal_actual_choice_and_human_demand(title,cover):
    assert T.human_condition_actor_error(title,cover,SOURCE) is None


def test_unrelated_robot_company_story_is_not_a_human_demand_hypothesis():
    assert T.human_condition_actor_error('林园：变机器人前我绝不投','我不投',
        '这家公司做机器人，我决定暂时不投。') is None
