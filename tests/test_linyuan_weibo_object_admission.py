"""Different repost pages exposed one actual video in the Nov14 four-job batch."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'linyuan/fc'),str(Path(__file__).resolve().parents[1]/'linyuan')]
import index as fc

POSTS=('4859480166105564','4858907761574220','4858837373292024','4858824039598884')


def candidate(post,object_id='1034:4848505496469592',duration=5200):
    return dict(key='weibo_search:'+post,page_url='https://m.weibo.cn/detail/'+post,
        title='林园一个半小时精彩访谈',video_url='',extra=dict(duration=duration,video_object_id=object_id))


def test_actual_four_reposts_reserve_one_worker_and_leave_catalogue_unchanged():
    rows=[candidate(post) for post in POSTS];before=deepcopy(rows)
    assert fc.dedup_by_title(rows)==[rows[0]]
    assert rows==before


def test_distinct_native_videos_with_the_same_title_keep_separate_opportunities():
    rows=[candidate(POSTS[0]),candidate(POSTS[1],'1034:4848505496469593')]
    assert fc.dedup_by_title(rows)==rows


def test_exact_object_prefers_eligible_direct_copy_without_importing_approval():
    bad=candidate(POSTS[0],duration=10000);good=candidate(POSTS[1]);good['video_url']='https://example.test/public.mp4'
    assert fc.dedup_by_title([bad,good])==[good]
    assert 'verified' not in good and 'status' not in good


@pytest.mark.parametrize('object_id',[None,'','1034:not-a-number','4848505496469592',1234,'1034:123'])
def test_unknown_or_malformed_object_ids_cannot_collapse_distinct_pages(object_id):
    rows=[candidate(post,object_id) for post in POSTS[:2]]
    assert fc.dedup_by_title(rows)==rows


@pytest.mark.parametrize('page',[
    'https://example.test/source/', 'https://weibo.com.evil.test/',
    'https://user:password@m.weibo.cn/detail/',
])
def test_other_hosts_or_authenticated_urls_cannot_claim_native_identity(page):
    rows=[candidate(post) for post in POSTS[:2]]
    for i,row in enumerate(rows):row['page_url']=page+str(i)
    assert fc.dedup_by_title(rows)==rows


def test_json_serialized_crawler_metadata_is_supported_and_bad_metadata_stays_distinct():
    rows=[candidate(post) for post in POSTS[:2]]
    rows[1]['extra']=json.dumps(rows[1]['extra'])
    assert len(fc.dedup_by_title(rows))==1
    rows[1]['extra']='{'
    assert len(fc.dedup_by_title(rows))==2


def test_admission_collapses_only_eligible_native_copies_and_does_not_reject_source(monkeypatch):
    monkeypatch.setattr(fc,'MIN_DUR',20)
    rows=[]
    for post in POSTS:
        c=candidate(post,duration=80)
        rows.append(dict(id=c['key'],url=c['page_url'],source='weibo_search',title=c['title'],
            extra={**c['extra'],'has_video':True}))
    rows.append(dict(id='distinct80',url='https://www.bilibili.com/video/BVseparate80',
        source='bilibili_search',title='林园谈投资风险',extra=dict(duration=80)))
    state=dict(dispatched=[],published={},rejected=[]);before=deepcopy((rows,state))
    audit=fc.source_admission_audit(rows,state)
    assert audit['candidate_count']==2 and audit['excluded']['duplicate_source']==3
    assert all(r['duration_sec']==80 for r in audit['candidates'])
    assert (rows,state)==before
