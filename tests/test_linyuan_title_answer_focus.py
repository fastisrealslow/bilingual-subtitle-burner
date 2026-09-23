from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T


def test_actual_display_line_breaks_do_not_hide_the_subject_of_a_claim():
    import json
    path=Path(__file__).resolve().parents[1]/'linyuan/simulations/benchmark-20260921/content-stage-corpus.json'
    cases=json.loads(path.read_text())
    for case in cases:
        cues=[c['text'] for c in case['cues']]
        units=T.answer_reading_units(cues)
        assert ''.join(units)==''.join(cues)
    case=next(r for r in cases if r['id']=='sep23-source32')
    cues=[c['text'] for c in case['cues']]
    units=T.answer_reading_units(cues)
    assert ''.join(cues[5:8]) in units
    assert ''.join(cues[:2]) in units
    assert len(units)<len(cues)


def test_grouping_keeps_questions_and_named_other_speakers_out_of_guest_evidence():
    cues=['请问王总，您怎么看这个行业，','是否还会有空间？',
          '我认为这个行业以后还有很大发展空间。',
          '请问林总，您觉得现在进入牛市了吗？',
          '这个位置应该是不高，','还没有进入牛市。']
    units=T.answer_reading_units(cues)
    from speaker_attribution import other_guest_indices
    blocked=other_guest_indices(units)
    assert not set(T.guest_evidence_ids(units,['guest']*len(units)))&blocked
    assert cues[-2]+cues[-1] in units
    assert not any(cues[3] in u and cues[4] in u for u in units)


def test_question_end_inside_display_cue_does_not_swallow_guest_rebuttal():
    cues=['这这一点是怎么做到的呢？这你说的这个事儿啊，',
          '牛熊日，这这动洞察先机，我也洞察不了。']
    units=T.answer_reading_units(cues)
    assert units==['这这一点是怎么做到的呢？',
                  '这你说的这个事儿啊，牛熊日，这这动洞察先机，我也洞察不了。']
    assert ''.join(units)==''.join(cues)
    roles=T.bind_reading(dict(a_guest_answer='嘉宾说自己也无法洞察牛熊先机，没有确认主持人的前提。',
        b_question_premise='主持人问如何提前洞察熊市。',
        c_sentence_roles=dict(u0000='host',u0001='guest')),units)
    assert T.bind_answer_focus(dict(d_main_answer_quote=units[1]),units,roles)==[1]


def test_complete_short_main_answer_is_evidence_but_list_tail_is_not():
    units=['您觉得进入牛市了吗？','这个位置应该是不高。','还没有进入牛市。','还有一个','嗯']
    assert T.guest_evidence_ids(units,['host','guest','guest','guest','guest']) == [1,2]
    assert T.evidence_usable('还没有进入牛市。')
    assert not T.evidence_usable('还有一个')
    assert not T.evidence_usable('不是。')  # needs its predicate/context
    assert T.guest_evidence_ids(units,['host','unknown','unknown','guest','guest']) == []


def test_real8_main_answer_is_not_filtered_out_before_the_writer():
    import json
    path=Path(__file__).resolve().parents[1]/'linyuan/simulations/benchmark-20260921/content-stage-corpus.json'
    case=next(r for r in json.loads(path.read_text()) if r['id']=='sep23-source8')
    units=[c['text'] for c in case['cues']]
    roles=['host']*7+['guest']*25+['host']*3+['guest']*7
    ids=T.guest_evidence_ids(units,roles)
    assert 7 in ids and 10 in ids
    assert not set(range(7)) & set(ids)
    assert not {32,33,34} & set(ids)


def test_main_ids_are_bound_to_guest_and_cannot_be_replaced_by_side_details():
    units=['您觉得进入牛市了吗？','这个位置应该是不高。','还没有进入牛市。','旁边有另一位嘉宾。']
    roles=['host','guest','guest','unknown']
    assert T.bind_answer_focus({'d_main_answer_quote':''.join(units[1:3])},units,roles)==[1,2]
    for quote in (units[0],units[3], '这个位置很低，已经进入牛市。', True, '', [1,2]):
        with pytest.raises(ValueError):T.bind_answer_focus({'d_main_answer_quote':quote},units,roles)
    assert T.require_answer_focus({'evidence_ids':[1,7]},[1,2])
    with pytest.raises(ValueError):T.require_answer_focus({'evidence_ids':[7]},[1,2])


