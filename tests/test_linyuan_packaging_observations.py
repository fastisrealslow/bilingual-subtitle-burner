"""No lifetime views as same-age views, no release-date cohort guessing."""
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from collect_stats import packaging_observations


def test_receipt_policy_not_publish_date_defines_the_actual_cohort():
    now=10_000_000
    rows=[dict(bvid=b,pubdate=now-25*3600,play=n,title=b) for b,n in [('new',150),('old-stock',90)]]
    state=dict(published=dict(batch=dict(parts=[dict(bvid='new',click_packaging_policy='readable-copy-20261017'),
                                               dict(bvid='old-stock')])))
    result=packaging_observations(rows,{},state,now)
    by_id={r['bvid']:r for r in result['samples']}
    assert by_id['new']['cohort']=='readable-copy-20261017'
    assert by_id['old-stock']['cohort']=='legacy_or_unlabelled'


def test_late_measurement_is_never_backfilled_into_an_earlier_age_window():
    now=10_000_000
    item=dict(bvid='a',pubdate=now-74*3600,play=500,title='a')
    result=packaging_observations([item],{},{},now)
    assert len(result['samples'])==1 and result['samples'][0]['window']==72
    assert next(g for g in result['groups'] if g['window']==24)['median'] is None


def test_first_observation_is_not_overwritten_by_later_lifetime_growth():
    now=10_000_000;item=dict(bvid='a',pubdate=now-25*3600,play=100,title='a')
    first=packaging_observations([item],{},{},now)
    second=packaging_observations([{**item,'play':200}],dict(packaging_observations=first),{},now+6*3600)
    assert second['samples']==first['samples']
    assert 'Not exact 24h/72h counts' in second['scope']


def test_missing_or_empty_collection_keeps_existing_observations():
    now=10_000_000
    first=packaging_observations([dict(bvid='a',pubdate=now-25*3600,play=0)],{},{},now)
    assert packaging_observations([],dict(packaging_observations=first),{},now)['samples']==first['samples']
    assert first['groups'][0]['median']==0
