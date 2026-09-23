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
