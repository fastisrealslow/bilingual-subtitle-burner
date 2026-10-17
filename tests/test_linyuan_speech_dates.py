import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
from audience_copy import speech_date_evidence, dated_description
from source_priority import freshness_evidence, recording_recency_rank

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc).timestamp()


def test_upload_date_is_never_the_date_of_the_speech():
    assert speech_date_evidence({'publish_time': '2026-10-13'}, NOW)['date'] is None
    desc = dated_description('林园：我不加杠杆', {'segments': [{'start': 70.52, 'end': 172.44}]},
                            {'title': '林园新浪财经采访2020.12.14', 'publish_time': '2026-10-13'})
    assert desc=='林园：我不加杠杆'
    assert '原视频位置' not in desc and '暂未核实' not in desc
    assert dated_description(desc, {'segments': [{'start': 70.52, 'end': 172.44}]},
                             {'title': '林园新浪财经采访2020.12.14'}) == desc


def test_bad_future_and_conflicting_dates_stay_unknown():
    for title in ['2026.02.30', '2027年1月1日', '2020-12-14和2021-01-01', '10月14日', '']:
        assert speech_date_evidence({'title': title}, NOW)['date'] is None
    assert speech_date_evidence({'extra': 'bad json', 'title': '林园2020年12月14日'}, NOW)['date'] == '2020-12-14'


def test_parent_date_survives_generated_episode_title_and_dispatch():
    c = {'title': '林园 P1 studio', 'extra': {'collection_title': '2026年10月12日林园访谈'}}
    proof = speech_date_evidence(c, NOW)
    assert proof['date'] == '2026-10-12' and proof['verified'] is False
    assert speech_date_evidence({'title': '林园 P1 studio', 'speech_date_context': proof}, NOW) == proof
    assert recording_recency_rank(c, NOW) == 0
    assert recording_recency_rank({'title': '未知时间', 'publish_time': '2026-10-14'}, NOW) == 1


def test_reuploaded_historical_title_cannot_get_a_news_bonus():
    c = {'title': '林园新浪财经采访2020.12.14', 'publish_time': '2026-10-13T01:00:00Z'}
    assert freshness_evidence(c, NOW)['score'] == 0
    assert recording_recency_rank(c, NOW) == 2


def test_recent_speech_claim_after_the_original_upload_is_not_recent_evidence():
    c = {'title': '2026年10月14日林园最新访谈', 'publish_time': '2024-10-14T12:00:00Z'}
    assert speech_date_evidence(c, NOW)['basis'] == 'date_after_source_upload'
    assert recording_recency_rank(c, NOW) == 1


def test_only_explicit_primary_source_verification_can_display_a_date():
    context=dict(date='2020-12-14',verified=True,basis='primary_source_verified',
                 evidence_url='https://www.yicai.com/news/example.html')
    entry=dict(speech_date_context=context,publish_time='2026-10-13')
    assert speech_date_evidence(entry,NOW)['verified'] is True
    value=dated_description('林园：我不加杠杆',{},entry)
    assert value=='林园：我不加杠杆\n\n发言时间：2020年12月14日。'
    assert dated_description(value,{},entry)==value
    for update in [dict(verified=False),dict(verified='true'),dict(evidence_url=''),
                   dict(basis='source_metadata_unverified')]:
        assert dated_description('林园：我不加杠杆',{},dict(speech_date_context={**context,**update}))=='林园：我不加杠杆'


def test_old_work_log_footer_is_removed_but_source_credit_and_disclosure_survive():
    original=('林园：我不加杠杆\n来源：第一财经\nAI辅助字幕\n版权说明'
              '\n\n【原始讲话信息】\n讲话时间：暂未核实；不是本次上传时间。'
              '\n原视频位置：00:01:10–00:02:52\n请结合当时背景理解，不代表对当前行情的判断。')
    result=dated_description(original,{})
    assert result=='林园：我不加杠杆\n来源：第一财经\nAI辅助字幕\n版权说明'
    assert dated_description(result,{})==result
