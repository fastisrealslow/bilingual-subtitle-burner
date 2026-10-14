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
    assert '2020-12-14（原片标注，尚未独立核实）' in desc
    assert '00:01:10–00:02:52' in desc and '2026-10-13' not in desc
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
