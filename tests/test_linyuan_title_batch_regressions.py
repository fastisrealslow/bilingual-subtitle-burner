"""Real Sep19 copy failures: gate both rewritten copy and quote fallback."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import headline_policy as H
import title_rewrite as T

@pytest.mark.parametrize('title',[
    '林园：是应该是也有规则，人家告诉你',
    '林园：你首先买买的，我用我的价值观，我觉得这个有风险',
    '林园：一个是当下你的买买入的成本',
    '林园：都是直接给我们带来的是一些回报',
    '林园：炒股是钱拿来炒，是对人人体的磨练。',
    '林园：我没有没有特意去研究，但是我好像有人给我说是光伏光伏能源这一块',
])
def test_real_fragments_cannot_pass_either_copy_path(title):
    assert not H.complete(H.body(title))
    assert T.copy_fragment(title)
    source=H.body(title)
    proof=T._package(dict(title=title,cover_title=source,evidence=[source],subject=source),
        source,dict(method='source_quote',quote=source),[])['title_rewrite']
    assert T.error(title,proof,source)

@pytest.mark.parametrize('title',[
    '林园：认知不够的行业，再好我也不碰',
    '林园：医药股经营不好，我就不买',
    '林园：买入股票之前先看经营情况',
    '林园：不卖，一股都不卖',
    '林园：人人都要为自己的投资负责',
    '林园：大家都知道分红重要',
])
def test_complete_conversational_judgments_remain_allowed(title):
    assert not H.verbal_fragment(title)
    assert not T.copy_fragment(title)


def test_quote_fallback_cannot_drop_researched_company_scope(monkeypatch):
    source='我们研究的公司，医药公司业绩增长，股价却在下跌。'
    title='林园：医药公司业绩增长，股价却在下跌'
    monkeypatch.setattr(H,'title_candidates',lambda *a,**k:[title])
    monkeypatch.setattr(H,'cover_copy',lambda *a,**k:dict(text='医药公司业绩增长',kind='quote'))
    with pytest.raises(ValueError,match='未提炼出'):
        T._extractive(source,'林园',())
    quote=H.body(title)
    proof=T._package(dict(title=title,cover_title='医药公司业绩增长',evidence=[quote],subject=quote),
        source,dict(method='source_quote',quote=quote),[])['title_rewrite']
    assert '研究公司范围' in T.error(title,proof,source)


def test_complete_participation_verb_is_not_a_dangling_conjunction():
    assert not T.copy_fragment('林园：光伏能源我没有研究，也没有参与')
    assert H.complete('光伏能源我没有研究，也没有参与')
    assert T.copy_fragment('林园：光伏与')
    assert not H.complete('光伏与')


def test_source_abbreviation_is_an_evidence_anchor_not_an_invented_object():
    units=['光伏能源是这样的，就是我没有没有特意去研究。']
    subjects=T.subject_catalog(units)
    assert subjects['光伏']==[0]
    assert '风电' not in subjects


def test_actual_9b_repeated_source_noun_is_not_a_publishable_title():
    source='光伏污染大是我听说的，我没有研究过，所以我们没参与。'
    item=dict(title='林园：光伏光伏污染大是听说的，因此我们没参与',
        cover_title='听说光伏污染大所以没参与',subject='光伏',evidence=[source])
    assert '重复' in T._candidate_error(item,source,'林园',[])
    assert T._candidate_error({**item,'title':'林园：光伏污染大是听说的，所以我没参与'},source,'林园',[]) is None


@pytest.mark.parametrize('copy',[
    '林园：我没买，因为我觉得它不符合我的标准。',
    '你找这原因那原因，其实都不是原因',
])
def test_actual_all_output_drafts_need_a_concrete_object(copy):
    assert T.unresolved_subject_error(copy,copy)


def test_abstract_words_cannot_supply_missing_investment_object():
    units=['没买，因为我觉得它不符合我的标准。','中石油的产品可能被替代。']
    catalog=T.subject_catalog(units)
    assert '标准' not in catalog and '石油' in catalog
    assert T.unresolved_subject_error('林园：我没买中石油','我没买中石油') is None
    bound=T.bind_candidate(dict(title='林园：我没买中石油',cover_title='林园没买中石油'),
        dict(evidence_ids=[0,1]),units,catalog)
    assert bound['cover_title']=='没买中石油'
    assert bound['subject']=='石油'


def test_real_all_output_growth_cover_cannot_discard_belief():
    title='林园：我相信未来眼科牙科会有爆发性增长'
    source='我相信未来眼科牙科会有爆发性增长。'
    for cover in ['眼科牙科爆发性增长','眼科牙科未来爆发性增长']:
        item=dict(title=title,cover_title=cover,subject='眼科',evidence=[source])
        assert '判断语气' in T._candidate_error(item,source,'林园',[],check_layout=False)
        proof=T._package(item,source,dict(method='cpu_text_review',appeal=5,
            reason='真实模型把个人预期误审为事实',**{k:True for k in T.CHECKS}),[])['title_rewrite']
        assert '判断语气' in T.error(title,proof,source)
    assert T.cover_qualifier_error(title,'我相信眼科牙科会爆发性增长') is None
    assert T.cover_qualifier_error(title,'眼科牙科会不会爆发性增长？') is None
    assert T.cover_qualifier_error('林园：眼科牙科已经增长','眼科牙科已经增长') is None


def test_actual_second_growth_trial_cannot_drop_belief_from_both_fields():
    source='这这都是两个大行业，我相信未来这两个行业还是会有爆发性增长。'
    title='林园：眼科牙科未来爆发性增长'
    cover='眼科牙科未来爆发性增长'
    assert '判断语气' in T.forecast_copy_error(title,cover,source)
    item=dict(title=title,cover_title=cover,subject='眼科',evidence=[source])
    assert '判断语气' in T._candidate_error(item,source,'林园',[],check_layout=False)
    assert T.forecast_copy_error('我相信眼科牙科未来会有爆发性增长',
                                '眼科牙科会不会爆发性增长？',source) is None
    # A different historical claim must not acquire an invented hedge.
    assert T.forecast_copy_error('营收已经增长','营收已经增长',source) is None
    assert T.forecast_copy_error(title,cover,'这两个行业已经出现爆发性增长。') is None
