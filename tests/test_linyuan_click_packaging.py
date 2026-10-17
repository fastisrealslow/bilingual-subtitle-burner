"""Published copy defects, conservative recovery and bound fallback review."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import headline_policy as H
import title_rewrite as T
import presentation as P


@pytest.mark.parametrize('title',[
    '林园：大家说步步过山是一定这样，但有限时间里没有当年领先最后都会慢慢变',
    '林园：腾讯和茅台，因为我对腾讯我不了解',
    '林园：股市不适合我赚赚，但我预测的最坏结果还是没破',
    '林园：嗯，医药股我没有没有研究',
])
def test_published_noise_fails_draft_quote_and_cached_proof(title):
    assert H.readability_error(title)
    assert T.copy_fragment(title)
    quote=H.body(title)
    item=dict(title=title,cover_title='医药股要看经营',subject='医药股',evidence=[quote])
    for method in ('source_quote','cpu_text_review'):
        review=dict(method=method,quote=quote,appeal=5,reason='测试旧证明全部通过也不能绕过新的可读性规则',
                    **{k:True for k in T.CHECKS})
        proof=T._package(item,quote,review,[])['title_rewrite']
        assert T.error(title,proof,quote)


@pytest.mark.parametrize('title',[
    '林园：不卖，一股都不卖','林园：人人都要为自己的投资负责',
    '林园：医药股经营不好，我就不买','林园：我不懂腾讯，茅台我更了解',
    '林园：大家都知道分红重要','林园：步步高的产品要亲自体验',
])
def test_meaningful_speech_is_not_noise(title):
    assert not H.readability_error(title)
    assert not T.copy_fragment(title)


def test_noisy_source_can_recover_another_unchanged_complete_quote():
    source='大家说步步过山是一定这样。医药股经营不好，我就不买。'
    result=T._extractive(source,'林园',())
    assert '步步过山' not in result['title']
    assert H.body(result['title']) in source
    assert T.error(result['title'],result['title_rewrite'],source) is None


def test_fallback_reviews_alternatives_once_and_binds_the_accepted_copy(monkeypatch):
    source='医药股经营不好，我就不买。买入股票之前先看经营情况。'
    titles=['林园：医药股经营不好，我就不买','林园：买入股票之前先看经营情况']
    monkeypatch.setattr(H,'title_candidates',lambda *a,**k:titles)
    monkeypatch.setattr(H,'quote_candidates',lambda *a,**k:[])
    monkeypatch.setattr(H,'cover_copy',lambda title,*a,**k:dict(text=H.body(title),kind='quote'))
    calls=[]
    def reviewer(prompt,schema):
        calls.append(prompt)
        assert schema['properties']['reviews']['minItems']==2
        rows=json.loads(prompt.split('待检查文案：')[1].split('\n完整原文：')[0])
        return json.dumps(dict(reviews=[dict(index=r['index'],title_readable=r['title']==H.body(titles[1]),
            cover_readable=True,same_claim=True,reason='测试审核：第二句明确交代买入股票之前要看经营情况')
            for r in rows]),ensure_ascii=False)
    result=T._extractive(source,'林园',(),reviewer=reviewer)
    assert len(calls)==1 and result['title']==titles[1]
    proof=result['title_rewrite']
    assert T.error(result['title'],proof,source) is None
    changed=deepcopy(proof)
    changed['review']['readability_review']['cover_readable']=False
    assert T.error(result['title'],changed,source)
    changed=deepcopy(proof)
    changed['review']['readability_review']['copy_sha256']='0'*64
    assert T.error(result['title'],changed,source)


@pytest.mark.parametrize('rows',[[],[dict(index=0)],[dict(index=True)]])
def test_malformed_or_negative_review_is_not_approval(rows):
    with pytest.raises(ValueError,match='备用标题'):
        T._extractive('医药股经营不好，我就不买。','林园',(),
                      reviewer=lambda *a:json.dumps(dict(reviews=rows)))


def test_structured_service_timeout_does_not_publish_unreviewed_quotes():
    calls=[]
    def offline(*a):
        calls.append(a)
        error=RuntimeError('bounded timeout')
        error.retryable_service=True
        raise error
    with pytest.raises(RuntimeError,match='bounded timeout'):
        T.generate('医药股经营不好，我就不买。',structured_model=offline)
    assert len(calls)==1


def test_short_intact_quote_is_available_without_deleting_a_contrast(monkeypatch):
    source='我首先不愿意占别人便宜，但是我也绝对不吃亏，我是一个不吃亏的人。'
    def review(prompt,schema):
        options=json.loads(prompt.split('待检查文案：')[1].split('\n完整原文：')[0])
        return json.dumps(dict(reviews=[dict(index=r['index'],
            title_readable=r['title']=='我是一个不吃亏的人',cover_readable=True,same_claim=True,
            reason='测试审核：短原句完整表达自己不吃亏，没有删除转折后半句') for r in options]))
    result=T._extractive(source,'林园',(),reviewer=review)
    assert result['title']=='林园：我是一个不吃亏的人'
    assert result['cover_title']=='我是一个不吃亏的人'


def test_short_quote_expansion_cannot_bypass_already_published_title_dedup(monkeypatch):
    monkeypatch.setattr(H,'title_candidates',lambda *a,**k:[])
    def no_candidates(*a):
        pytest.fail('A duplicate short title must never be sent for approval')
    with pytest.raises(ValueError,match='未提炼出'):
        T._extractive('我是一个不吃亏的人。','林园',['林园：我是一个不吃亏的人'],reviewer=no_candidates)


def test_actual_source39_whole_short_sentence_reaches_bounded_review():
    corpus=Path(__file__).resolve().parents[1]/'linyuan/simulations/benchmark-20260921/all-current-title-corpus.json'
    source=''.join(c['text'] for c in json.loads(corpus.read_text())[6]['cues'])
    def review(prompt,schema):
        rows=json.loads(prompt.split('待检查文案：')[1].split('\n完整原文：')[0])
        assert len(rows)<=3
        assert any(r['title']=='我是一个不吃亏的人' for r in rows)
        return json.dumps(dict(reviews=[dict(index=r['index'],
            title_readable=r['title']=='我是一个不吃亏的人',cover_readable=True,same_claim=True,
            reason='测试真实原文：完整短句表达个人不吃亏的态度，没有拼接或删去条件') for r in rows]))
    assert T._extractive(source,'林园',(),reviewer=review)['title']=='林园：我是一个不吃亏的人'


@pytest.mark.parametrize('cover',[
    '小钱产大钱，利润不靠追加投资','云南白药是唯一关注点',
    '能做长久的生意才是好买卖','当前市场还没进入牛市',
])
def test_cover_breaks_keep_the_words_and_large_type_capacity(cover):
    lines=P.cover_headline(cover,max_lines=3,line_capacity=6)
    assert ''.join(lines)==H.compact(cover)
    assert len(lines)<=3 and max(map(len,lines))<=6
    assert not any(line.endswith(('不靠','是','的')) for line in lines[:-1])


def test_short_cover_does_not_trade_two_readable_lines_for_a_three_line_modifier_fragment():
    assert P.cover_headline('云南白药是唯一关注点',max_lines=3,line_capacity=5,min_line_chars=2)==[
        '云南白药是','唯一关注点']
