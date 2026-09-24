"""Protect reporting denominators and distinguish retrieval from acceptance."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_linyuan_source_yield import grouped


def test_cached_media_and_missing_reports_are_not_dropped():
    rows = [dict(sample=dict(id=1, source_url='https://www.bilibili.com/video/BV1'),
                 status='rejected', source_sha256='a'*64, steps={'src': {'outcome': 'skipped'}}),
            dict(sample=dict(id=2, source_url='https://www.bilibili.com/video/BV2'),
                 status='unresolved', stage='missing-report')]
    result = grouped(rows, [])[0]
    assert (result['total'], result['downloaded'], result['passed'], result['unresolved']) == (2, 1, 0, 1)
    assert result['percent'] == 0


def test_same_uploader_label_does_not_merge_platforms_or_retry_reasons():
    rows = [dict(sample=dict(id=i, source_url=url, author='同名作者'), status='rejected',
                 batch={'rejected': [{'reason': '字幕残字'}, {'reason': '字幕残字'}]})
            for i, url in enumerate(['https://m.weibo.cn/detail/1', 'https://www.bilibili.com/video/BV1'])]
    result = grouped(rows, [], by_author=True)
    assert len(result) == 2
    assert all(r['reasons'] == {'captions': 1} for r in result)


def test_one_pass_is_not_a_100_percent_rate_when_another_input_is_pending():
    samples = [dict(sample=dict(id=i, source_url='https://m.weibo.cn/detail/'+str(i)), status=status)
               for i, status in enumerate(['passed', 'unresolved'])]
    row = grouped(samples, [])[0]
    assert row['total'] == 2 and row['percent'] == 50.0
