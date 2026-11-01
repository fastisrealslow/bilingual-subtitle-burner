import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import title_rewrite as T


def test_published_complete_source_keeps_its_faithful_title():
    r=json.loads((ROOT/'tests/fixtures/linyuan_published_consumer_upgrade_title.json').read_text())
    text=''.join(c['text'] for c in r['cues'])
    assert r['published_bvid']=='BV1YAn46mEgJ'
    assert r['cues'][0]['start']==203.48 and r['cues'][-1]['end']==234.36
    assert T.error(r['published_title'],r['published_proof'],text) is None


def test_actual_approved_flags_cannot_turn_asset_comparison_into_trade_advice():
    r=json.loads((ROOT/'tests/fixtures/linyuan_inferred_trade_directive.json').read_text())
    text=''.join(c['text'] for c in r['cues'])
    assert all(r['old_proof']['review'][k] is True for k in T.CHECKS)
    assert '推导出的建议' in r['old_proof']['review']['reason']
    assert '买卖指令' in T.error(r['old_title'],r['old_proof'],text)
    item=dict(title=r['old_title'],cover_title=r['old_proof']['cover'],
        subject=r['old_proof']['subject'],evidence=r['old_proof']['evidence'])
    assert '买卖指令' in T._candidate_error(item,text,'林园',(),check_layout=False)


@pytest.mark.parametrize('title,cover,source',[
    ('林园：别投房产和汽车','房产汽车不要投','房产建多了会出问题，汽车几年后都是废铜烂铁。'),
    ('林园：科技股别买','科技股别买','我不买科技股，因为很难看清未来。'),
    ('林园：赶紧买食品股','赶紧买食品股','食品保存期短，供需能够自动调节。'),
    ('林园：必须卖出汽车股','必须卖出汽车股','汽车生产几年后会折旧。'),
    ('林园：千万不要买股票','千万不要买股票','现在不建议买股票。'),
    ('林园：不要买股票','不要买股票','不要卖股票。'),
    ('林园：不要买股票','不要买股票','不要继续加仓股票。'),
    ('林园：必须买股票','必须买股票','我必须买股票，这是我个人的选择。'),
    ('林园：房产会折旧','房产不能投资','房产建多了会出问题。'),
])
def test_descriptions_personal_choices_opposite_actions_and_added_urgency_reject(title,cover,source):
    assert T.unsupported_trade_directive_error(title,cover,source)


@pytest.mark.parametrize('title,cover,source',[
    ('林园：我不买科技股','我不买科技股','我不买科技股，因为看不清长期竞争。'),
    ('林园：房产建多会出问题','房产建多会出问题','房产建多了会出问题。'),
    ('林园：药品过期会自动调节','药品过期会自动调节','药品它都过期了，它自动会调节的。'),
    ('林园：别投房产','别投房产','别投资房产，这些是我给大家的建议。'),
    ('林园：不要买股票','不要买股票','现在千万不要买入股票。'),
    ('林园：建议买股票','建议买股票','我建议买入股票。'),
    ('林园：我必须买股票','我必须买股票','我必须买股票，这是我的选择。'),
])
def test_faithful_statements_and_explicit_same_direction_advice_keep_normal_review(title,cover,source):
    assert T.unsupported_trade_directive_error(title,cover,source) is None


def test_actual_published_general_encouragement_does_not_authorize_buying_invitation():
    r=json.loads((ROOT/'tests/fixtures/linyuan_published_attempt_directive.json').read_text())
    assert all(r['proof']['review'][k] is True for k in T.CHECKS)
    assert '买卖指令' in T.error(r['title'],r['proof'],r['raw_text'])
    item=dict(title=r['title'],cover_title=r['cover'],
        subject=r['proof']['subject'],evidence=r['proof']['evidence'])
    assert '买卖指令' in T._candidate_error(item,r['raw_text'],'林园',(),check_layout=False)


@pytest.mark.parametrize('copy',[
    '有些事直接去尝试买股票吧', '试着加仓股票吧', '尝试卖出股票吧',
    '不妨尝试投资股票', '可以试着买入股票',
])
def test_general_encouragement_is_not_same_action_permission(copy):
    source='那时候我们买股票，没有考虑风险。有些事可以去尝试。'
    assert T.unsupported_trade_directive_error('林园：'+copy,'有些事可以尝试',source)
    assert T.unsupported_trade_directive_error('林园：有些事可以尝试',copy,source)


@pytest.mark.parametrize('copy,source',[
    ('尝试买股票吧','可以尝试买入股票。'),
    ('试着卖出股票吧','建议卖出股票。'),
    ('可以试着买股票','不妨尝试买股票。'),
    ('尝试买股票吧','我建议大家可以尝试买入股票。'),
    ('我尝试买股票','我尝试买股票。'),
    ('过去尝试买股票吧','过去尝试买股票吧。'),
    ('是否可以尝试买股票？','是否可以尝试买股票？'),
    ('他可以尝试买股票','他可以尝试买股票。'),
    ('有些事可以尝试','有些事可以尝试。'),
])
def test_explicit_permission_and_non_directive_attempts(copy,source):
    assert T.unsupported_trade_directive_error('林园：'+copy,copy,source) is None


@pytest.mark.parametrize('source',[
    '我可以尝试买股票。', '他可以尝试买股票。', '不要尝试买股票吧。',
    '是否可以尝试买股票？', '那时候可以尝试买股票。',
    '不可以尝试买股票。',
    '可以尝试卖股票。', '可以尝试加仓股票。',
])
def test_personal_historical_negative_questions_and_other_actions_are_not_permission(source):
    assert T.unsupported_trade_directive_error('林园：尝试买股票吧','有些事可以尝试',source)