def test_optional_reader_runs_before_drafts_and_keeps_default_schema():
    old=T.reading_schema(10)
    assert 'd_main_answer_quote' not in old['properties']
    new=T.reading_schema(10,answer_focus=True)
    assert list(new['properties'])==['a_guest_answer','b_question_premise','c_sentence_roles','d_main_answer_quote']
    assert new['properties']['d_main_answer_quote']['type']=='string'


def test_real_setup_is_not_accepted_as_the_main_answer():
    setup='那么我就详细就讲一下，就是为什么我们说今天这是个打击。'
    answer='今天这个位置就是不挣钱，但是值得投资的。'
    units=[setup,answer]
    assert T.main_answer_quotes(units)==[answer]
    with pytest.raises(ValueError,match='开场预告'):
        T.bind_answer_focus(dict(d_main_answer_quote=setup),units,['guest','guest'])
    assert T.bind_answer_focus(dict(d_main_answer_quote=answer),units,['guest','guest'])==[1]
    assert not T.answer_setup('我先讲一下我的选择，我没有买入这家公司。')


def test_standalone_market_copy_cannot_only_say_this_position():
    ambiguous='今天这个位置不挣钱但值得投资'
    assert '位置' in T.unresolved_subject_error('林园：'+ambiguous,ambiguous)
    assert T.unresolved_subject_error('林园：我觉得A股这个位置值得投资','A股这个位置值得投资') is None
    assert T.unresolved_subject_error('林园：这个位置不高，还没进入牛市','还没进入牛市') is None


def test_each_sentence_must_be_attributed_without_guessing_omissions():
    units=['眼科牙科的投资价值如何？','这两个行业我相信还有增长空间。','这句话归属不清。']
    reading=dict(a_guest_answer='嘉宾相信眼科牙科这两个行业还有增长空间。',
        b_question_premise='主持人问眼科和牙科。',
        c_sentence_roles=dict(u0000='host',u0001='guest',u0002='unknown'))
    roles=T.bind_reading(reading,units)
    assert T.guest_evidence_ids(units,roles)==[1]
    for mapping in (dict(u0000='host',u0001='guest'),dict(u0000='host',u0001='guest',u0002=True),
                    dict(u0000='host',u0001='guest',u0002='unknown',u0003='guest')):
        with pytest.raises(ValueError):T.bind_reading({**reading,'c_sentence_roles':mapping},units)


def test_main_quote_can_include_short_continuations_but_cannot_bridge_host_or_unknown():
    units=['它利润的扩大不需要再去我去花钱，','来产生利润。']
    quote=''.join(units)
    assert not T.evidence_usable(units[1])
    assert T.bind_answer_focus({'d_main_answer_quote':quote},units,['guest','guest'])==[0,1]
    for role in ('host','unknown'):
        with pytest.raises(ValueError):
            T.bind_answer_focus({'d_main_answer_quote':quote},units,['guest',role])
    for role in ('host','unknown'):
        with pytest.raises(ValueError):
            T.bind_answer_focus({'d_main_answer_quote':quote},[units[0],'主持人追问。',units[1]],['guest',role,'guest'])


def test_real32_paragraph_tail_failure_is_replaced_by_a_bound_whole_claim():
    import json
    path=Path(__file__).resolve().parents[1]/'linyuan/simulations/benchmark-20260921/content-stage-corpus.json'
    case=next(r for r in json.loads(path.read_text()) if r['id']=='sep23-source32')
    units=[c['text'] for c in case['cues']]
    roles=['host']*2+['guest']*(len(units)-2)
    assert T.bind_answer_focus({'d_main_answer_quote':''.join(units[5:8])},units,roles)==[5,6,7]
    assert T.bind_answer_focus({'d_main_answer_quote':''.join(units[48:51])},units,roles)==[48,49,50]


