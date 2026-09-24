from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'linyuan'))
from source_priority import family, observed_priorities


def entry(i, **extra):
    return dict(slug=f'job-{i}',source_url=f'https://www.bilibili.com/video/BV{i:010d}',
                author='访谈频道',ts=999,**extra)


def test_repeated_bad_media_is_deprioritized_but_not_filtered():
    state=dict(dispatched=[entry(i,failed=True,last_error='原始素材短边 360 < 480') for i in range(3)])
    result=observed_priorities(state,1000)
    evidence=result[family(state['dispatched'][0])]
    assert -12 < evidence['adjustment'] < 0
    assert evidence['observed_mothers']==3


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
