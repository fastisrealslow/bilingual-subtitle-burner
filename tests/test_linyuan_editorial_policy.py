"""Prevent the actual short-video regression through all daily entry points."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import editorial_policy as policy
import produce_cn as produce


def complete_meta():
    return dict(duration_sec=150, segments=[dict(start=600,end=750)],
        editorial_review=dict(version=policy.VERSION,standalone_opening=True,
            complete_argument=True,reasoning_present=True,natural_ending=True,
            requires_audio_review=False,summary='观点与论据',transcript_sha256='abc'))


def test_real_short_mp4_cannot_pass_with_long_metadata():
    meta=complete_meta()
    assert policy.metadata_error(meta,150) is None
    assert '实际MP4不足120秒' in policy.metadata_error(meta,20)
    assert policy.metadata_error(meta,180) is not None


def test_disabled_model_review_is_explicit_and_still_enforces_source_integrity(tmp_path):
    cues=[dict(start=0,end=140,text='医药需求长期存在，因为人会衰老。')]
    with patch.object(produce,'llm',side_effect=AssertionError('disabled review called model')):
        record=produce.argument_record_for_render(cues,[dict(start=0,end=0)],
            '林园','',tmp_path,'')
    assert record['status']=='skipped' and 'complete_argument' not in record
    meta=dict(duration_sec=140,segments=[dict(start=0,end=140)],editorial_review=record)
    assert policy.metadata_error(meta,140) is None
    assert policy.metadata_error(meta,20) is not None
    assert policy.review_error({**record,'complete_argument':True}) is not None
    assert policy.review_error({**record,'transcript_sha256':''}) is not None
    import pytest
    cues[0]['text']='资本是足力的'
    with pytest.raises(produce.VisualQualityError,match='ASR污染'):
        produce.argument_record_for_render(cues,[dict(start=0,end=0)],'林园','',tmp_path,'')


def test_silence_padding_and_unrelated_splicing_do_not_satisfy_duration():
    meta=complete_meta()
    meta['segments']=[dict(start=600,end=620)]
    assert policy.metadata_error(meta,150) is not None
    meta['segments']=[dict(start=600,end=675),dict(start=900,end=975)]
    assert policy.metadata_error(meta,150) is not None


def test_source_bound_parenthetical_cut_still_needs_independent_meaning_review():
    source=next(iter(policy.REVIEWED_OMISSION_RANGES))
    ranges=policy.REVIEWED_OMISSION_RANGES[source]
    meta=complete_meta()
    duration=sum(b-a for a,b in ranges)
    meta.update(source_sha256=source,duration_sec=duration,
                segments=[dict(start=a,end=b) for a,b in ranges])
    assert policy.metadata_error(meta,duration) is not None
    meta['editorial_review'].update(review_protocol=3,omission_preserves_meaning=True,
        omitted_is_parenthetical=True,omitted_text_sha256='actual-removed-text-digest')
    assert policy.metadata_error(meta,duration) is None
    meta['source_sha256']='other-source'
    assert policy.metadata_error(meta,duration) is not None
    meta['source_sha256']=source
    meta['segments'][1]['start']+=5
    assert policy.metadata_error(meta,duration) is not None


def test_omission_reviewer_sees_removed_words_and_can_reject_the_edit(tmp_path):
    source=next(iter(policy.REVIEWED_OMISSION_RANGES))
    cues=[dict(start=938.6,end=1037.16,text='为什么医药需求增加。'),
          dict(start=1037.56,end=1043.56,text='但是这里有一个重要限制。'),
          dict(start=1044.44,end=1121.16,text='这是完整结论。')]
    picks=[dict(start=i,end=i,editorial_source_sha256=source) for i in (0,2)]
    answer=dict(standalone_opening=True,complete_argument=True,reasoning_present=True,
        natural_ending=True,requires_audio_review=False,summary='需求讨论',issues=[],
        opening_quote='为什么医药需求增加。',ending_quote='这是完整结论。',
        omission_preserves_meaning=False,omitted_is_parenthetical=False,omission_reason='不能删去必要限制')
    with patch.object(produce,'llm',return_value=json.dumps(answer,ensure_ascii=False)) as call:
        try:
            produce.review_complete_argument(cues,picks,'林园','test',tmp_path,'_7')
        except produce.VisualQualityError as exc:
            assert '剪除插语未通过' in str(exc)
        else:
            raise AssertionError('A rejected omission became publication approval')
    assert '[拟删插语]但是这里有一个重要限制。' in call.call_args.args[0][0]['content']
    assert json.loads((tmp_path/'editorial_review_7.json').read_text())['omission_preserves_meaning'] is False


def test_old_short_highlight_cache_is_not_reused():
    cues=[dict(start=i*30,end=(i+1)*30,text='这是完整论述。') for i in range(6)]
    with tempfile.TemporaryDirectory() as tmp:
        work=Path(tmp)
        (work/'highlights.json').write_text('[{"start":0,"end":0,"score":9}]')
        with patch.object(produce,'llm',return_value='[{"start":0,"end":4,"score":8,"reason":"完整论述"}]') as ask:
            picks=produce.pick_highlights(cues,'林园','test',work)
        assert ask.call_count==1
        assert policy.range_seconds(cues,picks[0])==150


def test_preselected_shortcut_rejected_before_copy_or_render():
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(produce,'copywrite') as copywrite:
            try:
                produce._produce_one(Path('source.mp4'),Path(tmp),Path(tmp),
                    [dict(start=0,end=20,text='完整一句，但过短。')],
                    '林园','采访','key',False,1280,720,'_1',
                    preselected_picks=[dict(start=0,end=0,score=9,reason='短句')])
            except ValueError as exc:
                assert '不足120秒' in str(exc)
            else:
                raise AssertionError('Preselected short clip escaped duration gate')
            copywrite.assert_not_called()


def test_short_model_picks_must_select_from_real_long_contexts():
    cues=[dict(start=i*30,end=(i+1)*30,text='这是这一观点的完整解释。') for i in range(10)]
    choices=produce.argument_context_candidates(cues,[dict(start=0,end=0)])
    verdicts={str(c['candidate_id']):'reject_low_value' for c in choices}
    verdicts['0']='accept_8'
    replies=['[{"start":0,"end":0,"score":8}]']*2+[json.dumps({'verdicts':verdicts})]
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(produce,'llm',side_effect=replies):
            picks=produce.pick_highlights(cues,'林园','test',Path(tmp))
    assert len(picks)==1 and policy.range_seconds(cues,picks[0])>=120
    assert picks[0]['end']>picks[0]['start']


def test_review_cannot_reject_source_by_copying_nonexistent_example_words(tmp_path):
    cues=[dict(start=0,end=150,text='医药需求随老龄化增长。这是我们的判断。')]
    valid=dict(standalone_opening=True,complete_argument=True,reasoning_present=True,
        natural_ending=True,requires_audio_review=False,summary='解释老龄化与需求的关系',issues=[],
        opening_quote='医药需求随老龄化增长。',ending_quote='这是我们的判断。')
    invalid={**valid,'issues':['生产效率大大不提高']}
    with patch.object(produce,'llm',side_effect=[json.dumps(invalid,ensure_ascii=False),json.dumps(valid,ensure_ascii=False)]):
        result=produce.review_complete_argument(cues,[dict(start=0,end=0)],'林园','key',tmp_path,'')
    assert result['issues']==[] and result['review_prompt_version']==6
    (tmp_path/'editorial_review.json').unlink()
    import pytest
    with patch.object(produce,'llm',return_value=json.dumps(invalid,ensure_ascii=False)):
        with pytest.raises(produce.EditorialReviewUnavailable):
            produce.review_complete_argument(cues,[dict(start=0,end=0)],'林园','key',tmp_path,'')


def test_title_generation_reads_the_entire_long_argument():
    cues=[dict(start=i*6,end=(i+1)*6,text='前面的解释。') for i in range(25)]
    cues[-1]['text']='长期持有才是我们一贯坚持的方法。'
    response='{"title":"林园：长期持有才是我们一贯坚持的方法","desc":"公开发言","tags":["林园"]}'
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(produce,'llm',return_value=response) as ask:
            produce.copywrite(cues,list(range(len(cues))),'林园','访谈','test',Path(tmp))
        assert cues[-1]['text'] in ask.call_args.args[0][0]['content']


def test_unresolved_negation_or_number_requires_audio_review():
    meta=complete_meta()
    meta['editorial_review']['requires_audio_review']=True
    assert policy.metadata_error(meta) is not None


def test_editor_cannot_approve_by_inventing_fluent_boundary_quotes():
    cues=[dict(start=0,end=150,text='这句有前提但原文没有完整结尾的')]
    reply=dict(standalone_opening=True,complete_argument=True,reasoning_present=True,
        natural_ending=True,requires_audio_review=False,summary='通顺摘要',issues=[],
        opening_quote='这句有前提',ending_quote='这是完整的结论')
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(produce,'llm',return_value=json.dumps(reply,ensure_ascii=False)):
            import pytest
            with pytest.raises(produce.VisualQualityError,match='证据'):
                produce.review_complete_argument(cues,[dict(start=0,end=0)],'林园','test',Path(tmp),'')


def test_reported_transcript_issue_overrides_positive_editor_flags():
    cues=[dict(start=0,end=150,text='这是一个观点，但关键名词无法理解，这是本段结论。')]
    reply=dict(standalone_opening=True,complete_argument=True,reasoning_present=True,
        natural_ending=True,requires_audio_review=False,summary='通顺摘要',
        issues=['关键名词无法理解'],opening_quote='这是一个观点',ending_quote='这是本段结论。')
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(produce,'llm',return_value=json.dumps(reply,ensure_ascii=False)):
            import pytest
            with pytest.raises(produce.VisualQualityError,match='歧义'):
                produce.review_complete_argument(cues,[dict(start=0,end=0)],'林园','test',Path(tmp),'')


def test_schema_placeholder_cannot_impersonate_a_review_summary():
    meta = complete_meta()
    meta['editorial_review']['summary'] = '主题、理由和结论'
    assert '占位文案' in policy.metadata_error(meta)


def test_confirmed_asr_corruption_is_rejected_before_editorial_review():
    assert 'ASR污染' in policy.transcript_integrity_error('你买片公司万丈深渊')
    assert 'ASR污染' in policy.transcript_integrity_error(
        '我总感觉到现在不在半山药\n也在办三药以上')
    assert 'ASR污染' in policy.transcript_integrity_error(
        '当然它也会受到多体但是我们看他的比如说它会很快恢复')
    assert 'ASR污染' in policy.transcript_integrity_error(
        '非常谢谢谢谢林园先生参与直播，接下来我们具体展开来讲')
    assert 'ASR污染' in policy.transcript_integrity_error('呃刘源先生您看好哪些领域')
    assert 'ASR污染' in policy.transcript_integrity_error('最后都能够这个平安化起')
    assert 'ASR污染' in policy.transcript_integrity_error(
        '经常做投资我也是个大部分万象前朝的那个投资总监')
    assert 'ASR污染' in policy.transcript_integrity_error('钱甚至还要倾家账')
    assert 'ASR污染' in policy.transcript_integrity_error('白度趋势但这个也有背景')
    assert 'ASR污染' in policy.transcript_integrity_error('这就是资本是逐利的我能挣钱我不会拉着你的')
    assert 'ASR污染' in policy.transcript_integrity_error('你跟着里边肯定能赚钱不见得')
    assert 'ASR污染' in policy.transcript_integrity_error('那他总是这样新智生产力')
    assert 'ASR污染' in policy.transcript_integrity_error('投资医药也是投老人口老龄化')
    assert 'ASR污染' in policy.transcript_integrity_error('还是要就是企业的目的是为什么')
    assert 'ASR污染' in policy.transcript_integrity_error('今天是有是投资的好时候')
    assert 'ASR污染' in policy.transcript_integrity_error('有创8%的股息')
    assert 'ASR污染' in policy.transcript_integrity_error('大概在115年16年的时候')
    assert 'ASR污染' in policy.transcript_integrity_error('医药板块是这样的我说细生说的')
    assert 'ASR污染' in policy.transcript_integrity_error('实际上我是十0年前我们都在说这个事啊')
    assert 'ASR污染' in policy.transcript_integrity_error('过去1年中国的老龄化比10年前是严重了很多')
    assert 'ASR污染' in policy.transcript_integrity_error('这就是我我举了个中药的例')
    assert 'ASR污染' in policy.transcript_integrity_error('当时人口死亡年龄大概是667岁')
    assert 'ASR污染' in policy.transcript_integrity_error('那可不得了那我肯定是花大财')
    assert 'ASR污染' in policy.transcript_integrity_error('它这个高低啊不不单纯是看书的股')
    assert 'ASR污染' in policy.transcript_integrity_error('你这个股司不起来哪有钱去消费')
    assert 'ASR污染' in policy.transcript_integrity_error('7块多是倒着来到15块牛市启动')
    assert 'ASR污染' in policy.transcript_integrity_error('今天来的不是笨难')
    assert 'ASR污染' in policy.transcript_integrity_error('你提戚过这怎么涨了这么高了你还跑虑听这')
    assert 'ASR污染' in policy.transcript_integrity_error('大概的时件不是高位')
    assert 'ASR污染' in policy.transcript_integrity_error('买还是卖还是持有那无非是这三个')
    assert policy.transcript_integrity_error('买好公司长期持有不会错') is None


def test_deploy_refill_requires_an_explicit_source_refresh_flag():
    spec = importlib.util.spec_from_file_location(
        'verify_production', ROOT / 'linyuan/fc/verify_production.py')
    verify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verify)
    assert verify.refill_requested({}) is False
    assert verify.refill_requested({'FC_REFILL': 'false'}) is False
    assert verify.refill_requested({'FC_REFILL': 'true'}) is True
    assert verify.refill_requested({'FC_REFILL': '1'}) is True

    deploy = (ROOT / '.github/workflows/fc-production-deploy.yml').read_text()
    monitor = (ROOT / '.github/workflows/linyuan-monitor.yml').read_text()
    assert "inputs.refill" in deploy
    assert "gh workflow run fc-production-deploy.yml" not in monitor
    inventory = (ROOT / '.github/workflows/linyuan-source-inventory.yml').read_text()
    assert "'林园监控 · 每日抓取'" in inventory
    assert "linyuan/dashboard/data.json" not in deploy


def test_daily_and_explicit_publication_share_the_same_gate():
    spec=importlib.util.spec_from_file_location('editorial_fc',ROOT/'linyuan/fc/index.py')
    fc=importlib.util.module_from_spec(spec);spec.loader.exec_module(fc)
    meta=complete_meta();meta['duration_sec']=20;meta['quality_gate_version']=fc.QUALITY_GATE_VERSION
    assert '不足120秒' in fc.artifact_quality_error(meta)
    assert fc.fresh_six_budget({'date':'2026-09-07','count':0},'ly-fresh-six-0906-05','2026-09-07') is None


def test_long_replacement_quota_preserves_history_and_expires():
    spec=importlib.util.spec_from_file_location('replacement_fc',ROOT/'linyuan/fc/index.py')
    fc=importlib.util.module_from_spec(spec);spec.loader.exec_module(fc)
    daily={'date':'2026-09-06','count':16,'fresh_six':{'count':6}}
    budget=fc.fresh_six_budget(daily,'ly-long-six-0906-03','2026-09-06')
    assert budget['count']==0
    assert daily['count']==16 and daily['fresh_six']['count']==6
    assert fc.fresh_six_budget(daily,'ly-long-six-0906-03','2026-09-07') is None
    assert fc.fresh_six_budget(daily,'unreviewed-daily','2026-09-06') is None


def test_only_exact_reviewed_replacements_can_exclude_hidden_short_history():
    import copy
    spec=importlib.util.spec_from_file_location('replacement_dedup_fc',ROOT/'linyuan/fc/index.py')
    fc=importlib.util.module_from_spec(spec);spec.loader.exec_module(fc)
    hidden=next(iter(fc.HIDDEN_SHORT_SIX_BVIDS))
    state={'published':{'old':{'parts':[{'bvid':hidden},{'bvid':'keep-me'}]}}}
    original=copy.deepcopy(state)
    meta={'fingerprints':{'sha256':'reviewed'}}
    slug='ly-long-six-0906-03'
    assert fc.replacement_comparison_state(state,meta,slug) is state
    with patch.object(fc,'FRESH_SIX_APPROVED',{'reviewed':{'slug':slug}}):
        filtered=fc.replacement_comparison_state(state,meta,slug)
        assert filtered['published']['old']['parts']==[{'bvid':'keep-me'}]
        assert fc.replacement_comparison_state(state,meta,'ly-long-six-0906-04') is state
    assert state==original