def test_real8_quote_grammar_copies_actual_words_instead_of_recreating_them():
    import json
    path=Path(__file__).resolve().parents[1]/'linyuan/simulations/benchmark-20260921/content-stage-corpus.json'
    case=next(r for r in json.loads(path.read_text()) if r['id']=='sep23-source8')
    units=[c['text'] for c in case['cues']]
    quotes=T.reading_schema(len(units),True,units)['properties']['d_main_answer_quote']['enum']
    assert ''.join(units[7:11]) in quotes
    assert '目前这个位置应该还是不高，还没有进入牛市。' not in quotes
    assert all(q in ''.join(units) for q in quotes)
    assert all(4<=len(T.compact(q))<=160 for q in quotes)
    # Grammar candidacy is not speaker approval.
    host=next(q for q in quotes if '上车' in q)
    with pytest.raises(ValueError):
        T.bind_answer_focus({'d_main_answer_quote':host},units,['host']*7+['guest']*(len(units)-7))


def test_answer_profile_has_a_distinct_cache_identity(monkeypatch):
    import produce_cn as P
    monkeypatch.setenv('LINYUAN_TITLE_DRAFT_PROFILE','source_choices')
    old=P._copy_style_identity('林园')
    monkeypatch.setenv('LINYUAN_TITLE_DRAFT_PROFILE','answer_focus')
    assert P._copy_style_identity('林园')!=old


def test_focus_trial_never_falls_back_to_unreviewed_secondary_quote():
    import json
    def reader(prompt,schema):
        return json.dumps(dict(a_guest_answer='当前的位置应该是不高，还没有进入牛市。',
            b_question_premise='目前位置是不是牛市',c_guest_spans=[dict(a_start=0,b_end=0)],d_main_answer_quote='这是完全不存在的原文'))
    with pytest.raises(ValueError,match='不回退到旁枝'):
        T.generate('这个位置应该是不高，还没有进入牛市。',structured_model=reader,answer_focus=True)


def test_main_answer_survives_the_complete_generate_and_final_proof_path():
    import json
    units=['您觉得目前进入牛市了吗？','这个位置应该是不高。','还没有进入牛市。',
           '衡量牛市熊市，要看这个市场有多少人赚钱。','就下跌的部分那一块，占的总市值是大的。']
    title='林园：现在还没有进入牛市'
    stages=[]
    def model(prompt,schema):
        props=schema['properties']
        if 'c_sentence_roles' in props:
            stages.append('read')
            assert title not in prompt
            return json.dumps(dict(a_guest_answer='位置应该不高，目前还没有进入牛市，要看有多少人赚钱。',
                b_question_premise='主持人问目前是否进入牛市。',
                c_sentence_roles={f'u{i:04d}':'host' if i==0 else 'guest' for i in range(len(units))},
                d_main_answer_quote=''.join(units[1:3])))
        if 'c_candidates' in props:
            stages.append('write')
            assert '主要回答编号：[1, 2]' in prompt
            main=dict(title=title,cover_title='现在还没有进入牛市',
                a_focus=dict(a_claim='目前还没有进入牛市，要看赚钱的人数。',b_evidence_ids=[2,3]))
            side=dict(title='林园：下跌部分占市场总市值大',cover_title='下跌部分占总市值大',
                a_focus=dict(a_claim='下跌部分占的总市值是大的。',b_evidence_ids=[4]))
            return json.dumps(dict(c_candidates=[main,side,side]))
        stages.append('review')
        assert '主要回答编号' not in prompt
        assert '林园：下跌部分占市场总市值大' not in prompt
        return json.dumps(dict(reviews=[dict(a_analysis=dict(a_guest_answer='嘉宾认为目前尚未进入牛市，还要看多少人赚钱。',
            b_question_premise='主持人问目前是否进入牛市。',c_reason='现在还没有进入牛市对应原文明确否定，未新增市场点位或回报判断。'),
            b_verdict=dict(index=0,appeal=4,**{k:True for k in T.CHECKS}))]))
    result=T.generate(''.join(units),source_cues=units,structured_model=model,answer_focus=True)
    assert stages==['read','write','review']
    assert result['title']==title
    assert T.error(title,result['title_rewrite'],''.join(units)) is None
    assert result['answer_focus_reading']['exact_source']==units[1:3]
