"""Uncached source selection must not depend on an Ollama response."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import produce_cn as p
from source_selection import select
from live_tracking import frame_interval


def dialogue():
    return [dict(start=0,end=10,text='您对医药股有什么判断？'),
        dict(start=10,end=55,text='医药行业需求随着老龄化增长，我们长期持有这些企业。'),
        dict(start=55,end=100,text='但是投资仍然有风险，价格和需求都要看，不能只看过去。'),
        dict(start=100,end=145,text='企业产品有需求，投资才有长期增长的基础。'),
        dict(start=145,end=150,text='您对科技股怎么看？')]


def test_real_question_and_answer_keep_every_condition_and_timestamp(tmp_path,monkeypatch):
    cues=dialogue(); before=json.dumps(cues,ensure_ascii=False)
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST','true')
    with patch.object(p,'llm',side_effect=AssertionError('model must not be required')):
        picks=p.pick_highlights(cues,'林园','',tmp_path)
        assert [(r['start'],r['end']) for r in picks]==[(0,3)]
    # Selection survives an outage, but unknown speaker attribution must not
    # turn into an automatically approved source-quote title.
    with patch.object(p,'llm',side_effect=p.LocalTextUnavailable('temporarily unavailable')):
        with pytest.raises(p.LocalTextUnavailable):
            p.copywrite(cues,list(range(4)),'林园','访谈','',tmp_path)
    assert json.dumps(cues,ensure_ascii=False)==before
    assert '但是投资仍然有风险' in ''.join(c['text'] for c in cues[:4])


def test_short_answer_cannot_borrow_next_question_to_reach_120():
    cues=dialogue();cues[3]['end']=115;cues[4]['start']=115
    assert select(cues)==[]


def test_real_source32_host_recap_cannot_end_the_previous_answer():
    from source_selection import boundary_error
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_source32_host_recap.json').read_text())
    cues=data['cues']; original=json.dumps(cues,ensure_ascii=False)
    picks=select(cues,limit=None,whole_source=True)
    assert [(p['start'],p['end']) for p in picks]==[(0,data['expected_previous_end'])]
    # The raw cue spans “西。啊。好的，刚刚说到消费”: do not guess a
    # timestamp inside it or cut the preceding “东西” word in half.
    assert cues[picks[0]['end']]['text']=='就投入小，产出大，而且就是一劳永逸。'
    assert cues[picks[0]['end']]['end']==708.92
    assert boundary_error(cues,dict(start=0,end=data['old_end']))
    assert boundary_error(cues,picks[0]) is None
    assert json.dumps(cues,ensure_ascii=False)==original


def test_host_recap_cannot_extend_short_answer_but_guest_recap_is_allowed():
    cues=dialogue()[:4]
    cues[-1]['end']=115
    cues += [dict(start=115,end=145,text='好的，刚刚说到消费，接下来聊一个新问题。'),
             dict(start=145,end=150,text='您对科技股怎么看？')]
    assert select(cues)==[]
    cues=dialogue();cues[2]['text']='我刚刚说到企业需求，现在把价格也讲清楚。'
    assert [(x['start'],x['end']) for x in select(cues)]==[(0,3)]


def test_real17_and72_cannot_borrow_host_tail_to_make_a_complete_clip(monkeypatch):
    import source_selection as S
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(S.editorial,'MIN_SECONDS',20)
    cases=json.loads((Path(__file__).parent/'fixtures/linyuan_source17_72_host_tail.json').read_text())
    for case in cases:
        cues=case['cues'];before=json.dumps(cues,ensure_ascii=False)
        assert S.boundary_error(cues,dict(start=0,end=len(cues)-1))
        picks=select(cues,limit=None,whole_source=True)
        if case['id']==17:
            assert [(p['start'],p['end']) for p in picks]==[(0,5)]
            assert cues[5]['end']==1376.44
            assert '好像有人给我说' in ''.join(c['text'] for c in cues[:6])
            assert S.boundary_error(cues,picks[0]) is None
        else:
            # Actual answer lasts <20s. Do not use next-topic host narration
            # to pad it into a successful video, or silently lower the floor.
            assert picks==[]
        assert json.dumps(cues,ensure_ascii=False)==before


def test_mechanical_chunk_end_is_not_an_answer_boundary():
    assert select(dialogue()[:-1])==[]
    assert select(dialogue()[:-1],whole_source=True)


def test_unfinished_asr_tail_and_unanswered_question_cannot_supply_duration():
    cues=dialogue()[:3]+[dict(start=100,end=160,text='但是如果企业的')]
    assert select(cues,whole_source=True)==[]


def test_known_third_party_audio_cannot_reuse_a_face_approval(tmp_path):
    bad='87e4dcea6b1292f184edb15188c38c4075a4fa94fc1b272ac2fc385f862faff1'
    with patch.object(p,'_file_sha256',return_value=bad),patch.object(p,'verified_source_evidence') as cache:
        report=p.run_source_quality_gate(Path('source.mp4'),tmp_path,'林园','')
        assert report['passed'] is False and report['retryable'] is False
        cache.assert_not_called()
        with pytest.raises(p.VisualQualityError,match='王红'):
            p.load_source_quality_report(Path('source.mp4'),tmp_path/'source_quality.json')
    assert '王红' in p.editorial.metadata_error(dict(speaker='林园',source_sha256=bad))


def test_seek_and_eof_use_same_grid_without_padding():
    assert frame_interval(27.2,275.08,25,7556)==(680,6876)
    with pytest.raises(ValueError,match='短'):
        frame_interval(27.2,280,25,7556)


def test_actual_mp4_decodes_exactly_the_planned_frames(tmp_path):
    import subprocess,shutil
    if not shutil.which('ffmpeg'):pytest.skip('Media test runs after ffmpeg installation')
    cv2=pytest.importorskip('cv2')
    video=tmp_path/'rounding.mp4'
    subprocess.run(['ffmpeg','-loglevel','error','-f','lavfi','-i',
        'testsrc2=size=64x64:rate=25:duration=1','-c:v','libx264','-y',str(video)],check=True)
    cap=cv2.VideoCapture(str(video))
    first,count=frame_interval(.2,.82,cap.get(cv2.CAP_PROP_FPS),round(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    cap.set(cv2.CAP_PROP_POS_FRAMES,first)
    assert sum(cap.read()[0] for _ in range(count))==count
    assert not cap.read()[0]
    cap.release()


def test_interviewer_clauses_cannot_shed_question_context_for_a_title():
    from headline_policy import title_candidates
    text='呃，您看好三种药品，然后你也聊聊原因，是因为受中国人口老龄化的影响嘛。我们看好的企业必须有持续增长的需求。'
    titles=title_candidates(text)
    assert titles and all('老龄化' not in title for title in titles)
    assert p.editorial.title_attribution_error('林园：然后你也聊聊到说看好这三个药品的原因')


@pytest.mark.parametrize('section',['topic_changes','next_question_preamble','host_outro'])
def test_actual_686_cannot_borrow_other_topics_questions_or_outro(section):
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_686_selection.json').read_text())
    assert select(data['sections'][section],whole_source=True)==[]


@pytest.mark.parametrize('section',['topic_changes','next_question_preamble','host_outro'])
def test_model_fallback_cannot_reintroduce_actual_686_bad_ranges(section):
    from source_selection import boundary_error
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_686_selection.json').read_text())
    cues=data['sections'][section]
    # Exact failed endings, before the next question/answer in the fixture.
    end={'topic_changes':48,'next_question_preamble':40,'host_outro':len(cues)-1}[section]
    assert boundary_error(cues,dict(start=0,end=end))


def test_complete_followup_answer_remains_allowed():
    from source_selection import boundary_error
    cues=dialogue()[:4]+[
        dict(start=145,end=155,text='这个需求我完全认同。'),
        dict(start=155,end=165,text='您怎么看支付能力的问题？'),
        dict(start=165,end=180,text='人口基数大，支付能力也需要综合考虑。')]
    assert boundary_error(cues,dict(start=0,end=len(cues)-1)) is None


def test_model_proposed_686_cross_topic_range_never_enters_render(tmp_path,monkeypatch):
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_686_selection.json').read_text())
    cues=data['sections']['topic_changes']
    monkeypatch.delenv('SOURCE_EDITORIAL_FIRST',raising=False)
    bad=json.dumps(dict(picks=[dict(start=0,end=48,score=9,reason='model candidate')]))
    with patch.object(p,'llm',side_effect=[bad,'{"picks":[]}']) as llm, \
            patch.object(p,'pick_argument_context',return_value=[]):
        assert p.pick_highlights(cues,'林园','',tmp_path,allow_empty=True)==[]
        assert llm.call_count==2


def test_informal_you_question_is_a_boundary_too():
    cues=dialogue()
    cues[0]['text']='你对医药股有什么判断？'
    cues[4]['text']='你对科技股怎么看？'
    assert [(x['start'],x['end']) for x in select(cues)]==[(0,3)]


def test_guest_example_is_not_a_new_interviewer_question():
    from source_selection import QUESTION
    assert not QUESTION.search('有这个产品，你比如说我们医医这个医药公司，它如果说是营业额，有比如说我们它销售额有一百亿，是吧？')
    cues=dialogue()
    cues[2]['text']='你比如说这个企业有一百亿销售额，是吧？'
    assert [(x['start'],x['end']) for x in select(cues)]==[(0,3)]


def test_actual_685_accepted_answer_survives_boundary_fixes():
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_685_selection.json').read_text())
    picks=select(data['cues'],whole_source=True)
    assert [{k:p[k] for k in ('start','end')} for p in picks]==[data['expected_pick']]


def test_source77_explicit_keynote_chapters_are_not_one_oversized_speech():
    # Literal chapter announcements from fixed source100 #77.  The timestamps
    # preserve the observed source intervals; no 120/180-second cut is used.
    cues=[
        dict(start=662.84,end=669.64,text='那么投资啊，就是我今天讲第一最重要的，就是投垄断。'),
        dict(start=669.64,end=780.0,text='所有的竞争都能造成风险，独家生意能够减少竞争带来的风险。'),
        dict(start=780.0,end=870.36,text='今天我就讲，所以这个垄断是很重要的，独家买卖最好是独家买卖。'),
        dict(start=875.32,end=885.56,text='那么第二个，我们投资要赚大钱的话，就是投资未来的大行业。'),
        dict(start=885.56,end=1903.96,text='方向不能错，选择长期有需求的行业。'),
        dict(start=1905.48,end=1908.12,text='接下来就是我们要谈成长性。'),
        dict(start=1908.12,end=2010.0,text='成长不是短期热闹，行业选择和持续需求必须连在一起。'),
        dict(start=2010.0,end=2103.8,text='从行业选择和成长性的角度，要用常识判断长期空间。'),
        dict(start=2106.44,end=2109.56,text='好了，接下来我就讲这个投资。'),
        dict(start=2109.56,end=2410.0,text='投资还要讲买入和长期坚持。'),
    ]
    picks=select(cues,limit=None,whole_source=True)
    assert [(cues[p['start']]['start'],cues[p['end']]['end']) for p in picks]==[
        (662.84,870.36),(1905.48,2103.8),(2106.44,2410.0)]


def test_actual_686_interviewer_stock_claim_cannot_be_guest_title():
    from headline_policy import title_candidates
    text=('二零二一年你在茅台股东大会上面透露过，'
          '你有持有茅台百分之二的股票，当时占定总资产的百分之四十。'
          '医药行业需求随着老龄化增长，我们长期持有这些企业。')
    titles=title_candidates(text)
    assert titles and all('百分之' not in title and '茅台' not in title for title in titles)


def test_sep23_real_short_speeches_keep_their_original_opening_and_end(monkeypatch):
    import source_selection as S
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(S.editorial,'MIN_SECONDS',20)
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_sep23_selection.json').read_text())
    for case in data:
        cues=case['cues'];before=json.dumps(cues,ensure_ascii=False)
        picks=S.select(cues,limit=None,whole_source=True)
        end=len(cues)-2 if case['id']==308 else len(cues)-1
        assert any(r['start']==0 and r['end']==end for r in picks), (case['id'],picks)
        assert json.dumps(cues,ensure_ascii=False)==before
        assert all(not S.boundary_error(cues,r) for r in picks)
    # 306 is an uninterrupted 332-second speech, just beyond the old 330 cap.
    case=next(r for r in data if r['id']==306)
    assert case['cues'][-1]['end']-case['cues'][0]['start']>330
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','legacy120')
    assert not S.select(case['cues'],limit=None,whole_source=True)


def test_seller_cta_is_not_added_to_our_video_or_confused_with_discussion():
    from source_selection import promotional_cta,boundary_error
    assert promotional_cta('林园炒股秘籍，下面小黄车有售。')
    assert not promotional_cta('比如，有人说小黄车有售。')
    assert not promotional_cta('我不想买小黄车里那些商品。')
    cues=[dict(start=0,end=30,text='我们只投资自己能看懂的公司。'),
          dict(start=31,end=34,text='林园炒股秘籍，下面小黄车有售。')]
    assert '带货' in boundary_error(cues,dict(start=0,end=1))


def test_self_question_needs_its_answer_and_named_topic_in_opening():
    from source_selection import contextual_self_answer
    cues=[dict(start=0,end=3,text='这个行业现在是不是牛市？是牛市。'),dict(start=4,end=9,text='这里讲的是AI这个行业。')]
    units=[dict(start=i,end=i,text=c['text']) for i,c in enumerate(cues)]
    assert contextual_self_answer(units,cues,0)
    assert not contextual_self_answer([{**units[0],'text':'这个行业现在是不是牛市？'},units[1]],cues,0)
    assert not contextual_self_answer([units[0]],cues,0)


def test_real_library314_tariffs_and_ai_are_independent_complete_candidates(monkeypatch):
    import source_selection as S
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(S.editorial,'MIN_SECONDS',20)
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_library314_selection.json').read_text())
    cues=data['cues'];before=json.dumps(cues,ensure_ascii=False)
    ranges=[(cues[r['start']]['start'],cues[r['end']]['end']) for r in S.select(cues,limit=None,whole_source=True)]
    assert (366.44,513.72) in ranges
    assert (515.32,639.48) in ranges
    assert not any(a<515.32<b for a,b in ranges)
    assert S.boundary_error(cues,dict(start=123,end=206))
    assert S.boundary_error(cues,dict(start=170,end=206)) is None
    assert json.dumps(cues,ensure_ascii=False)==before


def test_sector_mentions_and_same_sector_followups_do_not_create_chapters():
    from source_selection import declared_investment_sections,sentence_units
    for first,second,last in [
        ('医药行业值得长期研究。','医药啊，我只投资能看懂的公司。','我们要长期观察。'),
        ('关税影响产能。','比如人工智能啊，我不敢投。','这是一个举例。'),
        ('关税影响产能。','人工智能啊，提高了生产效率。','我们观察技术进步。')]:
        cues=[dict(start=i*10,end=i*10+9,text=t) for i,t in enumerate([first,second,last])]
        assert not declared_investment_sections(sentence_units(cues),cues)


def test_automatic_preview_next_topic_tail_is_cut_at_original_cue(monkeypatch):
    import source_selection as S
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(S.editorial,'MIN_SECONDS',20)
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_automatic_topic_tail.json').read_text())
    cues=data['cues'];before=json.dumps(cues,ensure_ascii=False)
    assert '换题' in S.boundary_error(cues,dict(start=0,end=len(cues)-1))
    picks=S.select(cues,limit=None,whole_source=True)
    assert [(x['start'],x['end']) for x in picks]==[(0,31)]
    assert cues[31]['end']==280.44
    assert S.boundary_error(cues,picks[0]) is None
    assert json.dumps(cues,ensure_ascii=False)==before


@pytest.mark.parametrize('subject',['财政政策','企业招聘','孩子教育','日常饮食'])
def test_next_topic_leadin_is_independent_of_subject(subject):
    from source_selection import TOPIC_CHANGE
    assert TOPIC_CHANGE.search('好的，那其实啊，我们还是想啊，接着来聊一下'+subject+'。')


@pytest.mark.parametrize('text',[
    '我们接着聊一下这个话题。','咱们下面谈这一点。',
    '好的，我们接下来讨论刚才的问题。','我们接着聊同一个问题。'])
def test_explicit_same_topic_continuation_is_retained(text):
    from source_selection import TOPIC_CHANGE
    assert not TOPIC_CHANGE.search(text)
