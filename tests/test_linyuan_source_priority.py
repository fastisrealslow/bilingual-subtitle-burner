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
