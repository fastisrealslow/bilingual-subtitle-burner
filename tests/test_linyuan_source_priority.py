from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'linyuan'))
from source_priority import family, observed_priorities, publisher_priority
from source_priority import freshness_evidence


def entry(i, **extra):
    return dict(slug=f'job-{i}',source_url=f'https://www.bilibili.com/video/BV{i:010d}',
                author='访谈频道',ts=999,**extra)


def test_repeated_bad_media_is_deprioritized_but_not_filtered():
    state=dict(dispatched=[entry(i,failed=True,last_error='原始素材短边 360 < 480') for i in range(3)])
    result=observed_priorities(state,1000)
    evidence=result[family(state['dispatched'][0])]
    assert -12 < evidence['adjustment'] < 0
    assert evidence['observed_mothers']==3


def test_static_portrait_failures_inform_dispatch_without_punishing_retries_or_receipts():
    # Actual normal run 40081085173 rejected all three excerpts of
    # BV1LYjC6tEGi: each motion proof has moving_by_third=[0,0,0].
    reason='标题前动态窗口缺少持续局部动作：疑似照片/背景板，不消耗标题与字幕生成预算'
    first=entry(1,failed=True,last_error=reason)
    retries=[{**first,'slug':f'retry-{i}','ts':1000+i} for i in range(3)]
    one=observed_priorities(dict(dispatched=[first,*retries]),1010)[family(first)]
    assert one['observed_mothers']==1 and one['adjustment']==0
    # Three different mother URLs model repeated outcomes from one publisher;
    # they are synthetic dispatch records, not new observed media evidence.
    mothers=[entry(i,failed=True,last_error=reason) for i in range(1,4)]
    negative=observed_priorities(dict(dispatched=mothers),1010)[family(first)]
    assert negative['quality_rejected_mothers']==3 and -12<negative['adjustment']<0
    published={e['slug']:dict(bvid='BVrealreceipt') for e in mothers}
    positive=observed_priorities(dict(dispatched=mothers,published=published),1010)[family(first)]
    assert positive['quality_rejected_mothers']==0 and positive['adjustment']>0


def test_static_motion_service_failures_do_not_count_against_media():
    rows=[entry(i,failed=True,last_error='真人动态验证不可用：HTTP Error 503') for i in range(3)]
    assert observed_priorities(dict(dispatched=rows),1010)=={}


def test_service_errors_retries_and_small_samples_do_not_penalize_author():
    bad=entry(1,failed=True,last_error='人物不一致或无法确认')
    transient=[entry(i,failed=True,last_error='人物 VLM 校验不可用：HTTP Error 402') for i in range(2,7)]
    result=observed_priorities(dict(dispatched=[bad,bad,bad,*transient]),1000)
    evidence=result[family(bad)]
    assert evidence['observed_mothers']==1 and evidence['adjustment']==0


def test_published_receipts_outweigh_stale_failure_flag_and_old_history_expires():
    entries=[entry(i,failed=True,last_error='来源取景无法保留完整人脸') for i in range(3)]
    state=dict(dispatched=entries,published={e['slug']:dict(bvid='BVdelivered') for e in entries})
    evidence=observed_priorities(state,1000)[family(entries[0])]
    assert evidence['delivered_mothers']==3 and evidence['adjustment']>0
    assert observed_priorities(state,1000+31*86400)=={}
    assert family(dict(author='访谈频道',page_url='https://m.bilibili.com/video/x'))==family(entries[0])
    assert family(dict(author='访谈频道',page_url='https://www.163.com/video/x'))!=family(entries[0])


def test_placeholder_authors_are_neutral_and_weibo_hosts_share_a_family():
    for name in ('账号已注销','网易视频','好看视频'):
        assert family(dict(author=name,page_url='https://www.bilibili.com/video/x')) is None
    assert family(dict(author='访谈频道',page_url='https://m.weibo.cn/detail/1')) == family(
        dict(author='访谈频道',page_url='https://weibo.com/1'))


def test_later_retry_cannot_erase_an_earlier_receipt_for_the_same_mother():
    originals=[entry(i) for i in range(3)]
    retries=[{**e,'slug':e['slug']+'-retry','ts':1000,'failed':True,
              'last_error':'来源取景无法保留完整人脸'} for e in originals]
    state=dict(dispatched=originals+retries,
               published={e['slug']:dict(bvid='BVdelivered') for e in originals})
    counts=observed_priorities(state,1001)[family(originals[0])]
    assert counts['delivered_mothers']==3 and counts['quality_rejected_mothers']==0


def test_platform_and_self_reported_primary_claims_are_not_provenance():
    for source in ('xueqiu_video','tencent_video','bilibili_api','bilibili_space','weibo_video'):
        assert publisher_priority(dict(source=source,extra={'origin_role':'official_publisher'},
            page_url='https://www.bilibili.com/video/BVunverified'))==10
    assert publisher_priority(dict(page_url='https://www.yicai.com/video/103329354.html',
        extra='{"origin_role":"official_publisher"}'))==30
    assert publisher_priority(dict(page_url='https://www.yicai.com/video/99999999.html',
        extra={'origin_role':'official_publisher'}))==10
    assert publisher_priority(dict(extra='bad json'))==10


def test_yesterday_upload_of_old_interview_does_not_get_latest_recording_bonus():
    from datetime import datetime, timezone
    now=datetime(2026,10,12,4,tzinfo=timezone.utc).timestamp()
    row=dict(publish_time='2026-10-11T09:20:09+00:00',
             extra={'content_date_hint':'2025-10-23','content_date_hint_provenance':'uploader_title_unverified'})
    proof=freshness_evidence(row,now)
    assert proof['score']==0 and proof['content_date_verified'] is False
    assert proof['basis']=='historical_context_hint_not_new_recording'
    assert freshness_evidence({**row,'extra':{}},now)['score']==15


def test_future_missing_and_invalid_clocks_cannot_get_highest_recency_rank():
    from datetime import datetime, timezone
    now=datetime(2026,10,12,tzinfo=timezone.utc).timestamp()
    for timestamp in ('2027-01-01T00:00:00Z','',None,'invalid'):
        assert freshness_evidence(dict(publish_time=timestamp),now)['score']==0
    assert freshness_evidence(dict(publish_time='2026-10-11T23:00:00Z',extra='broken'),now)['score']==15
