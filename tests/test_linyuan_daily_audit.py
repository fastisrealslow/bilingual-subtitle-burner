from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from daily_publication_audit import BEIJING,daily_receipts,opening_stock_check


def part(bvid,sha,when):
    return dict(status='published',bvid=bvid,ts=datetime.fromisoformat(when).replace(tzinfo=BEIJING).timestamp(),
                fingerprints=dict(sha256=sha))


def test_counter_cannot_substitute_for_actual_receipts():
    state=dict(daily_publish=dict(date='2026-09-13',count=4),published={})
    report=daily_receipts(state,datetime(2026,9,13,23,50,tzinfo=BEIJING))
    assert report['expected_by_now']==4 and report['unique_verified_file_count']==0
    assert report['receipt_check_passed'] is False


def test_after_midnight_makeup_counts_only_actual_day_and_deduplicates_receipts():
    p=part('BVnew','sha-new','2026-09-14T00:55:00')
    state=dict(published={'batch':dict(parts=[p]),'mirror':dict(parts=[p]),
        'yesterday':dict(parts=[part('BVold','sha-old','2026-09-13T21:00:00')])})
    report=daily_receipts(state,datetime(2026,9,14,10,20,tzinfo=BEIJING))
    assert report['submitted_count']==report['unique_verified_file_count']==1
    assert report['receipts'][0]['bvid']=='BVnew'
    assert report['receipt_check_passed'] is True


def test_two_bvids_of_same_file_cannot_fill_two_daily_slots():
    state=dict(published={'batch':dict(parts=[part('BV1','same','2026-09-13T10:00:00'),
                                             part('BV2','same','2026-09-13T14:00:00')])})
    report=daily_receipts(state,datetime(2026,9,13,14,20,tzinfo=BEIJING))
    assert report['submitted_count']==2 and report['unique_verified_file_count']==1
    assert report['duplicate_files'][0]['duplicates']=='BV1'
    assert report['receipt_check_passed'] is False


def test_missing_hash_and_excess_daily_uploads_fail_closed():
    rows=[part(f'BV{i}',f'sha{i}','2026-09-13T21:00:00') for i in range(5)]
    for count in (4,5):
        selected=rows[:count]
        if count==4:selected[0]={**selected[0],'fingerprints':{}}
        report=daily_receipts(dict(published={'batch':dict(parts=selected)}),datetime(2026,9,13,23,50,tzinfo=BEIJING))
        assert report['receipt_check_passed'] is False


def test_opening_stock_cannot_count_running_jobs_or_stale_inspections():
    now=datetime(2026,9,14,9,30,tzinfo=BEIJING)
    receipt=dict(unique_verified_file_count=0)
    assert not opening_stock_check(receipt,dict(in_flight_placeholders=12,inventory_fresh=True),now)['passed']
    stock=dict(verified_live=4,verified_landscape=1,inventory_fresh=True)
    assert opening_stock_check(receipt,stock,now)['passed']
    assert not opening_stock_check(receipt,{**stock,'inventory_fresh':False},now)['passed']
    assert not opening_stock_check(receipt,{**stock,'verified_landscape':0},now)['passed']


def test_midnight_makeup_reduces_real_remaining_stock_requirement():
    now=datetime(2026,9,14,9,30,tzinfo=BEIJING)
    receipt=dict(unique_verified_file_count=2)
    stock=dict(verified_live=2,verified_landscape=1,inventory_fresh=True)
    report=opening_stock_check(receipt,stock,now)
    assert report['required_live']==2 and report['passed']
    assert opening_stock_check(receipt,stock,now.replace(hour=10)) is None
