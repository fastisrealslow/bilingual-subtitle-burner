"""Public-day delivery cannot be inferred from submission or a late cron."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'linyuan')]
from daily_publication_audit import public_treatment_day,prior_treatment_bvids,experiment_recovery_needed
from test_linyuan_daily_two_treatment import part,state,clock
from fc.index import OWNER_MID

DAY='2026-11-14'
NOW=datetime(2026,11,15,0,20,tzinfo=__import__('publishing_experiment').CST)


def creator(rows):
    return dict(owner_mid=OWNER_MID,videos=[dict(bvid=p['bvid'],mid=OWNER_MID,state=0,public=True,
        statistics_origin='authenticated-creator-read',pubdate=p['ts'],
        publication_clock_candidates=dict(ptime=p['ts'])) for p in rows])


def test_two_distinct_public_same_day_treatments_pass_with_unchanged_receipts():
    rows=[part(DAY,h) for h in (14,21)];s=state(rows);before=deepcopy(s)
    r=public_treatment_day(s,creator(rows),DAY,NOW)
    assert r['passed'] and r['confirmed_same_day_treatment']==2 and s==before
    assert prior_treatment_bvids(s,DAY)==[p['bvid'] for p in rows]


@pytest.mark.parametrize('change',[
    lambda r:r.update(public=False),
    lambda r:r.update(mid='other'),
    lambda r:r.update(statistics_origin='public-search'),
    lambda r:r.update(pubdate=None,publication_clock_candidates={},ctime=clock(DAY+'T21:05:00')),
    lambda r:r.update(pubdate=clock('2026-11-15T00:01:00'),publication_clock_candidates={}),
    lambda r:r.update(publication_clock_candidates=dict(ptime=clock(DAY+'T21:06:00'))),
    lambda r:r.update(pubdate=clock('2026-11-16T00:00:00')),
])
def test_pending_foreign_unknown_late_and_conflicting_public_clocks_cannot_fill_the_day(change):
    rows=[part(DAY,h) for h in (14,21)];proof=creator(rows);change(proof['videos'][1])
    r=public_treatment_day(state(rows),proof,DAY,NOW)
    assert not r['passed'] and r['confirmed_same_day_treatment']==1


def test_actual_creator_ptime_is_supported_without_using_ctime():
    rows=[part(DAY,h) for h in (14,21)];proof=creator(rows)
    for row in proof['videos']:row.pop('pubdate')
    assert public_treatment_day(state(rows),proof,DAY,NOW)['passed']


def test_a_delayed_midnight_audit_still_fails_yesterdays_zero_treatments():
    today=public_treatment_day(state([]),{},'2026-11-15',NOW)
    closing=public_treatment_day(state([]),{},DAY,NOW)
    assert today['passed'] and today['expected_by_now']==0
    assert not closing['passed'] and closing['expected_by_now']==2
    assert experiment_recovery_needed(dict(experiment=dict(active=True),
        experiment_drafts=dict(passed=True),previous_complete_day_treatment=closing))


def test_post_comparison_policy_uses_the_same_public_day_and_source_file_checks():
    day='2026-11-19';rows=[part(day,h) for h in (14,21)]
    now=NOW.replace(day=20)
    assert public_treatment_day(state(rows),creator(rows),day,now)['passed']
