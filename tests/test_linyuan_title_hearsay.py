import sys,json
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T
SOURCE='光伏能源是这样的，就是我没有没有特意去研究，但是我好像有人给我说是光伏光伏能源这一块，这是对环境污染很大。所以我们就没参与啊，没参与。'


@pytest.mark.parametrize('title,cover',[
 ('林园：没参与光伏能源，因污染严重','光伏能源污染严重'),
 ('林园：光伏能源污染大，但未亲自研究','光伏能源污染大，但未亲自研究'),
 ('林园：听说光伏污染大，我没参与','光伏能源污染严重'),
])
def test_real_reviewers_cannot_drop_hearsay(title,cover):
    assert T.reported_claim_error(title,cover,SOURCE)
    item=dict(title=title,cover_title=cover,subject='光伏',evidence=[SOURCE])
    proof=T._package(item,SOURCE,dict(method='cpu_text_review',appeal=4,
        reason='历史模型误判全部通过，此测试验证新原文门禁。',**{k:True for k in T.CHECKS}),[])['title_rewrite']
    assert '转述' in T.error(title,proof,SOURCE)


@pytest.mark.parametrize('title,cover',[
 ('林园：没特意研究且听说污染大，所以不参与光伏。','因听闻污染且未研究，故不参与光伏'),
 ('林园：光伏能源我没有研究，也没有参与','光伏我没研究也没参与'),
])
def test_relayed_claim_or_personal_action_can_remain(title,cover):
    assert T.reported_claim_error(title,cover,SOURCE) is None


def test_other_topic_is_not_forced_to_become_hearsay():
    assert T.reported_claim_error('林园：企业经营利润持续增长','企业经营利润持续增长',SOURCE+'企业经营利润持续增长。') is None
    assert T.reported_claim_error('林园：光伏能源污染很大','光伏能源污染很大','我们研究过了，光伏能源污染很大。') is None


def test_cover_can_reuse_a_complete_qualified_title_before_review():
    title='林园：听说光伏污染大且未研究，所以没参与'
    item=T.bind_candidate(dict(title=title,cover_title='光伏能源污染大'),
        dict(evidence_ids=[0]),[SOURCE],T.subject_catalog([SOURCE]))
    assert '听说' in item['cover_title']
    assert T.reported_claim_error(title,item['cover_title'],SOURCE) is None


@pytest.mark.parametrize('copy',[
 '林园：这些公司有长期机会，不会有什么问题',
 '这些公司长期机会明确',
 '林园：反正和这些有关系的都要拿着',
])
def test_real_opaque_objects_are_not_self_contained(copy):
    assert T.unresolved_subject_error(copy,copy)


@pytest.mark.parametrize('copy',[
 '林园：这些医药公司业绩增长',
 '林园：我不买看不懂的公司',
 '医药企业的长期机会',
])
def test_concrete_objects_remain_allowed(copy):
    assert T.unresolved_subject_error(copy,copy) is None


def test_age_group_is_not_total_population():
    source='人口因为年龄越来越大，他消费的量越来越大。六十岁以上的老人会达到接近五亿。'
    assert T.population_scope_error('林园：医药行业消费量将随人口增长扩大','医药消费量随人口增长扩大',source)
    for copy in ['医药消费量随老年人口增长扩大','林园：年龄越大，医药消费量越大']:
        assert T.population_scope_error(copy,copy,source) is None
    assert T.population_scope_error('林园：人口增长带来更多消费','人口增长带来更多消费',source+'总人口持续增长。') is None


def test_actual_9b_cannot_turn_nonparticipation_into_exit():
    title='林园：因有人说是光伏污染大且自己未研究所以退出'
    cover='未研究仅听说不参与光伏能源'
    assert T.participation_phase_error(title,cover,SOURCE)
    item=dict(title=title,cover_title=cover,subject='光伏',evidence=[SOURCE])
    proof=T._package(item,SOURCE,dict(method='cpu_text_review',appeal=4,
        reason='实际9B审核漏看退出暗示的参与经历',**{k:True for k in T.CHECKS}),[])['title_rewrite']
    assert '退出' in T.error(title,proof,SOURCE)
    assert T.participation_phase_error('林园：光伏能源我没研究也没参与','光伏我没研究也没参与',SOURCE) is None
    assert T.participation_phase_error('林园：我后来退出了','后来退出了','我没有参与光伏，后来退出了其他项目。') is None


def test_explicit_nonparticipation_can_support_personal_investment_choice():
    assert T.personal_action_error('林园：光伏我没参与','光伏我没有参与',['所以我们就没参与。']) is None
    assert T.personal_action_error('林园：我不投光伏能源','我不投光伏能源',['所以我们就没参与。']) is None
    assert T.personal_action_error('林园：我不投光伏能源','我不投光伏能源',['这是对环境污染很大。'])


def test_incremental_cost_must_read_full_source_not_just_model_chosen_cues():
    source='它利润的扩大不需要再去我去花钱来产生利润。这种企业就是我投小钱产大钱。'
    item=dict(title='林园：我投小钱产大钱，利润扩大不需要花钱',cover_title='小钱产大钱，利润不靠花钱',subject='利润',evidence=['这种企业就是我投小钱产大钱。'])
    proof=T._package(item,source,dict(method='cpu_text_review',appeal=4,
        reason='实际32号省掉追加投入的范围，所选证据不含限定',**{k:True for k in T.CHECKS}),[])['title_rewrite']
    assert '追加' in T.error(item['title'],proof,source)


@pytest.mark.parametrize('copy',['心血管糖尿病药企不亏','买入医药股不会亏','坚持买入指数保本'])
def test_choice_correctness_cannot_become_no_loss(copy):
    source='把方向定下来以后，你就买这个指数，都是不会错的。心血管，然后糖尿病，反正与心血管有关系的，这个你都不会错。'
    title='林园：定方向后买指数坚持，心血管糖尿病药企不会错。'
    item=dict(title=title,cover_title=copy,subject='方向',evidence=[source])
    proof=T._package(item,source,dict(method='cpu_text_review',appeal=4,
        reason='实际68号9B把方向判断压成了不亏，独立审核错误通过',**{k:True for k in T.CHECKS}),[])['title_rewrite']
    assert '损益判断' in T.error(title,proof,source)
    assert '损益判断' in T._candidate_error(item,source,"林园",[],check_layout=False)


def test_source_stated_loss_claim_still_goes_to_normal_review():
    assert T.loss_claim_error('林园：这个方向不会错','心血管相关企业不会错','这个方向不会错。') is None
    assert T.loss_claim_error('林园：我那次没赚也不亏','那次不亏','我那次没赚也不亏。') is None
