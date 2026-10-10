import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import reference_audit as audit


def test_unselected_three_never_treated_as_original_or_repost():
    assert audit.COPYRIGHT_CLASSES[3]=='unselected'
    rows=[dict(verified=True,copyright_raw=3,copyright_class='unselected') for _ in range(10)]
    result=audit.copyright_summary(rows)
    assert result['unselected']==10
    assert result['self_made']==result['repost']==0
    assert not result['over_80_percent_confirmed']
    assert result['complete_classification']
    assert result['self_made_ratio_upper_bound']==0


def test_threshold_is_strictly_over_eighty_with_complete_ten_denominator():
    original=dict(verified=True,copyright_class='self_made')
    unknown=dict(verified=True,copyright_class='unknown')
    assert not audit.copyright_summary([original]*8+[unknown]*2)['over_80_percent_confirmed']
    assert audit.copyright_summary([original]*9+[unknown])['over_80_percent_confirmed']
    assert not audit.copyright_summary([original]*9)['over_80_percent_confirmed']


def test_owner_mismatch_cannot_be_a_verified_reference():
    r=audit.exact_reference('BVwrong',lambda p:dict(owner=dict(mid=42)))
    assert not r['verified']


def test_complete_collections_are_not_claimed_as_complete_account_feed():
    calls=[]
    def api(path):
        calls.append(path)
        if 'seasons_series_list' in path:
            return dict(items_lists=dict(page=dict(total=1),seasons_list=[dict(meta=dict(season_id=123,total=1,name='合集'))]))
        return dict(archives=[dict(bvid='BV123',title='林园',pubdate=100,duration=300)])
    data,report=audit.discover_collections({},api)
    assert report['collection_listing_complete']
    assert not report['account_feed_complete']
    assert data['BV123']['metadata_provenance']=='bilibili_public_collection'


def test_similar_title_catalogue_and_downloaded_reference_do_not_count_as_overlap():
    data=[dict(bvid='BVref',verified=True,title='林园谈医药',pubdate=100)]
    lineage=dict(references=[dict(bvid='BVref',candidate_families=['medicine'])],
                 families=[dict(id='medicine',mirror_urls=['https://example.com/mother'])])
    research=dict(jobs=dict(reference=dict(role='reference',url='https://www.bilibili.com/video/BVref',
        evidence=dict(media_integrity='passed',sha256='a'*64))))
    result=audit.coverage_report(data,research,lineage,{})
    assert result['counts']=={'family_lead_only':1}
    assert not result['rows'][0]['source_media_inspected']
    assert result['exact_argument_covered']==0
    assert not result['more_and_faster_than_reference']


def test_account_feed_window_is_separate_from_collection_listing():
    def getter(path):
        return dict(has_more=False,items=[dict(modules=dict(
            module_author=dict(mid=audit.REFERENCE_MID,pub_ts=100),
            module_dynamic=dict(major=dict(archive=dict(bvid='BVfeed',title='林园新视频')))))])
    data,result=audit.discover_account_window({},getter)
    assert result['account_feed_window_complete'] and result['account_feed_complete']
    assert data['BVfeed']['metadata_provenance']=='bilibili_account_feed'


def test_feed_failure_preserves_catalog_and_does_not_claim_latest_complete():
    def fail(path):raise ValueError('blocked')
    data,result=audit.discover_account_window({'BVold':{'title':'old'}},fail)
    assert data['BVold']['title']=='old' and not result['account_feed_window_complete']
